import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_service_worker_served_from_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    # кэшируется только статика; /media/ (фото детей) и страницы — никогда
    assert '!url.pathname.startsWith("/static/")' in r.text


def test_manifest_and_icons():
    manifest = json.loads((ROOT / "app/static/manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert (ROOT / "app" / icon["src"].lstrip("/")).is_file()
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])


def test_pages_link_manifest(client):
    html = client.get("/login").text
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in html and "apple-touch-icon" in html
