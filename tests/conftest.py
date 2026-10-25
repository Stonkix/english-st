import os
import tempfile

# Переменные окружения задаём до импорта приложения: settings читаются один раз
_tmp = tempfile.mkdtemp()
os.environ.update(
    DEBUG="true",
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    MEDIA_DIR=f"{_tmp}/media",
    BASE_URL="http://testserver",
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    """Схема создаётся миграциями — так же, как на проде."""
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def _clean_tables(_migrated):
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client():
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        yield c


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
