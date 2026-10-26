import pytest


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_no_openapi(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_prod_refuses_placeholder_secret():
    from app.config import Settings
    from app.main import check_production_secrets

    with pytest.raises(RuntimeError):
        check_production_secrets(Settings(debug=False, secret_key="change-me"))
    check_production_secrets(Settings(debug=False, secret_key="x" * 48))  # не бросает


@pytest.mark.parametrize("secret", ["", "   "])
def test_prod_refuses_empty_secret(secret):
    from app.config import Settings
    from app.main import check_production_secrets

    with pytest.raises(RuntimeError):
        check_production_secrets(Settings(debug=False, secret_key=secret))
