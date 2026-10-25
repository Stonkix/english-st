import logging

from fastapi import FastAPI

from app import models  # noqa: F401 — регистрирует таблицы в metadata
from app.config import Settings, settings

logging.basicConfig(level=logging.INFO)

_PLACEHOLDERS = ("change-me",)


def check_production_secrets(s: Settings) -> None:
    """В проде не даём стартовать с секретом-заглушкой."""
    if not s.debug and s.secret_key in _PLACEHOLDERS:
        raise RuntimeError("Задайте SECRET_KEY в .env (или DEBUG=true для локальной разработки)")


check_production_secrets(settings)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
