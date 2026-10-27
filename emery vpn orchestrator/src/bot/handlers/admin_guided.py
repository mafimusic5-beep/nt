"""Guided, private-only VPS admin controls. Do not store root passwords in FSM."""
from __future__ import annotations

import ipaddress

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.handlers.server_setup import _auto_location_title
from src.bot.utils.access import is_admin
from src.bot.utils.geoip import detect_node_location
from src.bot.ui.keyboards import admin_menu_keyboard

router = Router(name="admin_guided")
client = BackendClient()
_cleanup_in_progress = set()


class ServerWizard(StatesGroup):
    waiting_ip = State()
    waiting_password = State()


def _admin(user, chat) -> bool:
    return bool(user) and is_admin(user.id) and chat.type == ChatType.PRIVATE


def _keyboard(rows):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.button(text="← Админ-панель", callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(F.data == "admin_help")
async def help_panel(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    await callback.message.edit_text(
        "👑 Управление Skryon\n\n"
        "📦 Релизы Windows: загрузить, проверить, опубликовать.\n"
        "➕ Новый VPS: бот спросит IP и пароль отдельно, сам найдёт город.\n"
        "🖥 Серверы: действующие узлы, отключение с подтверждением.\n"
        "🧹 Очистить слоты: только после отдельного подтверждения.\n"
        "🔑 Ключи: поиск, управление доступом и подписками.\n"
        "📊 Статистика, ёмкость и проблемы: отдельные кнопки.\n\n"
        "Важное: публикация нового релиза не означает принудительную "
        "блокировку старых клиентов. Она включается только после проверки.",
        reply_markup=_keyboard([("🖥 Серверы", "admin_node_tools"), 
                                ("📦 Релизы Windows", "admin_releases")]))
    await callback.answer()


@router.callback_query(F.data == "admin_vps_add")
async def ask_ip(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    await state.set_state(ServerWizard.waiting_ip)
    await callback.message.edit_text(
        "🖥 Добавить VPS\n\nОтправьте публичный IPv4-адрес нового сервера.\n"
        "Для отмены нажмите кнопку.",
        reply_markup=_keyboard([("✖ Отменить", "admin_cancel_wizard")]))
    await callback.answer()


@router.message(ServerWizard.waiting_ip)
async def receive_ip(message: Message, state: FSMContext) -> None:
    if not _admin(message.from_user, message.chat):
        await state.clear(); return
    try:
        ip = ipaddress.ip_address((message.text or "").strip())
        if ip.version != 4 or not ip.is_global:
            raise ValueError("not a public IP")
    except ValueError:
        await message.answer("Нужен действительный публичный IPv4-адрес.")
        return
    await state.update_data(ip=str(ip))
    await state.set_state(ServerWizard.waiting_password)
    await message.answer(
        "🔒 Теперь отправьте пароль root одним сообщением. "
        "Бот попробует сразу удалить сообщение с паролем. "
        "Безопаснее предварительно настроить SSH-ключи, а пароль поменять после загрузки.",
        reply_markup=_keyboard([("✖ Отменить", "admin_cancel_wizard")]))


@router.message(ServerWizard.waiting_password)
async def receive_password(message: Message, state: FSMContext) -> None:
    if not _admin(message.from_user, message.chat):
        await state.clear(); return
    password = message.text or ""
    # Password must not end up in persistent FSM storage or logs.
    try: await message.delete()
    except Exception: pass
    saved = await state.get_data()
    await state.clear()
    if not (8 <= len(password) <= 200) or not saved.get("ip"):
        await message.answer("Некорректные данные. Добавление отменено.")
        return
    ip = saved["ip"]
    location = await detect_node_location(ip)
    name = _auto_location_title(location)
    region = str((location or {}).get("region_code") or "").strip().lower()[:64]
    if not name or not region:
        await message.answer("Не удалось безопасно определить город VPS. Сервер не добавлен.")
        return
    await message.answer("⚙️ Настраиваю VPS " + ip + "…")
    payload = {
        "name": name, "region_code": region, "endpoint": ip,
        "ssh_user": "root", "ssh_password": password, "capacity_clients": 15,
        "bandwidth_limit_mbps": 1000, "per_device_speed_limit_mbps": 50,
        "device_gate_host": "", "device_gate_port": 24443,
        "device_gate_server_name": "", "device_gate_spki_sha256": "",
    }
    try:
        result = await client.admin_bootstrap_node(payload)
        row = result.get("node") or {}
        await message.answer(
            f"✅ VPS #{row.get('id')} добавлен в пул.\n"
            f"{row.get('name')}\nIP: {row.get('endpoint')}\n"
            f"Статус: {row.get('status')}/{row.get('health_status')}",
            reply_markup=admin_menu_keyboard())
    except BackendClientError as exc:
        await message.answer("⛔ Backend не добавил VPS: " + str(exc.detail)[:200],
            reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "admin_cancel_wizard")
async def cancel_wizard(callback: CallbackQuery, state: FSMContext) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    await state.clear()
    await callback.message.edit_text("Отменено.", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_node_tools")
async def node_tools(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    await callback.message.edit_text(
        "🖥 Управление VPN-узлами\n\nВыберите действие. "
        "Разрушительные действия требуют отдельного подтверждения.",
        reply_markup=_keyboard([
            ("➕ Добавить VPS", "admin_vps_add"),
            ("📋 Список серверов", "admin_nodes"),
            ("🚫 Отключить сервер", "admin_node_action:remove"),
            ("🧹 Очистить устаревшие слоты", "admin_node_action:clear"),
            ("📊 Ёмкость", "admin_capacity"),
        ]))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_node_action:"))
async def action_node_list(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    action = callback.data.rsplit(":", 1)[1]
    if action not in ("remove", "clear"):
        await callback.answer("Неизвестное действие.", show_alert=True); return
    try: nodes = await client.admin_nodes()
    except BackendClientError as exc:
        await callback.answer(str(exc.detail)[:170], show_alert=True); return
    buttons = []
    for row in nodes[:20]:
        id_ = row.get("id")
        if not isinstance(id_, int) or id_ <= 0: continue
        name = str(row.get("name") or "Узел")[:24]
        buttons.append((f"#{id_} {name}", f"admin_node_confirm:{action}:{id_}"))
    await callback.message.edit_text(
        ("🧹 Очистить слоты" if action == "clear" else "🚫 Отключить узел") +
        "\n\nВыберите сервер. Действие ещё не выполнено.",
        reply_markup=_keyboard(buttons or [("Список пуст", "admin_node_tools")]))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_node_confirm:"))
async def confirm_node(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    try:
        _, _, action, idraw = callback.data.split(":")
        node_id = int(idraw)
        if action not in ("remove", "clear") or node_id <= 0: raise ValueError()
    except (ValueError, TypeError):
        await callback.answer("Неверное действие.", show_alert=True); return
    desc = ("удалит узел из пула" if action == "remove" else
            "может разорвать VPN-подключения пользователей этого узла")
    await callback.message.edit_text(
        f"⚠️ Подтвердите действие для узла #{node_id}.\n\nОперация {desc}.",
        reply_markup=_keyboard([
            ("✅ Подтверждаю", f"admin_node_execute:{action}:{node_id}"),
            ("Отмена", "admin_node_tools"),
        ]))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_node_execute:"))
async def execute_node(callback: CallbackQuery) -> None:
    if not _admin(callback.from_user, callback.message.chat):
        await callback.answer("Доступ запрещён.", show_alert=True); return
    try:
        _, _, action, raw = callback.data.split(":")
        node_id = int(raw)
        if action not in ("remove", "clear") or node_id <= 0: raise ValueError()
    except (ValueError, TypeError):
        await callback.answer("Неверное действие.", show_alert=True); return
    if node_id in _cleanup_in_progress:
        await callback.answer("Операция уже выполняется.", show_alert=True); return
    _cleanup_in_progress.add(node_id)
    await callback.answer("Выполняю…")
    try:
        if action == "remove":
            await client.admin_disable_node(node_id)
            msg = f"✅ Узел #{node_id} отключён."
        else:
            result = await client._request("POST",
                f"/api/v1/admin/nodes/{node_id}/assignments/clear",
                headers={"X-Admin-Api-Key": client.admin_api_key}, timeout_seconds=900.0)
            msg = (f"🧹 Очистка узла #{node_id}: удалено {result.get('cleared')}, "
                   f"ошибок {result.get('failed')}.")
        await callback.message.edit_text(msg, reply_markup=admin_menu_keyboard())
    except BackendClientError as exc:
        await callback.message.edit_text("⛔ Сервер отклонил действие: " +
            str(exc.detail)[:180], reply_markup=admin_menu_keyboard())
    finally:
        _cleanup_in_progress.discard(node_id)
