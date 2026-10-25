"""sqladmin на /admin: пускает только по нашей сессии с ролью admin."""

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.auth.sessions import COOKIE_NAME, get_session_user
from app.config import BASE_DIR, settings
from app.db import SessionLocal, engine
from app.models import Group, User


class SchoolAuth(AuthenticationBackend):
    """Свой вход у админки не нужен: используется общая сессия сайта (cookie est_session)."""

    async def login(self, request: Request):
        return RedirectResponse("/login", status_code=302)

    async def logout(self, request: Request):
        return RedirectResponse("/school", status_code=302)  # выходит из аккаунта сайт (/logout), не админка

    async def authenticate(self, request: Request):
        token = request.cookies.get(COOKIE_NAME)

        def lookup():
            with SessionLocal() as db:
                user = get_session_user(db, token)
                return user is not None and user.role == "admin" and user.is_active and not user.must_change_password

        if await run_in_threadpool(lookup):
            return True
        return RedirectResponse("/login", status_code=302)


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"
    can_create = False  # учителей создаёт раздел «Школа», учеников — учитель: там выдаётся временный пароль
    page_size = 50

    column_list = [User.id, User.login, User.role, User.first_name, User.last_name, User.group, User.is_active]
    column_details_exclude_list = [User.password_hash, User.avatar_history]
    column_searchable_list = [User.login, User.first_name, User.last_name]
    column_sortable_list = [User.id, User.login, User.role, User.last_name]
    form_excluded_columns = [User.password_hash, User.avatar_history, User.created_at, User.avatar]
    column_labels = {
        User.login: "Логин",
        User.role: "Роль",
        User.first_name: "Имя",
        User.last_name: "Фамилия",
        User.group: "Группа",
        User.is_active: "Активен",
        User.must_change_password: "Нужно сменить пароль",
        User.created_at: "Создан",
    }


class GroupAdmin(ModelView, model=Group):
    name = "Группа"
    name_plural = "Группы"
    icon = "fa-solid fa-users"
    page_size = 50

    column_list = [Group.id, Group.name, Group.teacher]
    column_labels = {Group.name: "Название", Group.teacher: "Учитель", Group.students: "Ученики", Group.created_at: "Создана"}
    form_excluded_columns = [Group.created_at]


def setup_admin(app) -> Admin:
    auth = SchoolAuth(secret_key=settings.secret_key, https_only=not settings.debug)
    admin = Admin(
        app,
        engine,
        title="Школа — база данных",
        templates_dir=str(BASE_DIR / "app" / "templates"),  # переопределения в templates/sqladmin/
        authentication_backend=auth,
    )
    for view in (UserAdmin, GroupAdmin):
        admin.add_view(view)
    return admin
