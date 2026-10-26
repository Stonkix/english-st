import logging
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import models  # noqa: F401 — регистрирует таблицы в metadata
from app.admin import setup_admin
from app.auth.deps import LoginRequired, PasswordChangeRequired, get_current_user
from app.auth.sessions import COOKIE_NAME, set_session_cookie
from app.config import BASE_DIR, Settings, settings
from app.db import SessionLocal
from app.origin import same_site
from app.routes.auth import router as auth_router
from app.routes.chat import is_json_path as is_chat_json_path
from app.routes.chat import router as chat_router
from app.routes.daily import JSON_PATHS
from app.routes.daily import router as daily_router
from app.routes.media import router as media_router
from app.routes.profile import router as profile_router
from app.routes.school import router as school_router
from app.routes.tasks import router as tasks_router
from app.routes.teacher import router as teacher_router
from app.templating import templates

logging.basicConfig(level=logging.INFO)

_PLACEHOLDERS = ("change-me",)
_UNRESOLVED = object()


def check_production_secrets(s: Settings) -> None:
    """В проде не даём стартовать с секретом-заглушкой."""
    if not s.debug and (not s.secret_key.strip() or s.secret_key in _PLACEHOLDERS):
        raise RuntimeError("Задайте SECRET_KEY в .env (или DEBUG=true для локальной разработки)")


check_production_secrets(settings)
logging.info("English ST: DEBUG=%s, BASE_URL=%s, база %s", settings.debug, settings.base_url, settings.database_url)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# В проде /static отдаёт nginx, mount работает для локальной разработки
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def check_origin(request: Request, call_next):
    """Защита от CSRF: изменяющие запросы принимаем только со своего сайта."""
    if request.method in _UNSAFE_METHODS:
        # Origin главнее Referer; если он есть, но битый или «null» — отказ, без запасного пути
        header = request.headers.get("origin")
        if header is None:
            header = request.headers.get("referer")
        if not same_site(header):
            logging.warning(
                "403: запрос %s %s пришёл с адреса %r, а BASE_URL=%r — откройте сайт по адресу из BASE_URL",
                request.method, request.url.path, header, settings.base_url,
            )
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
    if request.url.path in JSON_PATHS or is_chat_json_path(request.url.path):  # плеер и чат ждут JSON
        return JSONResponse({"error": "Ошибка"}, status_code=exc.status_code, headers=exc.headers)
    user = await run_in_threadpool(_error_page_user, request)  # обращение к БД не блокирует цикл событий
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", {"user": user}, status_code=404)
    return templates.TemplateResponse(
        request, "error.html", {"code": exc.status_code, "user": user},
        status_code=exc.status_code, headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Ошибка в адресе (не число, ноль, огромный id) — такой страницы нет; ошибка в форме — 400. Без сырого JSON."""
    if request.url.path in JSON_PATHS or is_chat_json_path(request.url.path):  # короткий JSON без подробностей
        return JSONResponse({"error": "Некорректный запрос"}, status_code=422)
    user = await run_in_threadpool(_error_page_user, request)
    if any(err.get("loc") and err["loc"][0] == "path" for err in exc.errors()):
        return templates.TemplateResponse(request, "404.html", {"user": user}, status_code=404)
    return templates.TemplateResponse(request, "error.html", {"code": 400, "user": user}, status_code=400)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """Неожиданный сбой: в журнал и на страницу сайта. К БД не обращаемся — она могла и упасть."""
    logging.error("Необработанная ошибка: %s %s", request.method, request.url.path, exc_info=exc)
    return templates.TemplateResponse(request, "error.html", {"code": 500, "user": None}, status_code=500)


app.include_router(auth_router)
app.include_router(daily_router)
app.include_router(chat_router)
app.include_router(profile_router)
app.include_router(media_router)
app.include_router(teacher_router)
app.include_router(tasks_router)
app.include_router(school_router)
setup_admin(app)  # /admin — sqladmin, вход по сессии сайта (только admin)


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
