def test_static_css(client):
    assert client.get("/static/css/app.css").status_code == 200


def test_ui_kit_in_debug(client):
    r = client.get("/ui-kit")
    assert r.status_code == 200 and "btn-primary" in r.text


def test_ui_kit_hidden_in_prod(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "debug", False)
    assert client.get("/ui-kit").status_code == 404


def test_404_page(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "Страница не найдена" in r.text


def test_error_page_shows_code(client):
    r = client.post("/healthz")  # метод не разрешён
    assert r.status_code == 405 and "405" in r.text and 'href="/"' in r.text


def test_ui_kit_shows_nav_for_every_role(client):
    r = client.get("/ui-kit")
    assert 'class="tabbar"' in r.text
    for href in ('href="/profile"', 'href="/teacher"', 'href="/school"'):
        assert href in r.text
