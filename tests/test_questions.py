import json

import pytest

from app.models.tasks import Question
from app.questions import (
    PayloadError,
    grade,
    grade_pairs,
    normalize_text,
    public_view,
    validate_payload,
)


def make_q(qtype, payload, **kw):
    return Question(type=qtype, prompt="?", payload=validate_payload(qtype, payload), **kw)


def all_type_samples():
    return [
        make_q("choice_text", {"options": ["a", "b"], "correct": 1}),
        make_q("choice_image", {"options": [{"image": "q/1.webp", "caption": "cat"}, {"image": "q/2.webp", "caption": ""}], "correct": 0}, image="q/0.webp"),
        make_q("text_input", {"accepted": ["I am", "I'm"]}),
        make_q("word_bank", {"answer": "I like cats", "distractors": ["dogs"]}),
        make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"]]}),
    ]


def test_normalize():
    assert normalize_text("  I’m   HAPPY. ") == "i'm happy"
    assert normalize_text("Ёлка!") == "елка"
    assert normalize_text("it‘s ʼok?!") == "it's 'ok"


def test_text_input_variants_and_typo():
    q = make_q("text_input", {"accepted": ["I am", "I'm"]})
    assert grade(q, {"text": "i’m"}).correct and grade(q, {"text": " I AM. "}).correct
    assert not grade(q, {"text": "you are"}).correct
    assert grade(q, {"text": "you are"}).correct_answer == "I am"


def test_typo_rule():
    q = make_q("text_input", {"accepted": ["apple"]})
    r = grade(q, {"text": "aple"})
    assert r.correct and r.typo
    r = grade(q, {"text": "apple"})
    assert r.correct and not r.typo
    assert not grade(q, {"text": "aplle x"}).correct
    q2 = make_q("text_input", {"accepted": ["cat"]})
    assert not grade(q2, {"text": "cot"}).correct  # короче 5 — без поблажек
    assert not grade(q, {"text": "aplpe"}).correct  # расстояние 2


def test_public_view_hides_answers():
    for q in all_type_samples():
        view = public_view(q, "s", True)
        blob = json.dumps({**view, "type": ""}, ensure_ascii=False)  # имя типа («match_pairs») не в счёт
        assert "correct" not in blob and "accepted" not in blob and "pairs" not in blob and "answer" not in blob


def test_public_view_image_urls():
    q = all_type_samples()[1]
    v = public_view(q, "s", False)
    assert v["image_url"] == "/media/q/0.webp"
    assert v["options"][0] == {"id": 0, "image_url": "/media/q/1.webp", "caption": "cat"}
    assert public_view(all_type_samples()[0], "s", False)["image_url"] is None


def test_choice_shuffle_deterministic():
    q = make_q("choice_text", {"options": list("abcdef"), "correct": 2})
    assert public_view(q, "7:2026-10-26", True) == public_view(q, "7:2026-10-26", True)
    ids = [o["id"] for o in public_view(q, "x", False)["options"]]
    assert ids == [0, 1, 2, 3, 4, 5]
    shuffled = [o["id"] for o in public_view(q, "x", True)["options"]]
    assert sorted(shuffled) == ids


def test_choice_grade():
    q = make_q("choice_text", {"options": ["a", "b"], "correct": 1})
    assert grade(q, {"option": 1}).correct and grade(q, {"option": 1}).correct_answer == "b"
    assert not grade(q, {"option": 0}).correct
    assert not grade(q, {"option": 5}).correct and not grade(q, {"option": True}).correct


def test_word_bank_grade():
    q = make_q("word_bank", {"answer": "I like cats", "distractors": ["dogs"]})
    v = public_view(q, "s", True)
    by_word = {t["word"]: t["id"] for t in v["tiles"]}
    assert grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["cats"]]}).correct
    assert not grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["dogs"]]}).correct
    assert grade(q, {"tiles": [by_word["I"]]}).correct_answer == "I like cats"
    assert not grade(q, {"tiles": [99, 0, 1]}).correct
    assert not grade(q, {"tiles": "x"}).correct


def test_word_bank_stable_ids_and_duplicates():
    q = make_q("word_bank", {"answer": "go go home", "distractors": ["now"]})
    a = {t["id"]: t["word"] for t in public_view(q, "s1", True)["tiles"]}
    b = {t["id"]: t["word"] for t in public_view(q, "s2", True)["tiles"]}
    assert a == b == {0: "go", 1: "go", 2: "home", 3: "now"}
    assert grade(q, {"tiles": [1, 0, 2]}).correct  # одинаковые слова взаимозаменяемы


def test_match_pairs_view_and_grade():
    q = make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"], ["c", "z"]]})
    v = public_view(q, "s", True)
    assert [p["id"] for p in v["left"]] == [0, 1, 2]
    assert {p["id"]: p["text"] for p in v["right"]} == {0: "x", 1: "y", 2: "z"}
    assert grade_pairs(q, 0).correct and not grade_pairs(q, 1).correct
    assert not grade_pairs(q, 0).typo
    assert grade_pairs(q, 2).correct_answer == "a — x; b — y; c — z"


def test_seeded_shuffle_is_deterministic():
    q = make_q("match_pairs", {"pairs": [[str(i), str(i)] for i in range(6)]})
    assert public_view(q, "seed", True)["right"] == public_view(q, "seed", True)["right"]


def test_validate_rejects():
    bad = [
        ("choice_text", {"options": ["a"], "correct": 0}),
        ("match_pairs", {"pairs": [["a", "x"], ["a", "y"]]}),
        ("match_pairs", {"pairs": [["a", "x"], ["b", "x"]]}),
        ("unknown", {}),
        ("choice_text", {"options": ["a", " "], "correct": 0}),
        ("choice_text", {"options": ["a", "b"], "correct": 2}),
        ("choice_image", {"options": [{"image": "p", "caption": "x" * 51}, {"image": "q", "caption": ""}], "correct": 0}),
        ("text_input", {"accepted": []}),
        ("word_bank", {"answer": "one", "distractors": []}),
        ("word_bank", {"answer": "a b", "distractors": list("abcdefg")}),
    ]
    for qtype, data in bad:
        with pytest.raises(PayloadError):
            validate_payload(qtype, data)


def test_validate_normalizes():
    p = validate_payload("word_bank", {"answer": "  I   like cats ", "distractors": [" dogs ", ""]})
    assert p == {"answer": "I like cats", "distractors": ["dogs"]}
    assert validate_payload("text_input", {"accepted": [" a ", "b"]}) == {"accepted": ["a", "b"]}


def test_malformed_answer_is_wrong_not_error():
    q = make_q("choice_text", {"options": ["a", "b"], "correct": 1})
    assert not grade(q, {"option": "zzz"}).correct and not grade(q, {}).correct
    assert not grade(q, None).correct
    t = make_q("text_input", {"accepted": ["apple"]})
    assert not grade(t, {"text": 5}).correct and not grade(t, {}).correct
