import logging
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import models  # noqa: F401 — регистрирует таблицы в metadata
from app.config import BASE_DIR, Settings, settings
from app.templating import templates

logging.basicConfig(level=logging.INFO)

_PLACEHOLDERS = ("change-me",)


def check_production_secrets(s: Settings) -> None:
    """В проде не даём стартовать с секретом-заглушкой."""
    if not s.debug and s.secret_key in _PLACEHOLDERS:
        raise RuntimeError("Задайте SECRET_KEY в .env (или DEBUG=true для локальной разработки)")


check_production_secrets(settings)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# В проде /static отдаёт nginx, mount работает для локальной разработки
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", status_code=404)
    return templates.TemplateResponse(
        request, "error.html", {"code": exc.status_code},
        status_code=exc.status_code, headers=exc.headers,
    )


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ui-kit")
def ui_kit(request: Request):
    """Витрина компонентов для разработки; в проде её нет."""
    if not settings.debug:
        raise StarletteHTTPException(404)
    demo = {
        role: SimpleNamespace(role=role, first_name="Петя", last_name="Котов", avatar=None)
        for role in ("student", "teacher", "admin")
    }
    return templates.TemplateResponse(
        request, "ui_kit.html", {"user": demo["student"], "demo_users": demo}
    )
