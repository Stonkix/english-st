"""Логины: нормализация, проверка формата, подсказка по имени, уникальность."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MAX_LEN = 32

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}  # fmt: skip


def normalize_login(raw: str) -> str:
    return raw.strip().lower()


def is_valid_login(login: str) -> bool:
    return bool(LOGIN_RE.fullmatch(login))


def _translit(text: str) -> str:
    """Кириллица → латиница; всё, что после этого не a-z0-9, выбрасывается."""
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    return re.sub(r"[^a-z0-9]", "", latin)


def suggest_login(first_name: str, last_name: str) -> str:
    """«имя.фамилия» латиницей. Если одна часть пуста — только другая; если обе — пустая строка."""
    parts = [p for p in (_translit(first_name), _translit(last_name)) if p]
    return ".".join(parts)[:MAX_LEN]


def unique_login(db: Session, base: str) -> str:
    """base, а если занят — base2, base3…; база урезается, чтобы результат был не длиннее 32."""
    candidate, n = base[:MAX_LEN], 1
    while db.scalar(select(User.id).where(User.login == candidate)) is not None:
        n += 1
        candidate = f"{base[: MAX_LEN - len(str(n))]}{n}"
    return candidate
