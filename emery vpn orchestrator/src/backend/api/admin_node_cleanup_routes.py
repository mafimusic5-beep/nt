from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.backend.deps.auth import require_admin_api_key
from src.backend.deps.db import get_db
from src.backend.services.admin_node_assignment_cleanup_service import (
    AdminNodeAssignmentCleanupService,
)


router = APIRouter(prefix="/api/v1")


@router.post(
    "/admin/nodes/{node_id}/assignments/clear",
    dependencies=[Depends(require_admin_api_key)],
)
def admin_clear_node_assignments(node_id: int, db: Session = Depends(get_db)):
    return AdminNodeAssignmentCleanupService(db).clear(node_id)
