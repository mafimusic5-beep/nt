from __future__ import annotations

import hashlib

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.api.backend_client import BackendClient, BackendClientError
from src.bot.utils.access import is_admin

router = Router(name="compat_commands")
client = BackendClient()


def _args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _admin(message: Message) -> bool:
    return bool(message.from_user) and is_admin(message.from_user.id)


async def _deny(message: Message) -> bool:
    if _admin(message):
        return False
    await message.answer("Доступ запрещен.")
    return True


@router.message(Command("stats"))
async def stats_command(message: Message) -> None:
    if await _deny(message):
        return
    try:
        stats = await client.admin_stats()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    await message.answer(
        "Статистика:\n"
        f"- Пользователи: {stats.get('users', 0)}\n"
        f"- Подписки: {stats.get('subscriptions', 0)}\n"
        f"- Активные устройства: {stats.get('active_devices', 0)}\n"
        f"- Заказы: {stats.get('orders', 0)}\n"
        f"- Оплаты: {stats.get('payments', 0)}\n"
        f"- Коды: {stats.get('codes', 0)}"
    )


@router.message(Command("nodes", "configs"))
async def nodes_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    try:
        nodes = await client.admin_nodes()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    if not nodes:
        await message.answer("Узлы не найдены.")
        return
    lines = ["Список узлов:"]
    for node in nodes[:30]:
        lines.append(
            f"- #{node.get('id')} {node.get('name')} [{node.get('region_code')}] "
            f"{node.get('status')}/{node.get('health_status')} "
            f"{node.get('current_clients')}/{node.get('capacity_clients')}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("codes"))
async def codes_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    try:
        payload = await client._request(
            "GET",
            "/api/v1/admin/codes",
            params={"limit": 20, "offset": 0},
            headers={"X-Admin-Api-Key": client.admin_api_key},
        )
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not items:
        await message.answer("Кодов пока нет.")
        return
    lines = [f"Коды: {payload.get('total', len(items))}"]
    for item in items:
        code_hash = str(item.get("code_hash") or "")
        lines.append(
            f"- #{item.get('id')} {item.get('status', '-')} | "
            f"tg={item.get('telegram_id') or '-'} | hash={code_hash[:10]}..."
        )
    await message.answer("\n".join(lines))


@router.message(Command("revoke"))
async def revoke_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    raw = _args(message).strip()
    if not raw:
        await message.answer("Формат: /revoke CODE или /revoke ID")
        return

    try:
        if raw.removeprefix("#").isdigit():
            code_id = int(raw.removeprefix("#"))
        else:
            # Modern storage keeps only SHA-256 hashes. Recreate the same hash
            # locally so legacy `/revoke CODE` remains usable without logging or
            # persisting the plaintext activation code.
            code_hash = hashlib.sha256(raw.upper().encode("utf-8")).hexdigest()
            payload = await client._request(
                "GET",
                "/api/v1/admin/codes/search",
                params={"query": code_hash, "limit": 2, "offset": 0},
                headers={"X-Admin-Api-Key": client.admin_api_key},
            )
            matches = [
                item
                for item in (payload.get("items", []) if isinstance(payload, dict) else [])
                if str(item.get("code_hash") or "").lower() == code_hash
            ]
            if len(matches) != 1:
                await message.answer("Код не найден.")
                return
            code_id = int(matches[0]["id"])

        item = await client._request(
            "POST",
            f"/api/v1/admin/codes/{code_id}/revoke",
            headers={"X-Admin-Api-Key": client.admin_api_key},
        )
    except BackendClientError as exc:
        if exc.status_code == 404:
            await message.answer("Код не найден.")
        else:
            await message.answer(f"Ошибка backend: {exc.detail}")
        return

    await message.answer(f"Код #{item.get('id', code_id)} отключён.")


@router.message(Command("newcode"))
async def newcode_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    if _args(message):
        await message.answer(
            "Старая форма `/newcode 30 note` больше не используется: в новой модели код "
            "привязан к активной подписке. Используй /newcode без аргументов или кнопку «Код себе».",
            parse_mode="Markdown",
        )
        return
    try:
        result = await client.admin_generate_code(message.from_user.id)
    except BackendClientError as exc:
        if exc.status_code == 404:
            await message.answer("Нет активной подписки, для которой можно выпустить код.")
        else:
            await message.answer(f"Ошибка backend: {exc.detail}")
        return
    await message.answer(
        "Код сгенерирован. Показывается один раз полностью:\n"
        f"<code>{result.get('activation_code')}</code>",
        parse_mode="HTML",
    )


@router.message(Command("events"))
async def events_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    try:
        rows = await client.admin_problem_activations()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    if not rows:
        await message.answer("Проблемных событий активации нет.")
        return
    lines = ["Последние проблемные активации:"]
    for row in rows[:20]:
        lines.append(
            f"- {row.get('created_at', '-')} | {row.get('action', '-')} | actor={row.get('actor_id', '-')}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("useconfig"))
async def useconfig_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    await message.answer(
        "Команда /useconfig больше не нужна: современный пул сам выбирает подходящий активный узел. "
        "Для просмотра узлов используй /servers."
    )


@router.message(Command("syncconfigs"))
async def syncconfigs_compat_command(message: Message) -> None:
    if await _deny(message):
        return
    try:
        nodes = await client.admin_nodes()
    except BackendClientError as exc:
        await message.answer(f"Ошибка backend: {exc.detail}")
        return
    await message.answer(
        f"Ручная синхронизация больше не требуется: backend является источником пула. Узлов сейчас: {len(nodes)}."
    )
