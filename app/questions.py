"""Вопросы заданий: схемы payload, публичный вид для плеера, проверка ответа."""

import hashlib
import hmac
import random
from dataclasses import dataclass

from app.config import settings
from app.models.tasks import QUESTION_TYPES, Question


class PayloadError(ValueError):
    """Ошибка в данных вопроса; текст показывается учителю в форме."""


@dataclass
class GradeResult:
    correct: bool
    typo: bool
    correct_answer: str


def _clean_str(value, limit: int, what: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise PayloadError(f"{what} must be text")
    value = value.strip()
    if not value and not allow_empty:
        raise PayloadError(f"{what} cannot be empty")
    if len(value) > limit:
        raise PayloadError(f"{what} must be at most {limit} characters")
    return value


def _list(value, lo: int, hi: int, what: str) -> list:
    if not isinstance(value, list):
        raise PayloadError(f"{what} must be a list")
    if not lo <= len(value) <= hi:
        raise PayloadError(f"{what}: enter between {lo} and {hi}")
    return value


def _correct_index(data: dict, count: int) -> int:
    correct = data.get("correct")
    if isinstance(correct, bool) or not isinstance(correct, int) or not 0 <= correct < count:
        raise PayloadError("Select the correct answer")
    return correct


def validate_payload(qtype: str, data: dict) -> dict:
    """Проверяет и нормализует payload вопроса; иначе PayloadError."""
    if qtype not in QUESTION_TYPES:
        raise PayloadError("Unknown question type")
    if not isinstance(data, dict):
        raise PayloadError("Invalid question data")

    if qtype == "choice_text":
        options = [_clean_str(o, 100, "Answer choice") for o in _list(data.get("options"), 2, 6, "Answer choices")]
        return {"options": options, "correct": _correct_index(data, len(options))}

    if qtype == "choice_image":
        options = []
        for o in _list(data.get("options"), 2, 4, "Image choices"):
            if not isinstance(o, dict):
                raise PayloadError("Invalid answer choice")
            options.append({
                "image": _clean_str(o.get("image"), 500, "Answer choice image"),
                "caption": _clean_str(o.get("caption", ""), 50, "Caption", allow_empty=True),
            })
        return {"options": options, "correct": _correct_index(data, len(options))}

    if qtype == "text_input":
        accepted = [_clean_str(a, 200, "Answer") for a in _list(data.get("accepted"), 1, 10, "Accepted answers")]
        return {"accepted": accepted}

    if qtype == "word_bank":
        answer = " ".join(_clean_str(data.get("answer"), 200, "Phrase").split())
        if not 2 <= len(answer.split()) <= 12:
            raise PayloadError("Enter a phrase of 2–12 words")
        raw = data.get("distractors", [])
        if not isinstance(raw, list):
            raise PayloadError("Extra words must be a list")
        distractors = [_clean_str(d, 50, "Extra word") for d in raw if not (isinstance(d, str) and not d.strip())]
        if len(distractors) > 6 or any(len(d.split()) != 1 for d in distractors):
            raise PayloadError("Enter up to 6 extra words, one per field")
        return {"answer": answer, "distractors": distractors}

    # match_pairs
    pairs = []
    for p in _list(data.get("pairs"), 2, 6, "Pairs"):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise PayloadError("Each pair needs a left and a right entry")
        pairs.append([_clean_str(p[0], 60, "Left side of the pair"), _clean_str(p[1], 60, "Right side of the pair")])
    if len({p[0] for p in pairs}) != len(pairs):
        raise PayloadError("Use a unique entry on the left of each pair")
    if len({p[1] for p in pairs}) != len(pairs):
        raise PayloadError("Use a unique entry on the right of each pair")
    return {"pairs": pairs}


def _shuffled(items: list, seed: str, salt: str) -> list:
    """Перемешивание, одинаковое при одинаковом seed (строковый seed, не hash())."""
    original = list(items)
    items = list(items)
    random.Random(f"{seed}|{salt}").shuffle(items)
    if len(items) >= 3 and items == original:  # для 2 элементов — честный 50/50, иначе порядок предсказуем
        items = items[1:] + items[:1]  # порядок не должен совпасть с исходным
    return items


def _perm(n: int, seed: str, question_id, salt: str) -> list[int]:
    """Непрозрачные id: perm[i] — id элемента с исходным индексом i.

    Seed перестановки смешан с секретом сервера (HMAC): seed ученика (id + дата) известен,
    и без секрета по нему можно было бы восстановить порядок фразы и пары.
    """
    perm = list(range(n))
    key = settings.secret_key.encode()
    digest = hmac.new(key, f"{seed}|{question_id}|ids|{salt}".encode(), hashlib.sha256).hexdigest()
    random.Random(digest).shuffle(perm)
    return perm


def pair_ids(question: Question, seed: str) -> tuple[list[int], list[int]]:
    """match_pairs: (id левой части пары i, id правой части пары i); перестановки независимы."""
    n = len(question.payload["pairs"])
    left = _perm(n, seed, question.id, "left")
    right = _perm(n, seed, question.id, "right")
    return left, right


def _media(path: str | None) -> str | None:
    return "/media/" + path if path else None


def public_view(question: Question, seed: str, shuffle_options: bool) -> dict:
    """Вид вопроса для плеера: без правильных ответов."""
    p = question.payload
    view = {
        "id": question.id,
        "type": question.type,
        "prompt": question.prompt,
        "image_url": _media(question.image),
    }
    salt = str(question.id)
    if question.type == "choice_text":
        options = [{"id": i, "text": t} for i, t in enumerate(p["options"])]
        view["options"] = _shuffled(options, seed, salt) if shuffle_options else options
    elif question.type == "choice_image":
        options = [
            {"id": i, "image_url": _media(o["image"]), "caption": o["caption"]}
            for i, o in enumerate(p["options"])
        ]
        view["options"] = _shuffled(options, seed, salt) if shuffle_options else options
    elif question.type == "word_bank":
        # id плитки непрозрачный (перестановка по seed), иначе порядок id выдаёт ответ
        words = p["answer"].split() + p["distractors"]
        ids = _perm(len(words), seed, question.id, "tiles")
        tiles = [{"id": ids[i], "word": w} for i, w in enumerate(words)]
        view["tiles"] = _shuffled(tiles, seed, salt)
    elif question.type == "match_pairs":
        left_ids, right_ids = pair_ids(question, seed)
        view["left"] = [{"id": left_ids[i], "text": pair[0]} for i, pair in enumerate(p["pairs"])]
        right = [{"id": right_ids[i], "text": pair[1]} for i, pair in enumerate(p["pairs"])]
        view["right"] = _shuffled(right, seed, salt)
    return view


def normalize_text(s: str) -> str:
    s = s.lower().replace("ё", "е")
    for ch in "’‘ʼ`´":
        s = s.replace(ch, "'")
    s = " ".join(s.split())
    return s.rstrip(".,!?").strip()


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _correct_answer(question: Question) -> str:
    p = question.payload
    if question.type == "choice_text":
        return p["options"][p["correct"]]
    if question.type == "choice_image":
        option = p["options"][p["correct"]]
        return option["caption"] or f"Option {p['correct'] + 1}"
    if question.type == "text_input":
        return p["accepted"][0]
    if question.type == "word_bank":
        return p["answer"]
    return "; ".join(f"{left} — {right}" for left, right in p["pairs"])


def grade(question: Question, answer: dict, seed: str) -> GradeResult:
    """Проверка ответа (seed тот же, что у public_view). Некорректная форма ответа — просто неверно."""
    shown = _correct_answer(question)
    wrong = GradeResult(False, False, shown)
    if not isinstance(answer, dict):
        return wrong
    p = question.payload

    if question.type in ("choice_text", "choice_image"):
        option = answer.get("option")
        ok = isinstance(option, int) and not isinstance(option, bool) and option == p["correct"]
        return GradeResult(ok, False, shown)

    if question.type == "text_input":
        text = answer.get("text")
        if not isinstance(text, str):
            return wrong
        if len(text) > 300:
            return wrong
        given = normalize_text(text)
        accepted = [normalize_text(a) for a in p["accepted"]]
        if given in accepted:
            return GradeResult(True, False, shown)
        # поблажка только для ответов от 5 символов (по длине эталона: «aple» → «apple»)
        if any(len(a) >= 5 and abs(len(given) - len(a)) <= 1 and _levenshtein(given, a) == 1 for a in accepted):
            return GradeResult(True, True, shown)
        return wrong

    if question.type == "word_bank":
        ids = answer.get("tiles")
        words = p["answer"].split() + p["distractors"]
        if not isinstance(ids, list):
            return wrong
        perm = _perm(len(words), seed, question.id, "tiles")
        index_of = {tile_id: i for i, tile_id in enumerate(perm)}
        picked = []
        for tile_id in ids:
            if isinstance(tile_id, bool) or not isinstance(tile_id, int) or tile_id not in index_of:
                return wrong
            picked.append(words[index_of[tile_id]])
        ok = normalize_text(" ".join(picked)) == normalize_text(p["answer"])
        return GradeResult(ok, False, shown)

    # match_pairs считается через grade_pairs (число ошибок хранится в попытке)
    return wrong


def grade_pairs(question: Question, mistakes: int) -> GradeResult:
    """match_pairs: верно, только если не было ошибок при сопоставлении."""
    return GradeResult(mistakes == 0, False, _correct_answer(question))


def picture_paths(question: Question) -> set[str]:
    """Все файлы картинок вопроса: к тексту вопроса и к вариантам choice_image."""
    paths = {question.image} if question.image else set()
    if question.type == "choice_image":
        paths.update(o["image"] for o in question.payload.get("options", []))
    return paths
