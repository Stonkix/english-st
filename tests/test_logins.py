from app.logins import is_valid_login, normalize_login, suggest_login, unique_login
from tests.factories import make_user


def test_normalize():
    assert normalize_login("  Ivan.Petrov ") == "ivan.petrov"


def test_valid():
    assert is_valid_login("ivan.petrov") and is_valid_login("a_b-1")
    assert not is_valid_login("iv") and not is_valid_login("иван") and not is_valid_login("a b c")


def test_suggest():
    assert suggest_login("Щука", "Хрущёв") == "shchuka.khrushchev"
    assert suggest_login("Юля", "Ильина") == "yulya.ilina"


def test_suggest_latin_is_lowercased_and_junk_dropped():
    assert suggest_login("John", "O'Neil Jr.") == "john.oneiljr"


def test_suggest_empty_part_has_no_dot():
    assert suggest_login("Иван", "ъь") == "ivan"
    assert suggest_login("???", "Петров") == "petrov"
    assert suggest_login("", "") == ""
    assert suggest_login("日本", "!!!") == ""


def test_suggest_trimmed_to_32():
    assert len(suggest_login("Александра" * 5, "Хрущёв" * 5)) == 32


def test_unique(db):
    make_user(db, login="ivan.petrov")
    assert unique_login(db, "ivan.petrov") == "ivan.petrov2"


def test_unique_free_and_next_suffix(db):
    assert unique_login(db, "ivan.petrov") == "ivan.petrov"
    make_user(db, login="ivan.petrov")
    make_user(db, login="ivan.petrov2")
    assert unique_login(db, "ivan.petrov") == "ivan.petrov3"


def test_unique_trims_base_to_fit_32(db):
    base = "a" * 32
    make_user(db, login=base)
    result = unique_login(db, base)
    assert result == "a" * 31 + "2"
    assert len(result) == 32
