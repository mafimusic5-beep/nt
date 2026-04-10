import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.ui.keyboards import admin_code_detail_keyboard, admin_codes_keyboard, admin_menu_keyboard
from src.bot.utils.access import is_admin
from src.bot.utils.formatters import format_dt

logger = logging.getLogger(__name__)

router = Router(name="admin_codes")
client = BackendClient()
PAGE_SIZE = 10


class CodeSearchState(StatesGroup):
    waiting_query = State()


def _admin(user_id: int | None) -> bool:
    return bool(user_id) and is_admin(user_id)


def _hash_short(value: str | None) -> str:
    if not value:
        return "—"
    return f"{value[:12]}…"


async def _admin_request(method: str, path: str, *, params: dict | None = None) -> dict:
    return await client._request(method, path, params=params, headers={"X-Admin-Api-Key": client.admin_api_key})


def _list_text(payload: dict, query: str | None = None) -> str:
    items = payload.get("items", [])
    total = payload.get("total", 0)
    offset = payload.get("offset", 0)
    limit = payload.get("limit", PAGE_SIZE)
    page = offset // limit + 1 if limit else 1
    pages = max((total + limit - 1) // limit, 1) if limit else 1
    title = f"Поиск ключей: <code>{escape(query)}</code>" if query else "Все ключи"
    if not items:
        return f"{title}\n\nНичего не найдено."
    lines = [title, f"Страница {page}/{pages} • Всего: {total}", ""]
    for item in items:
        lines.append(
            f"#{item['id']} • tg={item.get('telegram_id') or '—'} • sub={item.get('subscription_id') or '—'}\n"
            f"Статус: {item.get('status', '—')} • hash: <code>{_hash_short(item.get('code_hash'))}</code>\n"
            f"Создан: {format_dt(item.get('created_at'))}"
        )
        lines.append("")
    lines.append("Нажми на ключ ниже.")
    return "\n".join(lines).strip()


def _detail_text(code: dict) -> str:
    return "\n".join(
        [
            f"Ключ #{code['id']}",
            f"Telegram ID: <code>{code.get('telegram_id') or '—'}</code>",
            f"User ID: <code>{code.get('user_id') or '—'}</code>",
            f"Subscription ID: <code>{code.get('subscription_id') or '—'}</code>",
            f"Статус ключа: <b>{escape(code.get('status', '—'))}</b>",
            f"Хэш: <code>{escape(code.get('code_hash', '—'))}</code>",
            f"Создан: {format_dt(code.get('created_at'))}",
            f"Первое использование: {format_dt(code.get('first_redeemed_at'))}",
            f"Подписка: {escape(code.get('subscription_status') or '—')}",
            f"Подписка до: {format_dt(code.get('subscription_ends_at'))}",
            "",
            "Исходный текст ключа не восстановить: в базе хранится только хэш.",
        ]
    )


async def _show_list(target, state: FSMContext, page: int, query: str | None = None, edit: bool = True) -> None:
    offset = max(page, 0) * PAGE_SIZE
    if query:
        await state.set_state(CodeSearchState.waiting_query)
        await state.update_data(code_query=query)
        payload = await _admin_request("GET", "/api/v1/admin/codes/search", params={"query": query, "limit": PAGE_SIZE, "offset": offset})
        mode = "s"
    else:
        await state.clear()
        payload = await _admin_request("GET", "/api/v1/admin/codes", params={"limit": PAGE_SIZE, "offset": offset})
        mode = "l"
    text = _list_text(payload, query=query)
    markup = admin_codes_keyboard(payload.get("items", []), total=payload.get("total", 0), page=max(page, 0), page_size=PAGE_SIZE, mode=mode)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(CodeSearchState.waiting_query)
async def code_search_input(message: Message, state: FSMContext) -> None:
    if not _admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пустой запрос.")
        return
    if query.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        await message.answer("Поиск отменен.", reply_markup=admin_menu_keyboard())
        return
    try:
        await _show_list(message, state, page=0, query=query, edit=False)
    except BackendClientError as exc:
        logger.warning("admin code search failed: %s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.callback_query(F.data == "admin_codes_search")
async def code_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await state.set_state(CodeSearchState.waiting_query)
    await state.update_data(code_query="")
    await callback.message.edit_text(
        "Введи запрос для поиска ключей.\nМожно искать по ID, telegram_id, subscription_id, статусу или куску хэша.\n\nДля отмены отправь: <code>отмена</code>",
        parse_mode="HTML",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_codes")
async def code_list_open(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    try:
        await _show_list(callback.message, state, page=0)
        await callback.answer()
    except BackendClientError as exc:
        await callback.message.edit_text(f"Ошибка backend: {exc.detail}", reply_markup=admin_menu_keyboard())
        await callback.answer()


@router.callback_query(F.data.startswith("admin_codes_page:"))
async def code_list_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    _, _, mode, page_raw = (callback.data or "").split(":")
    query = None
    if mode == "s":
        query = ((await state.get_data()).get("code_query") or "").strip() or None
    await _show_list(callback.message, state, page=int(page_raw), query=query)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_code_open:"))
async def code_open(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    _, _, code_id_raw, mode, page_raw = (callback.data or "").split(":")
    code = await _admin_request("GET", f"/api/v1/admin/codes/{int(code_id_raw)}")
    await callback.message.edit_text(
        _detail_text(code),
        parse_mode="HTML",
        reply_markup=admin_code_detail_keyboard(int(code_id_raw), code.get("status", ""), mode=mode, page=int(page_raw)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_code_action:"))
async def code_action(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    _, _, action, code_id_raw, mode, page_raw = (callback.data or "").split(":")
    code_id = int(code_id_raw)
    page = int(page_raw)
    if action == "delete":
        await _admin_request("DELETE", f"/api/v1/admin/codes/{code_id}")
        query = None
        if mode == "s":
            query = ((await state.get_data()).get("code_query") or "").strip() or None
        await _show_list(callback.message, state, page=page, query=query)
        await callback.answer("Ключ удалён.")
        return
    path = f"/api/v1/admin/codes/{code_id}/revoke" if action == "revoke" else f"/api/v1/admin/codes/{code_id}/activate"
    code = await _admin_request("POST", path)
    prefix = "Ключ деактивирован.\n\n" if action == "revoke" else "Ключ активирован.\n\n"
    await callback.message.edit_text(
        prefix + _detail_text(code),
        parse_mode="HTML",
        reply_markup=admin_code_detail_keyboard(code_id, code.get("status", ""), mode=mode, page=page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_codes_back:"))
async def code_back(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    _, _, mode, page_raw = (callback.data or "").split(":")
    query = None
    if mode == "s":
        query = ((await state.get_data()).get("code_query") or "").strip() or None
    await _show_list(callback.message, state, page=int(page_raw), query=query)
    await callback.answer()
