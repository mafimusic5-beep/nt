import json

from src.backend.repositories.base import BaseRepository
from src.common.models import AuditLog


class AuditRepository(BaseRepository):
    def write(
        self,
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict | None = None,
    ) -> None:
        log = AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=json.dumps(details or {}, ensure_ascii=False),
        )
        self.db.add(log)
