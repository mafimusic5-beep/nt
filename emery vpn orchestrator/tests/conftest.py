from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.backend.core.bootstrap import seed_plans
from src.common.db import Base


@pytest.fixture()
def db_session(tmp_path: Path) -> Session:
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_file}", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(engine)
    session = TestingSessionLocal()
    seed_plans(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
