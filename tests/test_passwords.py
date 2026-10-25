import pytest

from app.auth.passwords import (
    RULE_TEXT,
    generate_temp_password,
    hash_password,
    validate_password,
    verify_password,
)


def test_hash_roundtrip():
    h = hash_password("Passw0rd12")
    assert h.startswith("scrypt$") and verify_password("Passw0rd12", h)
    assert not verify_password("Passw0rd13", h)
    assert hash_password("Passw0rd12") != h  # соль


def test_verify_garbage():
    assert verify_password("x", "not-a-hash") is False


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "scrypt$",
        "scrypt$abc",
        "md5$00$00",
        "scrypt$zz$zz",
        "scrypt$00$",
        "scrypt$$00",
        "scrypt$00$00$extra",
    ],
)
def test_verify_malformed_stored(stored):
    assert verify_password("Passw0rd12", stored) is False


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


def test_policy_codes_order():
    assert validate_password("Пр") == ["length", "lower", "upper", "digit", "charset"]


def test_rule_text():
    assert RULE_TEXT == {
        "length": "Не короче 10 символов",
        "lower": "Есть строчная латинская буква (a–z)",
        "upper": "Есть заглавная латинская буква (A–Z)",
        "digit": "Есть цифра",
        "charset": "Только латиница, цифры и знаки — без русских букв и пробелов",
    }


def test_temp_password():
    for _ in range(200):
        p = generate_temp_password()
        assert len(p) == 12 and validate_password(p) == []
        assert not set(p) & set("0O1lI")


def test_temp_password_too_short():
    with pytest.raises(ValueError):
        generate_temp_password(9)


def test_scrypt_concurrency_is_capped(monkeypatch):
    """scrypt занимает 16 МиБ на вызов: одновременно считается не больше двух хешей."""
    import threading
    import time

    from app.auth import passwords

    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def slow_scrypt(password, **kwargs):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return b"\x00" * 32

    monkeypatch.setattr(passwords.hashlib, "scrypt", slow_scrypt)
    threads = [threading.Thread(target=passwords.hash_password, args=("Passw0rd12",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["peak"] == 2
