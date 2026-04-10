from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.backend.deps.auth import require_admin_api_key
from src.backend.deps.db import get_db
from src.backend.schemas.admin_codes import AdminCodeDeleteResponse, AdminCodeItemResponse, AdminCodeListResponse
from src.backend.services.admin_code_service import AdminCodeService

router = APIRouter(prefix="/api/v1/admin/codes", tags=["admin-codes"], dependencies=[Depends(require_admin_api_key)])


@router.get("", response_model=AdminCodeListResponse)
def admin_list_codes(limit: int = 10, offset: int = 0, db: Session = Depends(get_db)):
    return AdminCodeService(db).list_codes(limit=limit, offset=offset)


@router.get("/search", response_model=AdminCodeListResponse)
def admin_search_codes(query: str, limit: int = 10, offset: int = 0, db: Session = Depends(get_db)):
    return AdminCodeService(db).search_codes(query=query, limit=limit, offset=offset)


@router.get("/{code_id}", response_model=AdminCodeItemResponse)
def admin_get_code(code_id: int, db: Session = Depends(get_db)):
    return AdminCodeService(db).get_code(code_id)


@router.post("/{code_id}/revoke", response_model=AdminCodeItemResponse)
def admin_revoke_code(code_id: int, db: Session = Depends(get_db)):
    return AdminCodeService(db).revoke_code(code_id)


@router.post("/{code_id}/activate", response_model=AdminCodeItemResponse)
def admin_activate_code(code_id: int, db: Session = Depends(get_db)):
    return AdminCodeService(db).activate_code(code_id)


@router.delete("/{code_id}", response_model=AdminCodeDeleteResponse)
def admin_delete_code(code_id: int, db: Session = Depends(get_db)):
    return AdminCodeService(db).delete_code(code_id)
