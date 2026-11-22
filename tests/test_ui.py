from tests.factories import login_as, make_user


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
    assert r.status_code == 404 and "Page not found" in r.text


def test_error_page_shows_code(client):
    r = client.post("/healthz")  # метод не разрешён
    assert r.status_code == 405 and "405" in r.text and 'href="/"' in r.text


def test_ui_kit_shows_nav_for_every_role(client):
    r = client.get("/ui-kit")
    assert 'class="tabbar"' in r.text
    for href in ('href="/profile"', 'href="/teacher"', 'href="/school"'):
        assert href in r.text


def test_top_level_tabs_keep_navigation_outside_htmx_target(client, db):
    make_user(db, login="boss", role="admin")
    login_as(client, "boss")
    page = client.get("/school").text
    assert 'data-tab-navigation hx-boost="true" hx-target="#main" hx-select="#main"' in page
    assert 'href="/school" aria-current="page"' in page
    assert 'hx-select-oob="#chat-nav-count,#orders-nav-count"' in page
    assert page.index('<nav class="tabbar"') < page.index('<main id="main" tabindex="-1">')
    assert 'id="chat-nav-count"' in page and 'id="orders-nav-count"' in page
    assert "data-tab-navigation" not in client.get("/school/teachers").text


def test_htmx_tab_navigation_redirects_after_session_ends(client):
    response = client.get("/profile", headers={"HX-Request": "true", "HX-Boosted": "true"})
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login"
