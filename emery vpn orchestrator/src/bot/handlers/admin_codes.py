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
PAGE_SIZE = 1

class CodeSearchState(StatesGroup):
    waiting_query = State()

def _admin(user_id):
    return bool(user_id) and is_admin(user_id)

def _hash_short(value):
    return f"{value[:10]}..." if value else "-"

async def _admin_request(method, path, params=None):
    return await client._request(method, path, params=params, headers={"X-Admin-Api-Key": client.admin_api_key})

def _list_text(payload, query=None):
    items = payload.get("items", [])
    total = payload.get("total", 0)
    offset = payload.get("offset", 0)
    limit = payload.get("limit", PAGE_SIZE)
    page = offset // limit + 1 if limit else 1
    pages = max((total + limit - 1) // limit, 1) if limit else 1
    title = f"Search: <code>{escape(query)}</code>" if query else "Keys"
    if not items:
        return f"{title}\n\nNothing found."
    item = items[0]
    return "\n".join([
        f"{title} | {page}/{pages}",
        "",
        f"#{item['id']} | {item.get('status', '-')}",
        f"tg: <code>{item.get('telegram_id') or '-'}</code> | sub: <code>{item.get('subscription_id') or '-'}</code>",
        f"hash: <code>{_hash_short(item.get('code_hash'))}</code>",
        f"created: {format_dt(item.get('created_at'))}",
        "",
        "Use arrows or open current key.",
    ])

def _detail_text(code):
    return "\n".join([
        f"Key #{code['id']}",
        f"Telegram ID: <code>{code.get('telegram_id') or '-'}</code>",
        f"User ID: <code>{code.get('user_id') or '-'}</code>",
        f"Subscription ID: <code>{code.get('subscription_id') or '-'}</code>",
        f"Status: <b>{escape(code.get('status', '-'))}</b>",
        f"Hash: <code>{escape(code.get('code_hash', '-'))}</code>",
        f"Created: {format_dt(code.get('created_at'))}",
        f"First redeem: {format_dt(code.get('first_redeemed_at'))}",
        f"Subscription: {escape(code.get('subscription_status') or '-')}",
        f"Ends at: {format_dt(code.get('subscription_ends_at'))}",
        "",
        "Plain key cannot be restored from hash.",
    ])

async def _show_list(target, state: FSMContext, page: int, query=None, edit=True):
    offset = max(page, 0) * PAGE_SIZE
    if query:
        await state.set_state(CodeSearchState.waiting_query)
        await state.update_data(code_query=query)
        payload = await _admin_request("GET", "/api/v1/admin/codes/search", {"query": query, "limit": PAGE_SIZE, "offset": offset})
        mode = "s"
    else:
        await state.clear()
        payload = await _admin_request("GET", "/api/v1/admin/codes", {"limit": PAGE_SIZE, "offset": offset})
        mode = "l"
    text = _list_text(payload, query)
    markup = admin_codes_keyboard(payload.get("items", []), total=payload.get("total", 0), page=max(page, 0), page_size=PAGE_SIZE, mode=mode)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)

@router.message(CodeSearchState.waiting_query)
async def code_search_input(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        await message.answer("Access denied.")
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Empty query.")
        return
    if query.lower() in {"cancel", "/cancel", "отмена"}:
        await state.clear()
        await message.answer("Search canceled.", reply_markup=admin_menu_keyboard())
        return
    try:
        await _show_list(message, state, 0, query, False)
    except BackendClientError as exc:
        logger.warning("admin code search failed: %s", exc.detail)
        await message.answer(f"Backend error: {exc.detail}")

@router.callback_query(F.data == "admin_codes_search")
async def code_search_start(callback: CallbackQuery, state: FSMContext):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(CodeSearchState.waiting_query)
    await state.update_data(code_query="")
    await callback.message.edit_text("Send search query. Use id, telegram id, subscription id, status or hash part. Send cancel to stop.", reply_markup=admin_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_codes")
async def code_list_open(callback: CallbackQuery, state: FSMContext):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    try:
        await _show_list(callback.message, state, 0)
        await callback.answer()
    except BackendClientError as exc:
        await callback.message.edit_text(f"Backend error: {exc.detail}", reply_markup=admin_menu_keyboard())
        await callback.answer()

@router.callback_query(F.data.startswith("admin_codes_page:"))
async def code_list_page(callback: CallbackQuery, state: FSMContext):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _, _, mode, page_raw = (callback.data or "").split(":")
    query = None
    if mode == "s":
        query = ((await state.get_data()).get("code_query") or "").strip() or None
    await _show_list(callback.message, state, int(page_raw), query)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_code_open:"))
async def code_open(callback: CallbackQuery):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _, _, code_id_raw, mode, page_raw = (callback.data or "").split(":")
    code = await _admin_request("GET", f"/api/v1/admin/codes/{int(code_id_raw)}")
    await callback.message.edit_text(_detail_text(code), parse_mode="HTML", reply_markup=admin_code_detail_keyboard(int(code_id_raw), code.get("status", ""), mode=mode, page=int(page_raw)))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_code_action:"))
async def code_action(callback: CallbackQuery, state: FSMContext):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _, _, action, code_id_raw, mode, page_raw = (callback.data or "").split(":")
    code_id = int(code_id_raw)
    page = int(page_raw)
    if action == "delete":
        await _admin_request("DELETE", f"/api/v1/admin/codes/{code_id}")
        query = None
        if mode == "s":
            query = ((await state.get_data()).get("code_query") or "").strip() or None
        await _show_list(callback.message, state, page, query)
        await callback.answer("Deleted.")
        return
    path = f"/api/v1/admin/codes/{code_id}/revoke" if action == "revoke" else f"/api/v1/admin/codes/{code_id}/activate"
    code = await _admin_request("POST", path)
    prefix = "Revoked.\n\n" if action == "revoke" else "Activated.\n\n"
    await callback.message.edit_text(prefix + _detail_text(code), parse_mode="HTML", reply_markup=admin_code_detail_keyboard(code_id, code.get("status", ""), mode=mode, page=page))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_codes_back:"))
async def code_back(callback: CallbackQuery, state: FSMContext):
    if not _admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    _, _, mode, page_raw = (callback.data or "").split(":")
    query = None
    if mode == "s":
        query = ((await state.get_data()).get("code_query") or "").strip() or None
    await _show_list(callback.message, state, int(page_raw), query)
    await callback.answer()
