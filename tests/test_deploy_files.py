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
    assert "rate=20r/m" in n and "zone=englishst_login burst=20" in n
    assert "limit_req_status 429;" in n and "error_page 429 /429.html;" in n
    assert "<script" not in _read("app/static/429.html")
    assert "frame-ancestors 'none'" in n
    assert "alias /srv/englishst/media/;" in n


def test_update_runs_migrations():
    u = _read("deploy/update.sh")
    assert "alembic upgrade head" in u and "git pull" in u


def test_backup_and_env_example():
    assert "/var/backups/englishst" in _read("deploy/backup.sh")
    env = _read(".env.example")
    for name in ("DEBUG", "BASE_URL", "SECRET_KEY", "DATABASE_URL", "MEDIA_DIR"):
        assert name in env
