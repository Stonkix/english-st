# Этапы 0–1: каркас и аккаунты — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** рабочий сайт с входом, блокировками, профилем и аватаром, группами и учениками у учителя, разделом «Школа» у админа и файлами деплоя — готовый к первой выкладке на VPS.

**Architecture:** FastAPI + Jinja2 + HTMX, страницы рендерятся на сервере; синхронный SQLAlchemy 2.0 поверх SQLite, схема через Alembic. Сессии — случайный токен в cookie, хеш токена в БД. Загруженные файлы отдаются только после проверки прав (в проде через `X-Accel-Redirect`).

**Tech Stack:** Python 3.12, FastAPI, uvicorn[standard], SQLAlchemy 2.0, Alembic, Jinja2, pydantic-settings, python-multipart, Pillow + pillow-heif, sqladmin, pytest + httpx (TestClient), htmx (вендорный файл).

**Spec:** `docs/superpowers/specs/2026-10-25-english-st-design.md` (§3, §5.1, §6, §7 — User/Group/Session/LoginAttempt, §8.1, §9, §10, §11, §12 этапы 0–1).

**Образец кода:** соседний проект `C:\Users\alexa\Desktop\rieltor_site` — `app/config.py`, `app/db.py`, `app/main.py`, `app/templating.py`, `app/login_guard.py`, `app/images.py`, `app/admin.py`, `deploy/*`, `tests/test_smoke.py`. Повторяем его стиль: короткие модули, комментарии по-русски, докстринги с инструкцией для CLI.

## Global Constraints

- Python 3.12; без Node, без сборки фронтенда, без CDN — все JS/шрифты лежат в `app/static`.
- Интерфейс и все тексты — на русском.
- Коммиты без строки `Co-Authored-By` и без упоминания Claude.
- Пароль: регулярка `^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[\x21-\x7E]{10,}$` — от 10 символов, латиница, строчная + заглавная + цифра, спецсимволы разрешены, не обязательны.
- Блокировка входа: 5 неудач по ключу `логин|IP` → блок на 10 минут; 20 неудач за 10 минут по ключу `IP` → блок IP на 10 минут.
- Хеш паролей: `hashlib.scrypt`, параметры как у соседних сайтов: `n=2**14, r=8, p=1, dklen=32`, соль 16 байт.
- Сессия: cookie `est_session`, httpOnly, SameSite=Lax, Secure при `DEBUG=false`; срок 30 дней со скольжением.
- Все даты в БД — naive UTC (`app/timeutil.utcnow()`).
- Логин: `^[a-z0-9._-]{3,32}$`, хранится в нижнем регистре.
- Загрузка: не больше 15 МБ на файл; аватар — квадрат 512px WebP, EXIF/GPS удалены, HEIC поддерживается.
- Прод: `uvicorn --port 8002 --workers 1`, пользователь `englishst`, каталог `/srv/englishst`.
- Изменяющие запросы (POST/PUT/PATCH/DELETE) принимаются, только если `Origin` (или при его отсутствии `Referer`) совпадает с `BASE_URL`.
- У аккаунта одна роль: `student` / `teacher` / `admin`; `admin` проходит любые проверки `teacher`.
- 1 ученик = 1 группа; группа принадлежит одному учителю (или админу).

## Review Focus

1. Логин с заглавными буквами и пробелами (`" Ivan.Petrov "`) — входит как `ivan.petrov`, и блокировка считает его тем же логином (нельзя обойти блок сменой регистра). Тест — Task 5 и Task 7.
2. Пароль, набранный на русской раскладке (`Привет12345`), — понятное сообщение «Только латиница…», а не общий отказ. Тест — Task 4 и Task 7.
3. Аватар — не картинка, слишком большой файл или HEIC с iPhone — понятная ошибка или успешная загрузка, но никогда 500. Тест — Task 8.
4. Ученика деактивировали, пока он залогинен, — следующий же запрос выкидывает его на `/login`. Тест — Task 9.
5. Учитель дважды нажал «Создать» или ввёл занятый логин — сообщение «Логин уже занят», без 500 и без дубликата. Тест — Task 9.

---

## Карта файлов

```
requirements.txt, requirements-dev.txt, .gitignore, .gitattributes, .env.example, README.md
alembic.ini
alembic/env.py, alembic/script.py.mako, alembic/versions/0001_accounts.py
app/__init__.py
app/config.py          настройки из .env
app/db.py              engine (WAL, foreign_keys), SessionLocal, Base, get_db
app/timeutil.py        utcnow()
app/main.py            приложение, middleware Origin, роутеры, ошибки
app/templating.py      Jinja2Templates + глобальные переменные
app/models/__init__.py импорт всех моделей
app/models/accounts.py User, Group
app/models/security.py Session, LoginAttempt
app/auth/passwords.py  hash/verify, validate_password, generate_temp_password
app/auth/login_guard.py блокировки входа
app/auth/sessions.py   создание/проверка/отзыв сессий
app/auth/deps.py       current_user, require_user, require_role, home_for
app/auth/access.py     can_view_avatar
app/logins.py          normalize_login, is_valid_login, suggest_login, unique_login
app/images.py          save_avatar, InvalidImage
app/routes/auth.py     /login, /logout, /password/new, /
app/routes/profile.py  /profile, пароль, аватар
app/routes/media.py    /media/{path} с проверкой прав
app/routes/teacher.py  группы и ученики
app/routes/school.py   учителя (админ)
app/admin.py           sqladmin только для admin
app/cli.py             create-admin, reset-password, unlock
app/templates/…        base.html, partials/nav.html, ui_kit.html, auth/*, profile.html, teacher/*, school/*, 404.html, error.html
app/static/css/app.css UI-кит
app/static/js/app.js   подсказки пароля, нажатие кнопок
app/static/fonts/      Nunito woff2 (cyrillic + latin, 400/700/800) + OFL.txt
app/static/vendor/htmx.min.js  копия из rieltor_site
deploy/englishst.service, deploy/nginx.conf, deploy/backup.sh, deploy/update.sh
tests/conftest.py, tests/factories.py, tests/test_*.py
```

---

### Task 1: Каркас приложения, БД и Alembic

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `.gitattributes`, `.env.example`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `app/__init__.py`, `app/config.py`, `app/db.py`, `app/timeutil.py`, `app/main.py`, `app/models/__init__.py`
- Test: `tests/conftest.py`, `tests/test_app.py`

**Interfaces:**
- Produces:
  - `app.config.settings: Settings` с полями `debug: bool=False`, `base_url: str="http://127.0.0.1:8002"`, `secret_key: str="change-me"`, `database_url: str` (по умолчанию `sqlite:///<BASE_DIR>/data/site.db`), `media_dir: Path=BASE_DIR/"media"`; `BASE_DIR: Path`.
  - `app.db`: `engine`, `SessionLocal`, `class Base(DeclarativeBase)`, `get_db() -> Iterator[Session]` — как в rieltor_site (WAL + foreign_keys).
  - `app.timeutil.utcnow() -> datetime` — naive UTC.
  - `app.main.app: FastAPI` с `GET /healthz -> {"ok": true}`; `docs_url/redoc_url/openapi_url = None`.
  - `tests/conftest.py`: перед импортом приложения выставляет `DEBUG=true`, `DATABASE_URL` и `MEDIA_DIR` во временный каталог, `BASE_URL=http://testserver`; фикстура сессии прогоняет `alembic upgrade head` через `alembic.command.upgrade(Config("alembic.ini"), "head")`; фикстура `client` — `TestClient(app, headers={"Origin": "http://testserver"})`; autouse-фикстура после каждого теста удаляет строки из всех таблиц `reversed(Base.metadata.sorted_tables)`; фикстура `db` — `SessionLocal()`.

- [ ] **Step 1: Write the failing tests** — `tests/test_app.py`

```python
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}

def test_no_openapi(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

def test_prod_refuses_placeholder_secret(monkeypatch):
    from app.main import check_production_secrets
    from app.config import Settings
    import pytest
    with pytest.raises(RuntimeError):
        check_production_secrets(Settings(debug=False, secret_key="change-me"))
    check_production_secrets(Settings(debug=False, secret_key="x" * 48))  # не бросает
```

- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest tests/test_app.py -q` — Expected: FAIL (нет `app.main`).
- [ ] **Step 3: Implement** файлы из списка. `requirements.txt`: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `sqlalchemy>=2.0,<2.1`, `alembic>=1.13`, `sqladmin[i18n]>=0.32`, `jinja2>=3.1`, `python-multipart>=0.0.9`, `pillow>=11.0`, `pillow-heif>=0.18`, `itsdangerous>=2.2`, `pydantic-settings>=2.4`, `tzdata`. `requirements-dev.txt`: `-r requirements.txt`, `pytest>=8`, `httpx>=0.27`. `.gitignore` как у rieltor_site + `media/`, `data/`. `.gitattributes`: `* text=auto eol=lf`. `check_production_secrets(s: Settings) -> None` бросает `RuntimeError("Задайте SECRET_KEY в .env (или DEBUG=true для локальной разработки)")` при `not s.debug` и секрете из заглушек; вызывается на импорте `app.main` для `settings`. `alembic/env.py` берёт URL из `settings.database_url` и `target_metadata = Base.metadata` (импорт `app.models`), `render_as_batch=True` (SQLite).
- [ ] **Step 4: Run** `.venv\Scripts\python -m pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "Каркас: FastAPI, SQLite, Alembic, тесты"`

---

### Task 2: UI-кит и базовый шаблон

**Files:**
- Create: `app/templating.py`, `app/templates/base.html`, `app/templates/partials/nav.html`, `app/templates/ui_kit.html`, `app/templates/404.html`, `app/templates/error.html`, `app/static/css/app.css`, `app/static/js/app.js`, `app/static/fonts/*` (+ `OFL.txt`), `app/static/vendor/htmx.min.js`, `app/static/img/favicon.svg`
- Modify: `app/main.py` (mount `/static`, обработчик ошибок, `GET /ui-kit`)
- Test: `tests/test_ui.py`

**Interfaces:**
- Produces:
  - `app.templating.templates: Jinja2Templates`, глобалы `settings`, `static_v`.
  - `base.html` — блоки `title`, `content`, `scripts`; подключает `css/app.css?v={{ static_v }}`, `vendor/htmx.min.js`, `js/app.js`; если в контексте есть `user`, рисует `partials/nav.html`.
  - `partials/nav.html` — пункты по роли (только реализованные в этом плане): ученик — «Главная» `/`, «Профиль» `/profile`; учитель — «Группы» `/teacher`, «Профиль»; админ — как учитель + «Школа» `/school`. Активный пункт по `request.url.path`.
  - CSS-классы, которыми пользуются все следующие шаблоны: `.page`, `.card`, `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-block`, `.field` (label + input + `.field-error`), `.alert-error`, `.alert-success`, `.avatar`, `.avatar-lg`, `.tabbar`, `.list`, `.list-item`, `.badge`, `.temp-password` (крупный моноширинный блок для показа пароля).

- [ ] **Step 1: Write the failing tests** — `tests/test_ui.py`

```python
def test_static_css(client):
    assert client.get("/static/css/app.css").status_code == 200

def test_ui_kit_in_debug(client):
    r = client.get("/ui-kit")
    assert r.status_code == 200 and "btn-primary" in r.text

def test_404_page(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "Страница не найдена" in r.text
```

- [ ] **Step 2: Run** `pytest tests/test_ui.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.** Стиль из §9 спеки: Nunito, зелёный `#58CC02` (тень `#58A700`), синий `#1CB0F6`, красный `#FF4B4B` (тень `#EA2B2B`), рамки `#E5E5E5`, скругление карточек 16px; «объёмная» кнопка — `box-shadow: 0 4px 0 <тень>`, при `:active` — `translateY(4px)` и тень 0. Мобильный первым: `.tabbar` фиксирована снизу; `@media (min-width: 900px)` — левая колонка, контент по центру до 600px. `@view-transition { navigation: auto; }`; `prefers-reduced-motion` отключает переходы. Шрифты — Nunito 400/700/800, подмножества cyrillic и latin, woff2 из npm-пакета `@fontsource/nunito` (лицензия OFL — положить `OFL.txt`). `htmx.min.js` скопировать из `C:\Users\alexa\Desktop\rieltor_site\app\static\vendor\`. `/ui-kit` показывает все компоненты; при `DEBUG=false` — 404.
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS. Открыть `/ui-kit` в браузере в размере 375×812 и ≥900px — кнопки, поля, меню выглядят как на референсе Duolingo.
- [ ] **Step 5: Commit** `git commit -am "UI-кит в стиле Duolingo и базовый шаблон"` (с `git add` новых файлов).

---

### Task 3: Модели аккаунтов и первая миграция

**Files:**
- Create: `app/models/accounts.py`, `app/models/security.py`, `alembic/versions/0001_accounts.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_models.py`, `tests/factories.py`

**Interfaces:**
- Produces:
  - `User`: `id: int`, `login: str` (unique, index), `password_hash: str`, `role: str` (`"student"|"teacher"|"admin"`), `first_name: str`, `last_name: str`, `avatar: str | None` (путь относительно `media_dir`), `group_id: int | None` (FK `groups.id`, `ondelete="RESTRICT"`), `must_change_password: bool=False`, `is_active: bool=True`, `created_at: datetime`; свойство `full_name -> str` (`"Имя Фамилия"`); `ROLES = ("student", "teacher", "admin")`.
  - `Group`: `id`, `name: str`, `teacher_id: int` (FK `users.id`), `created_at`; relationship `students: list[User]` (по `User.group_id`), `teacher: User`.
  - `Session` (таблица `sessions`): `token_hash: str` (PK, sha256 hex), `user_id` (FK, `ondelete="CASCADE"`), `created_at`, `last_seen_at`, `expires_at`, `ip: str`, `user_agent: str`.
  - `LoginAttempt` (таблица `login_attempts`): `key: str` (PK), `failures: int`, `last_failure_at: datetime | None`, `blocked_until: datetime | None`.
  - `tests/factories.py`: `make_user(db, role="student", login=None, password="Passw0rd12", group=None, must_change=False, is_active=True, first_name="Иван", last_name="Петров") -> User` (логин по умолчанию — `f"user{n}"`), `make_group(db, teacher, name="Группа 1") -> Group`, `login_as(client, login, password="Passw0rd12") -> Response`. `make_user` хеширует пароль через `app.auth.passwords.hash_password` (Task 4 — до неё фабрика импортирует его лениво).

- [ ] **Step 1: Write the failing tests** — `tests/test_models.py`

```python
import pytest
from sqlalchemy.exc import IntegrityError
from app.models import User, Group

def test_login_unique(db):
    db.add(User(login="ivan", password_hash="x", role="student", first_name="И", last_name="П"))
    db.commit()
    db.add(User(login="ivan", password_hash="x", role="student", first_name="И", last_name="П"))
    with pytest.raises(IntegrityError):
        db.commit()

def test_group_students(db):
    t = User(login="t1", password_hash="x", role="teacher", first_name="Анна", last_name="С")
    db.add(t); db.flush()
    g = Group(name="5А", teacher_id=t.id); db.add(g); db.flush()
    db.add(User(login="s1", password_hash="x", role="student", first_name="Петя", last_name="К", group_id=g.id))
    db.commit(); db.refresh(g)
    assert [s.login for s in g.students] == ["s1"] and g.teacher.login == "t1"

def test_migration_matches_models():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.db import engine, Base
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
```

- [ ] **Step 2: Run** `pytest tests/test_models.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** модели (SQLAlchemy 2.0 `Mapped[...]`) и миграцию `0001_accounts` (сгенерировать `alembic revision --autogenerate -m accounts`, переименовать файл, проверить глазами).
- [ ] **Step 4: Run** `pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Модели пользователей, групп, сессий и попыток входа"`

---

### Task 4: Пароли

**Files:**
- Create: `app/auth/__init__.py`, `app/auth/passwords.py`
- Test: `tests/test_passwords.py`

**Interfaces:**
- Produces:
  - `hash_password(password: str) -> str` — формат `"scrypt$<salt_hex>$<hash_hex>"`.
  - `verify_password(password: str, stored: str) -> bool` — сравнение через `hmac.compare_digest`; на мусорном `stored` возвращает `False`.
  - `validate_password(password: str) -> list[str]` — коды невыполненных правил в порядке `["length", "lower", "upper", "digit", "charset"]`; пустой список = пароль подходит.
  - `RULE_TEXT: dict[str, str]` — точный текст:
    - `length`: «Не короче 10 символов»
    - `lower`: «Есть строчная латинская буква (a–z)»
    - `upper`: «Есть заглавная латинская буква (A–Z)»
    - `digit`: «Есть цифра»
    - `charset`: «Только латиница, цифры и знаки — без русских букв и пробелов»
  - `generate_temp_password(length: int = 12) -> str` — `secrets`, алфавит без `0 O 1 l I`, всегда проходит `validate_password`.

- [ ] **Step 1: Write the failing tests**

```python
from app.auth.passwords import hash_password, verify_password, validate_password, generate_temp_password

def test_hash_roundtrip():
    h = hash_password("Passw0rd12")
    assert h.startswith("scrypt$") and verify_password("Passw0rd12", h)
    assert not verify_password("Passw0rd13", h)
    assert hash_password("Passw0rd12") != h  # соль

def test_verify_garbage():
    assert verify_password("x", "not-a-hash") is False

def test_policy():
    assert validate_password("Passw0rd12") == []
    assert validate_password("Pass!w0rd#12") == []
    assert validate_password("Pa0") == ["length"]
    assert validate_password("passw0rd12") == ["upper"]
    assert validate_password("PASSW0RD12") == ["lower"]
    assert validate_password("Passwordab") == ["digit"]
    assert validate_password("Passw0rd 12") == ["charset"]

def test_policy_russian_layout():
    assert "charset" in validate_password("Привет12345")
    assert "charset" in validate_password("Passw0rdЯ12")

def test_temp_password():
    for _ in range(200):
        p = generate_temp_password()
        assert len(p) == 12 and validate_password(p) == []
        assert not set(p) & set("0O1lI")
```

- [ ] **Step 2: Run** `pytest tests/test_passwords.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Пароли: scrypt, правила, временный пароль"`

---

### Task 5: Блокировка входа и логины

**Files:**
- Create: `app/auth/login_guard.py`, `app/logins.py`
- Test: `tests/test_login_guard.py`, `tests/test_logins.py`

**Interfaces:**
- Consumes: `LoginAttempt`, `utcnow()`.
- Produces — `app.auth.login_guard`:
  - `MAX_FAILURES = 5`, `BLOCK_FOR = timedelta(minutes=10)`, `IP_MAX_FAILURES = 20`, `IP_WINDOW = timedelta(minutes=10)`.
  - `blocked_until(db, login: str, ip: str, now: datetime | None = None) -> datetime | None` — позднейший из активных блоков ключей `f"{login}|{ip}"` и `f"ip|{ip}"`.
  - `register_failure(db, login: str, ip: str, now=None) -> datetime | None` — увеличивает оба счётчика; счётчик ключа обнуляется, если `last_failure_at` старше 10 минут или его блок уже истёк; возвращает время окончания блока, если он только что наступил или уже действует.
  - `register_success(db, login: str, ip: str) -> None` — удаляет ключ `login|ip` (ключ IP не трогает).
  - `unlock(db, login: str) -> int` — удаляет все ключи `f"{login}|%"`, возвращает число.
  - `reset_all(db) -> None`.
  - Логин в функции приходит уже нормализованным.
- Produces — `app.logins`:
  - `normalize_login(raw: str) -> str` — `strip().lower()`.
  - `is_valid_login(login: str) -> bool` — `^[a-z0-9._-]{3,32}$`.
  - `suggest_login(first_name: str, last_name: str) -> str` — транслитерация `"имя.фамилия"`: а→a б→b в→v г→g д→d е→e ё→e ж→zh з→z и→i й→y к→k л→l м→m н→n о→o п→p р→r с→s т→t у→u ф→f х→kh ц→ts ч→ch ш→sh щ→shch ъ→"" ы→y ь→"" э→e ю→yu я→ya; латиница сохраняется, прочее выкидывается, обрезка до 32.
  - `unique_login(db, base: str) -> str` — `base`, иначе `base2`, `base3`…

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timedelta
from app.auth import login_guard as g

T0 = datetime(2026, 10, 25, 12, 0)

def test_block_after_five(db):
    for i in range(4):
        assert g.register_failure(db, "ivan", "1.1.1.1", now=T0) is None
    until = g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert until == T0 + timedelta(minutes=10)
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=9)) == until
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=10, seconds=1)) is None

def test_other_ip_not_blocked(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.blocked_until(db, "ivan", "2.2.2.2", now=T0) is None

def test_counter_forgets_after_quiet(db):
    for _ in range(4):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=11)) is None

def test_ip_limit_across_logins(db):
    for i in range(20):
        g.register_failure(db, f"user{i}", "3.3.3.3", now=T0)
    assert g.blocked_until(db, "someone", "3.3.3.3", now=T0) == T0 + timedelta(minutes=10)

def test_success_clears_account_key(db):
    for _ in range(4):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    g.register_success(db, "ivan", "1.1.1.1")
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0) is None

def test_unlock(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.unlock(db, "ivan") == 1
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0) is None
```

```python
from app.logins import normalize_login, is_valid_login, suggest_login, unique_login

def test_normalize():
    assert normalize_login("  Ivan.Petrov ") == "ivan.petrov"

def test_valid():
    assert is_valid_login("ivan.petrov") and is_valid_login("a_b-1")
    assert not is_valid_login("iv") and not is_valid_login("иван") and not is_valid_login("a b c")

def test_suggest():
    assert suggest_login("Щука", "Хрущёв") == "shchuka.khrushchev"
    assert suggest_login("Юля", "Ильина") == "yulya.ilina"

def test_unique(db):
    from tests.factories import make_user
    make_user(db, login="ivan.petrov")
    assert unique_login(db, "ivan.petrov") == "ivan.petrov2"
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Блокировка входа по логину+IP и по IP, подсказка логина"`

---

### Task 6: Сессии, права доступа, проверка Origin

**Files:**
- Create: `app/auth/sessions.py`, `app/auth/deps.py`
- Modify: `app/main.py` (middleware Origin, обработчик `LoginRequired`)
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces — `app.auth.sessions`:
  - `COOKIE_NAME = "est_session"`, `SESSION_TTL = timedelta(days=30)`, `TOUCH_EVERY = timedelta(hours=1)` (обновлять `last_seen_at/expires_at` не чаще раза в час — меньше записей).
  - `create_session(db, user: User, ip: str, user_agent: str, now=None) -> str` — сырой токен `secrets.token_urlsafe(32)`, в БД `sha256(token).hexdigest()`.
  - `get_session_user(db, token: str | None, now=None) -> User | None` — `None`, если токена нет, сессия истекла или `user.is_active` ложно.
  - `revoke_session(db, token: str) -> None`.
  - `revoke_user_sessions(db, user_id: int, keep_token: str | None = None) -> int`.
  - `set_session_cookie(response, token: str) -> None` / `clear_session_cookie(response) -> None` — флаги из Global Constraints, `max_age` = 30 дней.
- Produces — `app.auth.deps`:
  - `class LoginRequired(Exception)` → обработчик в `main.py` отвечает `303` на `/login` (для HTMX-запросов — заголовок `HX-Redirect: /login`).
  - `get_current_user(request, db) -> User | None` — кладёт пользователя в `request.state.user`.
  - `require_user(request, db) -> User` — нет пользователя → `LoginRequired`; `must_change_password` и путь не в `{"/password/new", "/logout"}` → `303` на `/password/new` (через исключение `PasswordChangeRequired`, обработчик в `main.py`).
  - `require_role(*roles: str)` — фабрика зависимости; `"admin"` проходит, если в `roles` есть `"teacher"`; иначе `HTTPException(403)`.
  - `home_for(user: User) -> str` — `"/"` для ученика, `"/teacher"` для учителя и админа.
  - `client_ip(request) -> str` — `request.client.host` (за nginx его выставляет `--proxy-headers`).
- Middleware в `main.py`: для POST/PUT/PATCH/DELETE сравнивает схему+хост из `Origin` (или `Referer`) с `settings.base_url`; не совпало или оба заголовка отсутствуют → `403`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import timedelta
from app.auth import sessions as s
from app.timeutil import utcnow
from tests.factories import make_user

def test_create_and_resolve(db):
    u = make_user(db)
    tok = s.create_session(db, u, "1.1.1.1", "ua")
    assert s.get_session_user(db, tok).id == u.id
    assert s.get_session_user(db, "wrong") is None and s.get_session_user(db, None) is None

def test_expiry_and_sliding(db):
    u = make_user(db); now = utcnow()
    tok = s.create_session(db, u, "ip", "ua", now=now)
    assert s.get_session_user(db, tok, now=now + timedelta(days=29)) is not None  # продлилась
    assert s.get_session_user(db, tok, now=now + timedelta(days=29 + 29)) is not None
    assert s.get_session_user(db, tok, now=now + timedelta(days=29 + 29 + 31)) is None

def test_inactive_user_rejected(db):
    u = make_user(db); tok = s.create_session(db, u, "ip", "ua")
    u.is_active = False; db.commit()
    assert s.get_session_user(db, tok) is None

def test_revoke_all_but_current(db):
    u = make_user(db)
    a = s.create_session(db, u, "ip", "ua"); b = s.create_session(db, u, "ip", "ua")
    assert s.revoke_user_sessions(db, u.id, keep_token=a) == 1
    assert s.get_session_user(db, a) and s.get_session_user(db, b) is None

def test_post_foreign_or_missing_origin_forbidden(client):
    from fastapi.testclient import TestClient
    from app.main import app
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert TestClient(app).post("/logout").status_code == 403  # ни Origin, ни Referer
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Сессии в БД, проверка ролей и Origin"`

---

### Task 7: Вход, выход, обязательная смена пароля, главная

**Files:**
- Create: `app/routes/__init__.py`, `app/routes/auth.py`, `app/templates/auth/login.html`, `app/templates/auth/password_new.html`, `app/templates/student/home.html`, `app/templates/partials/password_rules.html`
- Modify: `app/main.py`, `app/static/js/app.js`
- Test: `tests/test_auth_flow.py`

**Interfaces:**
- Consumes: Task 4–6.
- Produces:
  - `GET /login`, `POST /login` (form: `login`, `password`). Успех → `register_success`, `create_session`, cookie, `303` на `home_for(user)` (или `/password/new`). Неудача → `200` со страницей и текстом «Неверный логин или пароль». Блок → `429`, текст «Слишком много попыток. Попробуйте через N мин.» (N — округление вверх). Пароль с нелатинскими символами при неудаче — дополнительная подсказка «Проверьте раскладку: пароль набирается латиницей».
  - `POST /logout` → отзыв сессии, удаление cookie, `303` `/login`.
  - `GET/POST /password/new` (form: `new`, `repeat`) — только для залогиненного; ошибки — тексты `RULE_TEXT` и «Пароли не совпадают»; успех снимает `must_change_password`, `303` на `home_for`.
  - `GET /` — ученику страница-заглушка «Привет, {first_name}! Скоро здесь появятся ежедневные задания.»; учителю/админу — `303` `/teacher`.
  - `partials/password_rules.html` — список правил с `data-rule="<код>"`; `app.js` подсвечивает выполненные правила при вводе (та же логика, что `validate_password`). Переиспользуется в профиле (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
from tests.factories import make_user, login_as

def test_login_success_sets_cookie(client, db):
    make_user(db, login="ivan")
    r = login_as(client, " IVAN ")
    assert r.status_code == 303 and r.headers["location"] == "/"
    c = r.headers["set-cookie"]
    assert "est_session=" in c and "HttpOnly" in c and "samesite=lax" in c.lower()

def test_wrong_password_generic(client, db):
    make_user(db, login="ivan")
    r = login_as(client, "ivan", "Wrong12345a")
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text
    assert "Неверный логин или пароль" in login_as(client, "nobody", "x").text

def test_lockout_case_insensitive(client, db):
    make_user(db, login="ivan")
    for name in ["ivan", "IVAN", "Ivan", "iVan", "ivaN"]:
        login_as(client, name, "Wrong12345a")
    r = login_as(client, "ivan")  # верный пароль, но блок
    assert r.status_code == 429 and "Попробуйте через 10 мин" in r.text

def test_layout_hint(client, db):
    make_user(db, login="ivan")
    assert "Проверьте раскладку" in login_as(client, "ivan", "Пароль12345").text

def test_must_change_flow(client, db):
    make_user(db, login="kid", must_change=True)
    assert login_as(client, "kid").headers["location"] == "/password/new"
    assert client.get("/profile", follow_redirects=False).headers["location"] == "/password/new"
    r = client.post("/password/new", data={"new": "short", "repeat": "short"})
    assert "Не короче 10 символов" in r.text
    r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    client.post("/logout")
    assert login_as(client, "kid", "NewPassw0rd").headers["location"] == "/"

def test_teacher_home_redirect(client, db):
    make_user(db, role="teacher", login="anna")
    login_as(client, "anna")
    assert client.get("/", follow_redirects=False).headers["location"] == "/teacher"

def test_anonymous_redirected(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
```

(`login_as` вызывает `client.post("/login", ..., follow_redirects=False)`.)

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Экран входа — одна карточка: название школы, поля «Логин» и «Пароль» (у пароля кнопка «показать»), большая зелёная кнопка «Войти». Подсказка внизу: «Забыли пароль? Попросите учителя его сбросить».
- [ ] **Step 4: Run** `pytest -q` — PASS; руками в браузере 375px: вход, ошибка, блок, смена пароля.
- [ ] **Step 5: Commit** `git commit -m "Вход, выход и обязательная смена пароля"`

---

### Task 8: Профиль, аватар и защищённые файлы

**Files:**
- Create: `app/images.py`, `app/auth/access.py`, `app/routes/profile.py`, `app/routes/media.py`, `app/templates/profile.html`
- Modify: `app/main.py` (убрать публичный mount `/media`, если был), `app/templating.py` (фильтр `avatar_url`)
- Test: `tests/test_profile.py`, `tests/test_media.py`

**Interfaces:**
- Produces — `app.images`:
  - `MAX_UPLOAD_BYTES = 15 * 1024 * 1024`, `AVATAR_SIZE = 512`.
  - `class InvalidImage(Exception)`.
  - `save_avatar(user_id: int, data: bytes) -> str` — `exif_transpose` → RGB → `ImageOps.fit` 512×512 → WebP q=85; путь `avatars/<user_id>/<token_hex(6)>.webp` относительно `settings.media_dir`; не картинка или больше лимита → `InvalidImage`. `register_heif_opener()` — как в rieltor_site.
  - `delete_media(rel_path: str | None) -> None`.
- Produces — `app.auth.access.can_view_avatar(viewer: User, owner: User) -> bool` — `True`, если: это он сам; `viewer` — админ; `owner` — ученик группы, учитель которой `viewer`; `viewer` — ученик, а `owner` — его одногруппник или его учитель.
- Produces — маршруты:
  - `GET /profile` — фото, имя, логин, формы смены пароля и аватара, кнопка «Выйти».
  - `POST /profile/password` (form: `current`, `new`, `repeat`) — ошибки: «Текущий пароль указан неверно», `RULE_TEXT`, «Пароли не совпадают», «Новый пароль совпадает с текущим»; успех — `revoke_user_sessions(keep_token=текущий)`, сообщение «Пароль изменён».
  - `POST /profile/avatar` (file: `photo`) — ошибка «Не получилось открыть картинку. Попробуйте другое фото.»; старый файл удаляется.
  - `POST /profile/avatar/delete`.
  - `GET /media/avatars/{user_id}/{name}` — нет прав → 404; `DEBUG=true` → `FileResponse`; иначе пустой ответ с заголовком `X-Accel-Redirect: /_protected/avatars/<user_id>/<name>`; всегда `Cache-Control: private, max-age=86400`.
  - Фильтр шаблонов `avatar_url(user) -> str | None` → `/media/<user.avatar>`; без аватара шаблон рисует кружок с инициалами.

- [ ] **Step 1: Write the failing tests**

```python
from io import BytesIO
from PIL import Image
from tests.factories import make_user, make_group, login_as

def jpeg(w=3000, h=2000):
    b = BytesIO(); Image.new("RGB", (w, h), "red").save(b, "JPEG"); return b.getvalue()

def test_avatar_upload_square_webp(client, db):
    u = make_user(db, login="kid"); login_as(client, "kid")
    r = client.post("/profile/avatar", files={"photo": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code in (200, 303)
    db.refresh(u)
    from app.config import settings
    img = Image.open(settings.media_dir / u.avatar)
    assert img.format == "WEBP" and img.size == (512, 512) and not img.getexif()

def test_avatar_not_image(client, db):
    make_user(db, login="kid"); login_as(client, "kid")
    r = client.post("/profile/avatar", files={"photo": ("a.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 200 and "Не получилось открыть картинку" in r.text

def test_avatar_too_big(client, db):
    make_user(db, login="kid"); login_as(client, "kid")
    r = client.post("/profile/avatar", files={"photo": ("a.jpg", b"0" * (15 * 1024 * 1024 + 1), "image/jpeg")})
    assert r.status_code == 200 and "Не получилось открыть картинку" in r.text

def test_change_password_revokes_other_sessions(client, db):
    from fastapi.testclient import TestClient
    from app.main import app
    make_user(db, login="kid")
    other = TestClient(app, headers={"Origin": "http://testserver"}); login_as(other, "kid")
    login_as(client, "kid")
    r = client.post("/profile/password", data={"current": "Passw0rd12", "new": "NewPassw0rd", "repeat": "NewPassw0rd"})
    assert "Пароль изменён" in r.text
    assert other.get("/profile", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/profile").status_code == 200

def test_avatar_visibility(client, db):
    t = make_user(db, role="teacher", login="t1"); t2 = make_user(db, role="teacher", login="t2")
    g = make_group(db, t); g2 = make_group(db, t2)
    a = make_user(db, login="a", group=g); b = make_user(db, login="b", group=g)
    stranger = make_user(db, login="x", group=g2)
    login_as(client, "a")
    client.post("/profile/avatar", files={"photo": ("a.jpg", jpeg(), "image/jpeg")})
    db.refresh(a); url = f"/media/{a.avatar}"
    client.post("/logout")
    assert client.get(url, follow_redirects=False).status_code in (303, 404)  # аноним
    for who, ok in [("b", True), ("t1", True), ("x", False), ("t2", False)]:
        login_as(client, who)
        assert (client.get(url).status_code == 200) is ok, who
        client.post("/logout")

def test_prod_uses_x_accel(client, db, monkeypatch):
    from app.config import settings
    u = make_user(db, login="kid"); login_as(client, "kid")
    client.post("/profile/avatar", files={"photo": ("a.jpg", jpeg(), "image/jpeg")})
    db.refresh(u)
    monkeypatch.setattr(settings, "debug", False)
    r = client.get(f"/media/{u.avatar}")
    assert r.headers["x-accel-redirect"] == f"/_protected/{u.avatar}"
    assert r.headers["cache-control"].startswith("private")
```

Плюс тест HEIC: если `pillow_heif` умеет писать HEIF в окружении, сохранить картинку в HEIF и загрузить — ожидается успех; иначе `pytest.skip`.

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Размер загрузки проверять до чтения в память целиком (`UploadFile` читать порциями до `MAX_UPLOAD_BYTES + 1`).
- [ ] **Step 4: Run** `pytest -q` — PASS; руками в браузере: загрузка фото с телефона (эмуляция 375px).
- [ ] **Step 5: Commit** `git commit -m "Профиль, аватар и выдача файлов с проверкой прав"`

---

### Task 9: Учитель — группы и ученики

**Files:**
- Create: `app/routes/teacher.py`, `app/templates/teacher/groups.html`, `app/templates/teacher/group.html`, `app/templates/teacher/student_form.html`, `app/templates/teacher/student.html`, `app/templates/teacher/temp_password.html`, `app/templates/partials/login_suggest.html`
- Modify: `app/main.py`
- Test: `tests/test_teacher.py`

**Interfaces:**
- Consumes: `require_role("teacher")`, `generate_temp_password`, `hash_password`, `suggest_login`, `unique_login`, `normalize_login`, `is_valid_login`, `revoke_user_sessions`, `login_guard.unlock`.
- Produces:
  - `own_group(db, user: User, group_id: int) -> Group` и `own_student(db, user: User, student_id: int) -> User` — чужие (не своего учителя; для админа — любые) → `HTTPException(404)`.
  - `GET /teacher` — список групп учителя (у админа — все группы с именем учителя) + форма «Новая группа».
  - `POST /teacher/groups` (`name`), `POST /teacher/groups/{id}/rename` (`name`); пустое имя → «Введите название группы».
  - `GET /teacher/groups/{id}` — ученики (аватар, имя, логин, бейдж «заблокирован»/«не активен»).
  - `GET /teacher/groups/{id}/students/new`, `POST /teacher/groups/{id}/students` (`first_name`, `last_name`, `login`) — успех: ученик с `must_change_password=True`, страница `temp_password.html`: «Логин: … Временный пароль: …» и «Запишите пароль — больше он не покажется». Ошибки: «Логин уже занят», «Логин: латинские буквы, цифры, точка, дефис, подчёркивание; от 3 до 32 символов», «Введите имя и фамилию».
  - `GET /teacher/login-suggest?first_name=&last_name=` — HTMX-фрагмент с `unique_login(suggest_login(...))` для подстановки в поле логина.
  - `GET /teacher/students/{id}`; `POST /teacher/students/{id}/edit` (`first_name`, `last_name`, `group_id` — только из групп этого учителя); `POST .../reset-password` → новый временный пароль на `temp_password.html`, `must_change_password=True`, все сессии ученика отозваны; `POST .../unlock` → `login_guard.unlock`; `POST .../deactivate` → `is_active=False` + отзыв сессий; `POST .../activate`.
  - Деструктивные кнопки — с `hx-confirm` / `onsubmit="return confirm(...)"`.

- [ ] **Step 1: Write the failing tests**

```python
import re
from tests.factories import make_user, make_group, login_as
from app.models import User

def setup_teacher(client, db, login="t1"):
    t = make_user(db, role="teacher", login=login); g = make_group(db, t)
    login_as(client, login); return t, g

def test_create_student_shows_temp_password_once(client, db):
    t, g = setup_teacher(client, db)
    r = client.post(f"/teacher/groups/{g.id}/students", data={"first_name": "Петя", "last_name": "Котов", "login": "petya.kotov"})
    assert r.status_code == 200 and "больше он не покажется" in r.text
    pwd = re.search(r'data-temp-password="([^"]+)"', r.text).group(1)
    s = db.query(User).filter_by(login="petya.kotov").one()
    assert s.group_id == g.id and s.must_change_password and s.role == "student"
    client.post("/logout")
    assert login_as(client, "petya.kotov", pwd).headers["location"] == "/password/new"

def test_duplicate_login_friendly(client, db):
    t, g = setup_teacher(client, db)
    data = {"first_name": "Петя", "last_name": "Котов", "login": "petya"}
    client.post(f"/teacher/groups/{g.id}/students", data=data)
    r = client.post(f"/teacher/groups/{g.id}/students", data=data)
    assert r.status_code == 200 and "Логин уже занят" in r.text
    assert db.query(User).filter_by(login="petya").count() == 1

def test_login_suggest(client, db):
    t, g = setup_teacher(client, db)
    make_user(db, login="petya.kotov")
    r = client.get("/teacher/login-suggest", params={"first_name": "Петя", "last_name": "Котов"})
    assert "petya.kotov2" in r.text

def test_foreign_group_404(client, db):
    other = make_user(db, role="teacher", login="t2"); g2 = make_group(db, other)
    s2 = make_user(db, login="s2", group=g2)
    setup_teacher(client, db)
    assert client.get(f"/teacher/groups/{g2.id}").status_code == 404
    assert client.post(f"/teacher/students/{s2.id}/reset-password").status_code == 404

def test_student_cannot_open_teacher(client, db):
    make_user(db, login="kid"); login_as(client, "kid")
    assert client.get("/teacher").status_code == 403

def test_reset_password_kicks_student(client, db):
    from fastapi.testclient import TestClient
    from app.main import app
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    kid = TestClient(app, headers={"Origin": "http://testserver"}); login_as(kid, "kid")
    client.post(f"/teacher/students/{s.id}/reset-password")
    db.refresh(s)
    assert s.must_change_password
    assert kid.get("/", follow_redirects=False).headers["location"] == "/login"

def test_deactivate_logs_out_immediately(client, db):
    from fastapi.testclient import TestClient
    from app.main import app
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    kid = TestClient(app, headers={"Origin": "http://testserver"}); login_as(kid, "kid")
    client.post(f"/teacher/students/{s.id}/deactivate")
    assert kid.get("/", follow_redirects=False).headers["location"] == "/login"
    assert login_as(kid, "kid").status_code == 200  # «Неверный логин или пароль»

def test_unlock(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    from app.auth import login_guard
    for _ in range(5):
        login_guard.register_failure(db, "kid", "testclient")
    client.post(f"/teacher/students/{s.id}/unlock")
    assert login_guard.blocked_until(db, "kid", "testclient") is None

def test_move_only_to_own_group(client, db):
    t, g = setup_teacher(client, db)
    g_own2 = make_group(db, t, name="Группа 2")
    other = make_user(db, role="teacher", login="t2"); g_foreign = make_group(db, other)
    s = make_user(db, login="kid", group=g)
    client.post(f"/teacher/students/{s.id}/edit", data={"first_name": "Петя", "last_name": "К", "group_id": g_foreign.id})
    db.refresh(s); assert s.group_id == g.id
    client.post(f"/teacher/students/{s.id}/edit", data={"first_name": "Петя", "last_name": "К", "group_id": g_own2.id})
    db.refresh(s); assert s.group_id == g_own2.id
```

(Шаблон `temp_password.html` выводит пароль в элементе с `data-temp-password="..."` и классом `.temp-password`.)

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Создание ученика — в одной транзакции; `IntegrityError` по логину ловится и превращается в «Логин уже занят» (на случай двойного нажатия). На экране группы — крупная кнопка «Добавить ученика»; форма подставляет логин через `hx-get` по `change` полей имени и фамилии.
- [ ] **Step 4: Run** `pytest -q` — PASS; руками в браузере 375px: создать группу, ученика, войти учеником с временным паролем, сменить пароль, сбросить пароль учителем.
- [ ] **Step 5: Commit** `git commit -m "Учитель: группы и ученики, сброс пароля, разблокировка"`

---

### Task 10: Админ — раздел «Школа» и sqladmin

**Files:**
- Create: `app/routes/school.py`, `app/templates/school/index.html`, `app/templates/school/teachers.html`, `app/admin.py`, `app/templates/sqladmin/login.html`
- Modify: `app/main.py`
- Test: `tests/test_school.py`

**Interfaces:**
- Consumes: `require_role("admin")`, функции паролей и логинов, `temp_password.html` из Task 9.
- Produces:
  - `GET /school` — карточки разделов (в этом плане только «Учителя»; виды штампиков и словарь фильтра добавят следующие планы).
  - `GET /school/teachers`, `POST /school/teachers` (`first_name`, `last_name`, `login`) → временный пароль; `POST /school/teachers/{id}/reset-password`, `/deactivate`, `/activate` — те же правила и тексты, что у учеников.
  - `setup_admin(app)` — sqladmin на `/admin`, `AuthenticationBackend` пускает только по нашей сессии с ролью `admin` (иначе редирект на `/login`); представления `User` (без `password_hash` в формах), `Group`.

- [ ] **Step 1: Write the failing tests**

```python
from tests.factories import make_user, login_as
from app.models import User

def test_admin_creates_teacher(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    r = client.post("/school/teachers", data={"first_name": "Анна", "last_name": "Смирнова", "login": "anna"})
    assert "больше он не покажется" in r.text
    t = db.query(User).filter_by(login="anna").one()
    assert t.role == "teacher" and t.must_change_password

def test_teacher_cannot_open_school(client, db):
    make_user(db, role="teacher", login="t1"); login_as(client, "t1")
    assert client.get("/school").status_code == 403
    assert client.get("/admin/", follow_redirects=False).status_code in (302, 303)

def test_admin_has_teacher_powers(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    assert client.get("/teacher").status_code == 200
    r = client.post("/teacher/groups", data={"name": "Моя группа"}, follow_redirects=True)
    assert "Моя группа" in r.text

def test_admin_nav_has_school(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    assert 'href="/school"' in client.get("/teacher").text
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Админ: раздел Школа, учителя, sqladmin"`

---

### Task 11: CLI

**Files:**
- Create: `app/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str]) -> int` (argparse, запуск `python -m app.cli ...`); докстринг модуля — справка по командам, как в rieltor_site.
  - `create-admin --login L --first-name F --last-name N` — создаёт админа с временным паролем (`must_change_password=True`), печатает `Логин: L` и `Временный пароль: …`; занятый логин → сообщение и код 1.
  - `reset-password --login L` — новый временный пароль, `must_change_password=True`, отзыв сессий, печать пароля.
  - `unlock --login L` / `unlock --all` — `login_guard.unlock` / `reset_all`.

- [ ] **Step 1: Write the failing tests**

```python
import re
from app.cli import main
from app.models import User
from app.auth.passwords import verify_password

def test_create_admin(db, capsys):
    assert main(["create-admin", "--login", "Boss", "--first-name", "Ольга", "--last-name", "Иванова"]) == 0
    pwd = re.search(r"Временный пароль: (\S+)", capsys.readouterr().out).group(1)
    u = db.query(User).filter_by(login="boss").one()
    assert u.role == "admin" and u.must_change_password and verify_password(pwd, u.password_hash)
    assert main(["create-admin", "--login", "boss", "--first-name", "О", "--last-name", "И"]) == 1

def test_reset_password(db, capsys):
    from tests.factories import make_user
    make_user(db, login="kid")
    assert main(["reset-password", "--login", "kid"]) == 0
    pwd = re.search(r"Временный пароль: (\S+)", capsys.readouterr().out).group(1)
    db.expire_all(); u = db.query(User).filter_by(login="kid").one()
    assert verify_password(pwd, u.password_hash) and u.must_change_password
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** `git commit -m "CLI: создать админа, сбросить пароль, снять блокировку"`

---

### Task 12: Деплой и README

**Files:**
- Create: `deploy/englishst.service`, `deploy/nginx.conf`, `deploy/backup.sh`, `deploy/update.sh`, `README.md`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Produces:
  - `englishst.service` — как `rieltor.service`: `User/Group=englishst`, `WorkingDirectory=/srv/englishst`, `ExecStart=/srv/englishst/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1`, те же ограничения, `ReadWritePaths=/srv/englishst/data /srv/englishst/media`; комментарий, почему ровно один воркер (чат в памяти процесса, этап 3).
  - `nginx.conf` — зоны `limit_req` с префиксом `englishst_` (вход 5r/m, burst 5); `upstream` на `127.0.0.1:8002`; `client_max_body_size 16m`; `location /static/` — alias, `expires 30d`; `location /_protected/ { internal; alias /srv/englishst/media/; }`; `location = /login` с `limit_req`; `location /ws` — `proxy_http_version 1.1`, `Upgrade`/`Connection "upgrade"`, `proxy_read_timeout 3600s` (заготовка для этапа 3); заголовки безопасности как у rieltor_site + `Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"`; отдельный `server` для `english-st.online www.english-st.online` → `return 301 https://english-st.ru$request_uri`; `server_name english-st.ru www.english-st.ru`; комментарий с командой certbot на все четыре имени.
  - `backup.sh` — как у rieltor_site: онлайн-бэкап SQLite + `media`, `/var/backups/englishst`, 14 дней.
  - `update.sh` — `set -eu; cd /srv/englishst; sudo -u englishst git pull --ff-only; sudo -u englishst .venv/bin/pip install -r requirements.txt; sudo -u englishst .venv/bin/alembic upgrade head; systemctl restart englishst; systemctl is-active englishst`.
  - `README.md` — по образцу rieltor_site: что есть; запуск локально на Windows (`py -3.12 -m venv .venv`, `pip install -r requirements-dev.txt`, `alembic upgrade head`, `python -m app.cli create-admin ...`, `uvicorn app.main:app --reload --port 8002`); тесты; деплой на VDS по шагам (пользователь `englishst`, клонирование, `.env`, `alembic upgrade head`, `create-admin`, systemd, nginx рядом с соседними сайтами — не удалять их конфиги и не трогать `default`, если он уже удалён; certbot; cron бэкапа); обновление — `sudo /srv/englishst/deploy/update.sh`; CLI-команды; структура.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def test_service_single_worker_port():
    s = (ROOT / "deploy/englishst.service").read_text(encoding="utf-8")
    assert "--port 8002" in s and "--workers 1" in s and "User=englishst" in s

def test_nginx_protected_media_and_redirect():
    n = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
    assert "internal;" in n and "/_protected/" in n
    assert "127.0.0.1:8002" in n and "english-st.online" in n and "return 301 https://english-st.ru" in n
    assert "location /media/" not in n  # медиа только через X-Accel-Redirect

def test_update_runs_migrations():
    u = (ROOT / "deploy/update.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in u and "git pull" in u
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` — PASS. Пройти README «Запуск локально» с нуля в чистой папке: всё поднимается по инструкции.
- [ ] **Step 5: Commit** `git commit -m "Файлы деплоя и README"`

---

### Task 13: Финальная проверка этапа

- [ ] **Step 1:** `.venv\Scripts\python -m pytest -q` — все тесты зелёные.
- [ ] **Step 2:** Локально поднять сайт, в браузере в размере 375×812 пройти сценарии: админ создан через CLI → вход → смена пароля → «Школа» → учитель → вход учителем → группа → ученик → вход учеником → смена пароля → аватар → 5 неверных паролей → блок → учитель снимает блок → деактивация. Повторить на ширине ≥900px.
- [ ] **Step 3:** Отчитаться заказчику: список пройденных сценариев и что не проверено (живой сервер, реальный iPhone).

---

## Покрытие спеки

| Спека | Задача |
|---|---|
| §5.1 роли, имя/фамилия/аватар/пароль | 3, 7, 8, 9, 10 |
| §5.1 учитель создаёт учеников, временный пароль, смена при первом входе | 9, 7 |
| §5.1 сброс пароля учителем/админом | 9, 10, 11 |
| §5.1 1 ученик = 1 группа = 1 учитель | 3, 9 |
| §5.1 правила пароля | 4, 7, 8 |
| §5.1/§8.1 блокировка логин+IP и по IP, снятие блока | 5, 7, 9, 11 |
| §8.1 сессии 30 дней, отзыв при смене пароля и деактивации | 6, 8, 9 |
| §8.1 подсказка логина транслитом | 5, 9 |
| §8.1 имя меняет учитель, ученик — фото и пароль | 8, 9 |
| §8.1 первый админ через CLI; админ ⊇ учитель | 11, 6, 10 |
| §9 мобильный первым, стиль Duolingo, анимации кнопок, View Transitions | 2 |
| §9 «Школа» у админа, sqladmin | 10 |
| §10 права, Origin, загрузки, X-Accel для всех файлов, нет /docs, секреты в проде | 6, 8, 1, 12 |
| §11 деплой как у соседних сайтов, порт 8002, 1 воркер, update.sh, backup.sh | 12 |

**Отложено в следующие планы:** проверка имён фильтром мата (§8.5 — появится с фильтром в плане чата), PWA-манифест и service worker (этап 6), вкладки «Виды штампиков» и «Словарь фильтра» в «Школе» (планы штампиков и чата).
