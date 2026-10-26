"""Пароли: хеш scrypt, правила сложности и временный пароль."""

import hashlib
import hmac
import re
import secrets
import string
import threading

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_SALT_BYTES = 16
_SCHEME = "scrypt"

MIN_LENGTH = 10
MAX_LENGTH = 256  # длиннее не принимаем ни при смене пароля, ни при входе (защита от огромных запросов)

RULE_TEXT = {
    "length": "Не короче 10 символов",
    "lower": "Есть строчная латинская буква (a–z)",
    "upper": "Есть заглавная латинская буква (A–Z)",
    "digit": "Есть цифра",
    "charset": "Только латиница, цифры и знаки — без русских букв и пробелов",
}

# Без похожих символов (0 O 1 l I), чтобы временный пароль легко читался
_TEMP_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


# scrypt n=2^14, r=8 — 16 МиБ на вызов, GIL при этом отпускается: без ограничения 40 потоков съели бы ~640 МиБ
_HASH_SLOTS = threading.BoundedSemaphore(2)


def _scrypt(password: str, salt: bytes) -> bytes:
    data = password.encode()  # не кодируется в UTF-8 — ошибка до захвата слота
    with _HASH_SLOTS:
        return hashlib.scrypt(data, salt=salt, **_SCRYPT)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    return f"{_SCHEME}${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Сверяет пароль с сохранённым хешем. Любой испорченный хеш даёт False, а не исключение."""
    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != _SCHEME:
        return False
    try:
        salt = bytes.fromhex(parts[1])
        expected = bytes.fromhex(parts[2])
    except ValueError:
        return False
    if not salt or not expected:
        return False
    return hmac.compare_digest(_scrypt(password, salt), expected)


def check_password(password: str, stored: str) -> bool:
    """Слишком длинный или не кодируемый в UTF-8 пароль — просто неверный пароль, а не ошибка сервера.

    Пробелы по краям отбрасываются: в пароле их быть не может (только символы \x21–\x7E), а при копировании
    временного пароля из консоли или сообщения они легко прихватываются.
    """
    password = password.strip()
    if len(password) > MAX_LENGTH:
        return False
    try:
        return verify_password(password, stored)
    except (UnicodeError, ValueError):
        return False


def validate_password(password: str) -> list[str]:
    """Коды невыполненных правил; пустой список — пароль подходит."""
    checks = {
        "length": len(password) >= MIN_LENGTH,
        "lower": re.search(r"[a-z]", password) is not None,
        "upper": re.search(r"[A-Z]", password) is not None,
        "digit": re.search(r"[0-9]", password) is not None,
        "charset": re.fullmatch(r"[\x21-\x7E]*", password) is not None,
    }
    return [code for code, ok in checks.items() if not ok]


def new_password_errors(new: str, repeat: str) -> list[str]:
    """Тексты ошибок для формы «новый пароль + повтор»; пустой список — можно сохранять."""
    errors = [RULE_TEXT[code] for code in validate_password(new)]
    if len(new) > MAX_LENGTH:
        errors.append(f"Не длиннее {MAX_LENGTH} символов")
    if new != repeat:
        errors.append("Пароли не совпадают")
    return errors


def generate_temp_password(length: int = 12) -> str:
    if length < MIN_LENGTH:
        raise ValueError(f"Временный пароль короче {MIN_LENGTH} символов не подойдёт правилам")
    lower = [c for c in _TEMP_ALPHABET if c.islower()]
    upper = [c for c in _TEMP_ALPHABET if c.isupper()]
    digits = [c for c in _TEMP_ALPHABET if c.isdigit()]
    # Гарантируем по одному символу каждого класса, остальное — из общего алфавита
    chars = [secrets.choice(lower), secrets.choice(upper), secrets.choice(digits)]
    chars += [secrets.choice(_TEMP_ALPHABET) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
