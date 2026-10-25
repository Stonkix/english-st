import logging
from types import SimpleNamespace

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import models  # noqa: F401 — регистрирует таблицы в metadata
from app.auth.deps import LoginRequired, PasswordChangeRequired, get_current_user
from app.auth.sessions import COOKIE_NAME, set_session_cookie
from app.config import BASE_DIR, Settings, settings
from app.db import SessionLocal
from app.routes.auth import router as auth_router
from app.routes.media import router as media_router
from app.routes.profile import router as profile_router
from app.routes.teacher import router as teacher_router
from app.templating import templates

logging.basicConfig(level=logging.INFO)

_PLACEHOLDERS = ("change-me",)
_UNRESOLVED = object()


def check_production_secrets(s: Settings) -> None:
    """В проде не даём стартовать с секретом-заглушкой."""
    if not s.debug and s.secret_key in _PLACEHOLDERS:
        raise RuntimeError("Задайте SECRET_KEY в .env (или DEBUG=true для локальной разработки)")


check_production_secrets(settings)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# В проде /static отдаёт nginx, mount работает для локальной разработки
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


_DEFAULT_PORTS = {"http": 80, "https": 443}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_of(value: str) -> str | None:
    """«схема://хост[:порт]» из Origin или полного URL (Referer); None, если не разобрать."""
    try:
        parts = urlsplit(value.strip())
        scheme, host, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None  # в том числе «Origin: null»
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS[scheme] else "")


@app.middleware("http")
async def check_origin(request: Request, call_next):
    """Защита от CSRF: изменяющие запросы принимаем только со своего сайта."""
    if request.method in _UNSAFE_METHODS:
        # Origin главнее Referer; если он есть, но битый или «null» — отказ, без запасного пути
        header = request.headers.get("origin")
        if header is None:
            header = request.headers.get("referer")
        source = _origin_of(header) if header is not None else None
        if source is None or source != _origin_of(settings.base_url):
            return templates.TemplateResponse(request, "error.html", {"code": 403}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def refresh_session_cookie(request: Request, call_next):
    """Продлевает cookie вместе с сессией в БД (раз в час, см. TOUCH_EVERY)."""
    response = await call_next(request)
    token = getattr(request.state, "refresh_session_token", None)
    if token:
        own = f"{COOKIE_NAME}="
        # вход и выход сами ставят/снимают cookie — их значение главнее
        if not any(h.startswith(own) for h in response.headers.getlist("set-cookie")):
            set_session_cookie(response, token)
    return response


def _redirect(request: Request, url: str) -> Response:
    """303 на url; для HTMX-запросов — 200 с HX-Redirect, иначе страница встроится во фрагмент."""
    if request.headers.get("hx-request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    return _redirect(request, "/login")


@app.exception_handler(PasswordChangeRequired)
async def password_change_required(request: Request, exc: PasswordChangeRequired):
    return _redirect(request, "/password/new")


def _error_page_user(request: Request):
    """Пользователь для меню на странице ошибки: уже определённый запросом или найденный по cookie."""
    user = getattr(request.state, "user", _UNRESOLVED)
    if user is not _UNRESOLVED:
        return user
    try:
        with SessionLocal() as db:
            return get_current_user(request, db)
    except Exception:  # страница ошибки не должна сама падать
        logging.exception("Не удалось определить пользователя для страницы ошибки")
        return None


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    user = await run_in_threadpool(_error_page_user, request)  # обращение к БД не блокирует цикл событий
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {"user": user}, status_code=404)
    return templates.TemplateResponse(
        request, "error.html", {"code": exc.status_code, "user": user},
        status_code=exc.status_code, headers=exc.headers,
    )


app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(media_router)
app.include_router(teacher_router)


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
