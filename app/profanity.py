"""Фильтр мата: находит бранные слова с обходами и заменяет их словом из перечня (§8.5).

Каждое слово текста сводится к «скелету»: нижний регистр, ё→е, двойники букв и цифры → буквы, без разделителей
(«х.у.й» → «хуй»), повторы схлопнуты («бляяя» → «бля»). Подряд идущие одиночные буквы («х у й») склеиваются
в одно слово. Для русских корней латинские двойники переводятся в кириллицу, для английских — наоборот.
"""

import random
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import BadWord, BadWordException, ReplacementWord

_TO_RU = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т",
    "x": "х", "y": "у", "u": "и", "0": "о", "3": "з", "4": "ч", "6": "б", "@": "а", "$": "с",
})
_TO_EN = str.maketrans({
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k", "м": "m", "о": "o", "р": "p", "т": "t",
    "х": "x", "у": "y", "и": "u", "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
    "$": "s", "!": "i",
})
_REPEATS = re.compile(r"(.)\1+")
_TOKEN = re.compile(r"\S+")
_LETTERISH = "@$"  # внутри слова считаются буквами (двойники); прочие знаки по краям слова остаются как были


def _skeleton(word: str, table: dict) -> str:
    s = word.lower().replace("ё", "е").translate(table)
    return _REPEATS.sub(r"\1", "".join(ch for ch in s if ch.isalpha()))


def is_cyrillic(word: str) -> bool:
    return any("а" <= ch <= "я" or ch == "ё" for ch in word.lower())


@dataclass(frozen=True)
class Filter:
    ru: tuple[tuple[str, bool], ...]  # (корень-скелет, только в начале слова)
    en: tuple[tuple[str, bool], ...]
    exceptions_ru: tuple[str, ...]
    exceptions_en: tuple[str, ...]
    replacements: tuple[str, ...]

    def is_bad_word(self, word: str) -> bool:
        return _bad(_skeleton(word, _TO_RU), self.ru, self.exceptions_ru) or _bad(
            _skeleton(word, _TO_EN), self.en, self.exceptions_en
        )


def compile_filter(bad_words, exceptions, replacements) -> Filter:
    ru, en = [], []
    for raw in bad_words:
        anchored = raw.startswith("^")
        word = raw.lstrip("^").strip()
        if not word:
            continue
        if is_cyrillic(word):
            ru.append((_skeleton(word, _TO_RU), anchored))
        else:
            en.append((_skeleton(word, _TO_EN), anchored))
    words = [w.strip() for w in exceptions if w.strip()]
    return Filter(
        ru=tuple(p for p in ru if p[0]),
        en=tuple(p for p in en if p[0]),
        exceptions_ru=tuple(s for s in (_skeleton(w, _TO_RU) for w in words) if s),
        exceptions_en=tuple(s for s in (_skeleton(w, _TO_EN) for w in words) if s),
        replacements=tuple(w.strip() for w in replacements if w.strip()) or ("ромашка",),
    )


def load_filter(db: DbSession) -> Filter:
    """Словарь из БД. Читаем при каждом сообщении: он маленький, а правка админа действует сразу."""
    return compile_filter(
        db.scalars(select(BadWord.pattern)),
        db.scalars(select(BadWordException.word)),
        db.scalars(select(ReplacementWord.word).order_by(ReplacementWord.id)),
    )


def _covered(skel: str, start: int, exceptions: tuple[str, ...]) -> bool:
    """Совпадение начинается внутри слова-исключения («рубля», «застрахуй»)."""
    for exc in exceptions:
        j = skel.find(exc)
        while j != -1:
            if j <= start < j + len(exc):
                return True
            j = skel.find(exc, j + 1)
    return False


def _bad(skel: str, patterns, exceptions) -> bool:
    for pat, anchored in patterns:
        if anchored:
            if skel.startswith(pat) and not _covered(skel, 0, exceptions):
                return True
            continue
        i = skel.find(pat)
        while i != -1:
            if not _covered(skel, i, exceptions):
                return True
            i = skel.find(pat, i + 1)
    return False


def _core(match: re.Match) -> tuple[int, int] | None:
    """Границы слова без знаков по краям («блин!» → «блин»); None, если букв нет."""
    token = match.group()
    inside = [i for i, ch in enumerate(token) if ch.isalnum() or ch in _LETTERISH]
    if not inside:
        return None
    return match.start() + inside[0], match.start() + inside[-1] + 1


def _bad_spans(text: str, flt: Filter) -> list[tuple[int, int]]:
    words = [span for span in (_core(m) for m in _TOKEN.finditer(text)) if span]
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(words):
        start, end = words[i]
        if end - start == 1:  # «х у й»: подряд идущие одиночные буквы проверяем как одно слово
            j = i
            while j + 1 < len(words) and words[j + 1][1] - words[j + 1][0] == 1:
                j += 1
            if j > i:
                joined = "".join(text[s:e] for s, e in words[i:j + 1])
                if flt.is_bad_word(joined):
                    spans.append((start, words[j][1]))
                i = j + 1
                continue
        if flt.is_bad_word(text[start:end]):
            spans.append((start, end))
        i += 1
    return spans


def contains_profanity(text: str, flt: Filter) -> bool:
    return bool(_bad_spans(text, flt))


def clean_text(text: str, flt: Filter, rng: random.Random | None = None) -> tuple[str, bool]:
    """(текст для показа, было ли что заменить). Бранное слово целиком меняется на слово из перечня."""
    spans = _bad_spans(text, flt)
    if not spans:
        return text, False
    rng = rng or random
    out, pos = [], 0
    for start, end in spans:
        word = rng.choice(flt.replacements)
        if text[start].isupper():
            word = word[:1].upper() + word[1:]
        out += [text[pos:start], word]
        pos = end
    out.append(text[pos:])
    return "".join(out), True
