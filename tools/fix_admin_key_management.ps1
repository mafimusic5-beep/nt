$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$schemas = Join-Path $root 'emery vpn orchestrator/src/backend/schemas/admin.py'
$repo = Join-Path $root 'emery vpn orchestrator/src/backend/repositories/admin_repo.py'
$service = Join-Path $root 'emery vpn orchestrator/src/backend/services/admin_service.py'
$routes = Join-Path $root 'emery vpn orchestrator/src/backend/api/routes.py'
$client = Join-Path $root 'emery vpn orchestrator/src/bot/api/backend_client.py'
$handler = Join-Path $root 'emery vpn orchestrator/src/bot/handlers/admin.py'

# schemas/admin.py
$content = Get-Content $schemas -Raw -Encoding UTF8
if ($content -notmatch 'class ActivationCodeInfoResponse') {
  $content += @'


class ActivationCodeInfoResponse(BaseModel):
    code_hash: str
    status: str
    user_id: int
    telegram_id: int
    subscription_id: int
    subscription_status: str
    region_code: str
    created_at: datetime
    first_redeemed_at: datetime | None
    subscription_ends_at: datetime


class ActivationCodeDeleteResponse(BaseModel):
    code_hash: str
    status: str
    deleted: bool
'@
  Set-Content $schemas $content -Encoding UTF8
}

# repositories/admin_repo.py
$content = Get-Content $repo -Raw -Encoding UTF8
if ($content -notmatch 'def get_activation_code_by_hash') {
  $content += @'

    def get_activation_code_by_hash(self, code_hash: str) -> ActivationCode | None:
        return self.db.scalar(select(ActivationCode).where(ActivationCode.code_hash == code_hash))

    def revoke_activation_code(self, code: ActivationCode) -> ActivationCode:
        code.status = "deleted"
        return code
'@
  Set-Content $repo $content -Encoding UTF8
}

# services/admin_service.py
$content = Get-Content $service -Raw -Encoding UTF8
if ($content -notmatch 'ActivationCodeInfoResponse') {
  $content = $content.Replace(
    'from src.backend.schemas.admin import GrantSubscriptionRequest, GrantSubscriptionResponse, VpnNodeResponse, VpnNodeUpsertRequest',
    'from src.backend.schemas.admin import ActivationCodeDeleteResponse, ActivationCodeInfoResponse, GrantSubscriptionRequest, GrantSubscriptionResponse, VpnNodeResponse, VpnNodeUpsertRequest'
  )
}
if ($content -notmatch 'from src.common.models import User') {
  $content = $content.Replace(
    'from src.common.config import settings',
    "from src.common.config import settings`r`nfrom src.common.models import User"
  )
}
if ($content -notmatch 'def get_code_info\(') {
  $content = $content.Replace(@'
    def problem_activations(self) -> list[dict]:
        rows = self.admin_repo.list_problem_activations()
        return [
            {
                "created_at": r.created_at,
                "actor_id": r.actor_id,
                "action": r.action,
                "entity_id": r.entity_id,
                "details": r.details,
            }
            for r in rows
        ]
'@, @'
    def problem_activations(self) -> list[dict]:
        rows = self.admin_repo.list_problem_activations()
        return [
            {
                "created_at": r.created_at,
                "actor_id": r.actor_id,
                "action": r.action,
                "entity_id": r.entity_id,
                "details": r.details,
            }
            for r in rows
        ]

    def get_code_info(self, plain_code: str) -> ActivationCodeInfoResponse:
        normalized = plain_code.strip().upper()
        if not normalized:
            raise HTTPException(status_code=400, detail="activation_code_required")
        code_hash = hash_activation_code(normalized)
        code = self.admin_repo.get_activation_code_by_hash(code_hash)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        user = self.db.get(User, code.user_id)
        sub = self.sub_repo.get_subscription(code.subscription_id)
        if not user or not sub:
            raise HTTPException(status_code=404, detail="activation_code_relations_not_found")
        return ActivationCodeInfoResponse(
            code_hash=code.code_hash,
            status=code.status,
            user_id=code.user_id,
            telegram_id=user.telegram_id,
            subscription_id=code.subscription_id,
            subscription_status=sub.status,
            region_code=sub.region_code,
            created_at=code.created_at,
            first_redeemed_at=code.first_redeemed_at,
            subscription_ends_at=sub.ends_at,
        )

    def delete_code(self, plain_code: str) -> ActivationCodeDeleteResponse:
        normalized = plain_code.strip().upper()
        if not normalized:
            raise HTTPException(status_code=400, detail="activation_code_required")
        code_hash = hash_activation_code(normalized)
        code = self.admin_repo.get_activation_code_by_hash(code_hash)
        if not code:
            raise HTTPException(status_code=404, detail="activation_code_not_found")
        if code.status != "active":
            return ActivationCodeDeleteResponse(code_hash=code.code_hash, status=code.status, deleted=False)
        self.admin_repo.revoke_activation_code(code)
        self.audit_repo.write(
            "admin",
            "api",
            "activation_code_deleted",
            "activation_code",
            str(code.id),
            {"subscription_id": code.subscription_id},
        )
        self.db.commit()
        return ActivationCodeDeleteResponse(code_hash=code.code_hash, status=code.status, deleted=True)
'@)
  Set-Content $service $content -Encoding UTF8
}

# api/routes.py
$content = Get-Content $routes -Raw -Encoding UTF8
if ($content -notmatch 'ActivationCodeInfoResponse') {
  $content = $content.Replace(
@'
from src.backend.schemas.admin import (
    AdminStatsResponse,
    BestNodeResponse,
    GrantSubscriptionRequest,
    GrantSubscriptionResponse,
    HealthcheckRunResponse,
    ManualCodeResponse,
    NodeActionResponse,
    ProblemActivationResponse,
    VpnNodeResponse,
    VpnNodeUpsertRequest,
)
'@,
@'
from src.backend.schemas.admin import (
    ActivationCodeDeleteResponse,
    ActivationCodeInfoResponse,
    AdminStatsResponse,
    BestNodeResponse,
    GrantSubscriptionRequest,
    GrantSubscriptionResponse,
    HealthcheckRunResponse,
    ManualCodeResponse,
    NodeActionResponse,
    ProblemActivationResponse,
    VpnNodeResponse,
    VpnNodeUpsertRequest,
)
'@)
}
if ($content -notmatch '/admin/codes/info') {
  $content = $content.Replace(@'
@router.get("/admin/activations/problems",
    response_model=list[ProblemActivationResponse],
    dependencies=[Depends(require_admin_api_key)],
)
def admin_problem_activations(db: Session = Depends(get_db)):
    return AdminService(db).problem_activations()
'@, @'
@router.get("/admin/activations/problems",
    response_model=list[ProblemActivationResponse],
    dependencies=[Depends(require_admin_api_key)],
)
def admin_problem_activations(db: Session = Depends(get_db)):
    return AdminService(db).problem_activations()


@router.get("/admin/codes/info", response_model=ActivationCodeInfoResponse, dependencies=[Depends(require_admin_api_key)])
def admin_code_info(code: str, db: Session = Depends(get_db)):
    return AdminService(db).get_code_info(code)


@router.delete("/admin/codes", response_model=ActivationCodeDeleteResponse, dependencies=[Depends(require_admin_api_key)])
def admin_delete_code(code: str, db: Session = Depends(get_db)):
    return AdminService(db).delete_code(code)
'@)
  Set-Content $routes $content -Encoding UTF8
}

# bot/api/backend_client.py
$content = Get-Content $client -Raw -Encoding UTF8
if ($content -notmatch 'async def admin_code_info') {
  $content += @'

    async def admin_code_info(self, code: str) -> dict:
        return await self._request(
            "GET",
            "/api/v1/admin/codes/info",
            params={"code": code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )

    async def admin_delete_code(self, code: str) -> dict:
        return await self._request(
            "DELETE",
            "/api/v1/admin/codes",
            params={"code": code},
            headers={"X-Admin-Api-Key": self.admin_api_key},
        )
'@
  Set-Content $client $content -Encoding UTF8
}

# bot/handlers/admin.py
$content = Get-Content $handler -Raw -Encoding UTF8
if ($content -notmatch 'CommandObject') {
  $content = $content.Replace(
    'from aiogram.filters import Command',
    "from aiogram.filters import Command`r`nfrom aiogram.filters.command import CommandObject"
  )
}
if ($content -notmatch 'keyinfo_command_handler') {
  $content = $content.Replace(@'
@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await message.answer("Админ-панель", reply_markup=admin_menu_keyboard())
'@, @'
@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    await message.answer(
        "Админ-панель\n\n"
        "Команды:\n"
        "- /keyinfo <код> — информация по ключу\n"
        "- /keydelete <код> — удалить (деактивировать) ключ",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("keyinfo"))
async def keyinfo_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keyinfo ABCD1234EFGH")
        return
    try:
        info = await client.admin_code_info(code)
        redeemed_at = format_dt(info.get("first_redeemed_at")) if info.get("first_redeemed_at") else "—"
        await message.answer(
            "Информация по ключу:\n"
            f"Код: <code>{code}</code>\n"
            f"Статус: {info.get('status')}\n"
            f"Telegram ID: {info.get('telegram_id')}\n"
            f"User ID: {info.get('user_id')}\n"
            f"Subscription ID: {info.get('subscription_id')}\n"
            f"Статус подписки: {info.get('subscription_status')}\n"
            f"Регион: {info.get('region_code')}\n"
            f"Создан: {format_dt(info.get('created_at'))}\n"
            f"Первое погашение: {redeemed_at}\n"
            f"Действует до: {format_dt(info.get('subscription_ends_at'))}",
            parse_mode="HTML",
        )
    except BackendClientError as exc:
        logger.warning("admin keyinfo failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")


@router.message(Command("keydelete"))
async def keydelete_command_handler(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен.")
        return
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: /keydelete ABCD1234EFGH")
        return
    try:
        result = await client.admin_delete_code(code)
        action = "деактивирован" if result.get("deleted") else "уже не активен"
        await message.answer(
            "Операция выполнена:\n"
            f"Код: <code>{code}</code>\n"
            f"Статус: {result.get('status')}\n"
            f"Результат: {action}",
            parse_mode="HTML",
        )
    except BackendClientError as exc:
        logger.warning("admin keydelete failed: err=%s", exc.detail)
        await message.answer(f"Ошибка backend: {exc.detail}")
'@)
  Set-Content $handler $content -Encoding UTF8
}

Write-Host 'patched admin key management support'
