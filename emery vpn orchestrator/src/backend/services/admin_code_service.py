from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.backend.repositories.admin_code_repo import AdminCodeRepository
from src.backend.repositories.audit_repo import AuditRepository


class AdminCodeService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = AdminCodeRepository(db)
        self.audit = AuditRepository(db)

    @staticmethod
    def _serialize(row) -> dict:
        code = row[0]
        telegram_id = row[1]
        subscription_status = row[2]
        subscription_ends_at = row[3]
        return {
            "id": code.id,
            "user_id": code.user_id,
            "telegram_id": telegram_id,
            "subscription_id": code.subscription_id,
            "code_hash": code.code_hash,
            "status": code.status,
            "created_at": code.created_at,
            "first_redeemed_at": code.first_redeemed_at,
            "subscription_status": subscription_status,
            "subscription_ends_at": subscription_ends_at,
        }

    def list_codes(self, limit: int = 10, offset: int = 0) -> dict:
        rows = self.repo.list(limit, offset)
        total = self.repo.count()
        self.db.commit()
        return {"items": [self._serialize(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def search_codes(self, query: str, limit: int = 10, offset: int = 0) -> dict:
        rows = self.repo.search(query, limit, offset)
        total = self.repo.count_search(query)
        self.db.commit()
        return {"items": [self._serialize(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def get_code(self, code_id: int) -> dict:
        row = self.repo.get(code_id)
        if not row:
            raise HTTPException(status_code=404, detail="code_not_found")
        self.db.commit()
        return self._serialize(row)

    def revoke_code(self, code_id: int) -> dict:
        code = self.repo.set_status(code_id, "revoked")
        if not code:
            raise HTTPException(status_code=404, detail="code_not_found")
        self.audit.write("admin", "api", "revoke_activation_code", "activation_code", str(code.id))
        self.db.commit()
        return self.get_code(code_id)

    def activate_code(self, code_id: int) -> dict:
        code = self.repo.set_status(code_id, "active")
        if not code:
            raise HTTPException(status_code=404, detail="code_not_found")
        self.audit.write("admin", "api", "activate_activation_code", "activation_code", str(code.id))
        self.db.commit()
        return self.get_code(code_id)

    def delete_code(self, code_id: int) -> dict:
        code = self.repo.delete(code_id)
        if not code:
            raise HTTPException(status_code=404, detail="code_not_found")
        self.audit.write("admin", "api", "delete_activation_code", "activation_code", str(code.id))
        self.db.commit()
        return {"ok": True, "deleted_id": code_id}
