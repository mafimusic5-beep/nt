from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.backend.deps.auth import require_admin_api_key
from src.backend.deps.db import get_db
from src.backend.services.admin_node_delete_service import AdminNodeDeleteService


router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin_api_key)])


@router.delete("/nodes/{node_id}")
def delete_node(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return AdminNodeDeleteService(db).delete(node_id)
