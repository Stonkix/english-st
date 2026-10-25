"""Вопросы заданий: схемы payload, публичный вид для плеера, проверка ответа."""

import random
from dataclasses import dataclass

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
        raise PayloadError(f"{what}: нужен текст")
    value = value.strip()
    if not value and not allow_empty:
        raise PayloadError(f"{what}: не может быть пустым")
    if len(value) > limit:
        raise PayloadError(f"{what}: не длиннее {limit} символов")
    return value


def _list(value, lo: int, hi: int, what: str) -> list:
    if not isinstance(value, list):
        raise PayloadError(f"{what}: нужен список")
    if not lo <= len(value) <= hi:
        raise PayloadError(f"{what}: от {lo} до {hi}")
    return value


def _correct_index(data: dict, count: int) -> int:
    correct = data.get("correct")
    if isinstance(correct, bool) or not isinstance(correct, int) or not 0 <= correct < count:
        raise PayloadError("Отметьте правильный вариант")
    return correct


def validate_payload(qtype: str, data: dict) -> dict:
    """Проверяет и нормализует payload вопроса; иначе PayloadError."""
    if qtype not in QUESTION_TYPES:
        raise PayloadError("Неизвестный тип вопроса")
    if not isinstance(data, dict):
        raise PayloadError("Некорректные данные вопроса")

    if qtype == "choice_text":
        options = [_clean_str(o, 100, "Вариант") for o in _list(data.get("options"), 2, 6, "Вариантов ответа")]
        return {"options": options, "correct": _correct_index(data, len(options))}

    if qtype == "choice_image":
        options = []
        for o in _list(data.get("options"), 2, 4, "Картинок-вариантов"):
            if not isinstance(o, dict):
                raise PayloadError("Некорректный вариант")
            options.append({
                "image": _clean_str(o.get("image"), 500, "Картинка варианта"),
                "caption": _clean_str(o.get("caption", ""), 50, "Подпись", allow_empty=True),
            })
        return {"options": options, "correct": _correct_index(data, len(options))}

    if qtype == "text_input":
        accepted = [_clean_str(a, 200, "Ответ") for a in _list(data.get("accepted"), 1, 10, "Допустимых ответов")]
        return {"accepted": accepted}

    if qtype == "word_bank":
        answer = " ".join(_clean_str(data.get("answer"), 200, "Фраза").split())
        if not 2 <= len(answer.split()) <= 12:
            raise PayloadError("Фраза: от 2 до 12 слов")
        raw = data.get("distractors", [])
        if not isinstance(raw, list):
            raise PayloadError("Лишние слова: нужен список")
        distractors = [_clean_str(d, 50, "Лишнее слово") for d in raw if not (isinstance(d, str) and not d.strip())]
        if len(distractors) > 6 or any(len(d.split()) != 1 for d in distractors):
            raise PayloadError("Лишние слова: до 6 слов, по одному слову")
        return {"answer": answer, "distractors": distractors}

    # match_pairs
    pairs = []
    for p in _list(data.get("pairs"), 2, 6, "Пар"):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise PayloadError("Пара: левая и правая части")
        pairs.append([_clean_str(p[0], 60, "Левая часть пары"), _clean_str(p[1], 60, "Правая часть пары")])
    if len({p[0] for p in pairs}) != len(pairs):
        raise PayloadError("Левые части пар не должны повторяться")
    if len({p[1] for p in pairs}) != len(pairs):
        raise PayloadError("Правые части пар не должны повторяться")
    return {"pairs": pairs}


def _shuffled(items: list, seed: str, salt: str) -> list:
    """Перемешивание, одинаковое при одинаковом seed (строковый seed, не hash())."""
    original = list(items)
    items = list(items)
    random.Random(f"{seed}|{salt}").shuffle(items)
    if len(items) >= 2 and items == original:
        items = items[1:] + items[:1]  # порядок не должен совпасть с исходным
    return items


def _perm(n: int, seed: str, question_id, salt: str) -> list[int]:
    """Непрозрачные id: perm[i] — id элемента с исходным индексом i (зависит от seed)."""
    perm = list(range(n))
    random.Random(f"{seed}|{question_id}|ids|{salt}").shuffle(perm)
    if n >= 2 and perm == list(range(n)):
        perm = perm[1:] + perm[:1]
    return perm


def pair_ids(question: Question, seed: str) -> tuple[list[int], list[int]]:
    """match_pairs: (id левой части пары i, id правой части пары i); перестановки независимы."""
    n = len(question.payload["pairs"])
    left = _perm(n, seed, question.id, "left")
    right = _perm(n, seed, question.id, "right")
    if left == right:
        right = right[1:] + right[:1]
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
        return option["caption"] or f"вариант {p['correct'] + 1}"
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
