"""sqladmin на /admin: пускает только по нашей сессии с ролью admin."""

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import SelectField

from app.accounts import LOGIN_TAKEN
from app.auth.sessions import COOKIE_NAME, get_session_user
from app.config import BASE_DIR, settings
from app.db import SessionLocal, engine
from app.logins import is_valid_login, normalize_login
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


LOGIN_HELP = "Логин: 3–32 символа — латинские буквы, цифры, точка, дефис, подчёркивание"


class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-user"
    can_create = False  # учителей создаёт раздел «Школа», учеников — учитель: там выдаётся временный пароль
    can_delete = False  # у удалённого остались бы файлы фото, а учителя с группами не даёт удалить БД: отключают, не удаляют
    page_size = 50

    form_overrides = {"role": SelectField}
    form_args = {"role": {"choices": [(role, role) for role in User.ROLES]}}

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

    async def on_model_change(self, data: dict, model: User, is_created: bool, request: Request) -> None:
        """Логин хранится в нижнем регистре и по формату: иначе под ним нельзя войти. ValueError показывается в форме."""
        login = normalize_login(data.get("login") or "")
        if not is_valid_login(login):
            raise ValueError(LOGIN_HELP)
        taken = await run_in_threadpool(self._login_taken, login, model.id)
        if taken:
            raise ValueError(LOGIN_TAKEN)
        data["login"] = login

    @staticmethod
    def _login_taken(login: str, own_id: int | None) -> bool:
        with SessionLocal() as db:
            return db.scalar(select(User.id).where(User.login == login, User.id != own_id)) is not None


class GroupAdmin(ModelView, model=Group):
    name = "Группа"
    name_plural = "Группы"
    icon = "fa-solid fa-users"
    can_delete = False  # у группы есть ученики
    page_size = 50

    column_list = [Group.id, Group.name, Group.teacher]
    column_labels = {Group.name: "Название", Group.teacher: "Учитель", Group.students: "Ученики", Group.created_at: "Создана"}
    # ученики переводятся в группу из карточки ученика, здесь список «все ученики» только мешает
    form_excluded_columns = [Group.created_at, Group.students]

    async def scaffold_form(self, rules=None):
        """В учителя годятся только teacher и admin: иначе ученик увидел бы прежние фото одноклассников."""
        teachers = await run_in_threadpool(self._teacher_choices)
        self.form_args = {"teacher": {"data": teachers}}
        return await super().scaffold_form(rules)

    @staticmethod
    def _teacher_choices() -> list[tuple[str, str]]:
        stmt = select(User).where(User.role.in_(("teacher", "admin"))).order_by(User.last_name, User.first_name, User.id)
        with SessionLocal() as db:
            return [(str(u.id), str(u)) for u in db.scalars(stmt)]


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
