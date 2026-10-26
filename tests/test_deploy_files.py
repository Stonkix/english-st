from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_service_single_worker_port():
    s = _read("deploy/englishst.service")
    assert "UMask=0027" in s
    assert "--port 8002" in s and "--workers 1" in s and "User=englishst" in s
    assert "ReadWritePaths=/srv/englishst/data /srv/englishst/media" in s


def test_nginx_protected_media_and_redirect():
    n = _read("deploy/nginx.conf")
    assert "internal;" in n and "/_protected/" in n
    assert "127.0.0.1:8002" in n and "english-st.online" in n and "return 301 https://english-st.ru" in n
    assert "location /media/" not in n  # медиа только через X-Accel-Redirect


def test_nginx_limits_and_headers():
    n = _read("deploy/nginx.conf")
    assert "client_max_body_size 11m" in n
    assert "zone=englishst_login" in n and "zone=englishst_upload" in n
    assert "location /ws/ {" in n and "proxy_read_timeout 3600s" in n
    assert "zone=englishst_editor:10m rate=30r/m" in n and "zone=englishst_editor burst=10" in n
    assert "client_max_body_size 52m" in n
    editor = n[n.index("location ~ ^/tasks/"):]
    assert "englishst_editor" in editor.split("}")[0] and "englishst_upload" not in editor.split("}")[0]
    assert "rate=20r/m" in n and "zone=englishst_login burst=20" in n
    assert "limit_req_status 429;" in n and "error_page 429 /429.html;" in n
    assert "<script" not in _read("app/static/429.html")
    assert "frame-ancestors 'none'" in n
    assert "alias /srv/englishst/media/;" in n


def test_429_page_is_neutral():
    """429 отдаётся на любой адрес (не только на вход): текст не про вход, ссылка на главную."""
    p = _read("app/static/429.html")
    assert "Слишком много запросов. Подождите минуту и попробуйте снова." in p
    assert 'href="/"' in p and "/login" not in p and "входа" not in p
    assert "/static/css/app.css?v=" in p


def test_update_runs_migrations():
    u = _read("deploy/update.sh")
    assert "alembic upgrade head" in u and "git pull" in u


def test_backup_runs_python_as_app_user_and_checks_db():
    b = _read("deploy/backup.sh")
    guard = b.index('[ -f "$APP/data/site.db" ]')
    run = b.index("runuser -u englishst --")
    assert guard < run < b.index("import sqlite3")  # проверка файла раньше, чем sqlite что-то откроет
    assert "exit 1" in b[guard:run] and "trap " in b
    assert "mv " in b and "chown root:root" in b  # готовый файл переезжает в $DEST уже от root


def test_pip_installs_use_constraints():
    assert "-r requirements.txt -c constraints.txt" in _read("deploy/update.sh")
    assert "-r requirements.txt -c constraints.txt" in _read("README.md")
    assert "-c constraints.txt" in _read("README.md").split("requirements-dev.txt")[1].split("\n")[0]
    pins = [l for l in _read("constraints.txt").splitlines() if l and not l.startswith("#")]
    names = {l.split("==")[0].lower() for l in pins}
    assert all("==" in l for l in pins)
    for pkg in ("fastapi", "sqlalchemy", "sqladmin", "pillow", "pillow_heif", "uvicorn", "alembic"):
        assert pkg in names


def test_backup_and_env_example():
    assert "/var/backups/englishst" in _read("deploy/backup.sh")
    env = _read(".env.example")
    for name in ("DEBUG", "BASE_URL", "SECRET_KEY", "DATABASE_URL", "MEDIA_DIR"):
        assert name in env


def test_nginx_chat():
    n = _read("deploy/nginx.conf")
    chat = n[n.index("location ~ ^/chat/[0-9]+/image$"):]
    chat = chat[:chat.index("}")]
    assert "client_max_body_size 16m" in chat and "zone=englishst_chat" in chat
    assert n.count("connect-src 'self' wss://english-st.ru") == 2  # основная CSP и CSP админки
    ws = n[n.index("location /ws/ {"):]
    assert "Upgrade $http_upgrade" in ws.split("}")[0]


def test_nginx_shop_items_limited():
    n = _read("deploy/nginx.conf")
    block = n[n.index("location ~ ^/shop/"):]
    assert "zone=englishst_editor" in block[:block.index("}")]
