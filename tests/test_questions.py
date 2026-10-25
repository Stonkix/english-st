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
    assert grade(q, {"text": "i’m"}, "s").correct and grade(q, {"text": " I AM. "}, "s").correct
    assert not grade(q, {"text": "you are"}, "s").correct
    assert grade(q, {"text": "you are"}, "s").correct_answer == "I am"


def test_typo_rule():
    q = make_q("text_input", {"accepted": ["apple"]})
    r = grade(q, {"text": "aple"}, "s")
    assert r.correct and r.typo
    r = grade(q, {"text": "apple"}, "s")
    assert r.correct and not r.typo
    assert not grade(q, {"text": "aplle x"}, "s").correct
    q2 = make_q("text_input", {"accepted": ["cat"]})
    assert not grade(q2, {"text": "cot"}, "s").correct  # короче 5 — без поблажек
    assert not grade(q, {"text": "aplpe"}, "s").correct  # расстояние 2


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
    assert grade(q, {"option": 1}, "s").correct and grade(q, {"option": 1}, "s").correct_answer == "b"
    assert not grade(q, {"option": 0}, "s").correct
    assert not grade(q, {"option": 5}, "s").correct and not grade(q, {"option": True}, "s").correct


def test_word_bank_grade():
    q = make_q("word_bank", {"answer": "I like cats", "distractors": ["dogs"]})
    v = public_view(q, "s", True)
    by_word = {t["word"]: t["id"] for t in v["tiles"]}
    assert grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["cats"]]}, "s").correct
    assert not grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["dogs"]]}, "s").correct
    assert grade(q, {"tiles": [by_word["I"]]}, "s").correct_answer == "I like cats"
    assert not grade(q, {"tiles": [99, 0, 1]}, "s").correct
    assert not grade(q, {"tiles": "x"}, "s").correct


def test_word_bank_ids_opaque_and_duplicates():
    q = make_q("word_bank", {"answer": "go go home", "distractors": ["now"]})
    tiles = public_view(q, "s1", True)["tiles"]
    assert sorted(t["id"] for t in tiles) == [0, 1, 2, 3]
    ids = {}
    for t in tiles:
        ids.setdefault(t["word"], []).append(t["id"])
    go1, go2 = ids["go"]
    assert grade(q, {"tiles": [go2, go1, ids["home"][0]]}, "s1").correct  # одинаковые слова взаимозаменяемы


def test_word_bank_ids_do_not_leak_answer():
    q = make_q("word_bank", {"answer": "I like cats very much", "distractors": ["dogs", "run"]})
    for seed in ("a", "b", "c", "7:2026-10-26"):
        tiles = public_view(q, seed, True)["tiles"]
        by_id = " ".join(t["word"] for t in sorted(tiles, key=lambda t: t["id"]))
        assert by_id != "I like cats very much dogs run"
        assert [t["word"] for t in tiles] != "I like cats very much dogs run".split()
    tiles = public_view(q, "a", True)["tiles"]
    by_word = {t["word"]: t["id"] for t in tiles}
    right = [by_word[w] for w in "I like cats very much".split()]
    assert grade(q, {"tiles": right}, "a").correct
    assert not grade(q, {"tiles": right}, "other-seed").correct


def test_match_pairs_view_and_grade():
    q = make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"], ["c", "z"]]})
    v = public_view(q, "s", True)
    assert [p["text"] for p in v["left"]] == ["a", "b", "c"]
    assert sorted(p["id"] for p in v["left"]) == [0, 1, 2]
    assert sorted(p["id"] for p in v["right"]) == [0, 1, 2]
    left = {p["text"]: p["id"] for p in v["left"]}
    right = {p["text"]: p["id"] for p in v["right"]}
    assert any(left[a] != right[b] for a, b in (("a", "x"), ("b", "y"), ("c", "z")))
    assert [p["text"] for p in v["right"]] != ["x", "y", "z"]
    assert grade_pairs(q, 0).correct and not grade_pairs(q, 1).correct
    assert not grade_pairs(q, 0).typo
    assert grade_pairs(q, 2).correct_answer == "a — x; b — y; c — z"


def test_shuffles_change_order_from_three_items_and_two_items_are_unpredictable():
    seeds = [str(i) for i in range(30)]
    q = make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"], ["c", "z"]]})
    for seed in seeds:
        assert [p["text"] for p in public_view(q, seed, True)["right"]] != ["x", "y", "z"]
    q = make_q("word_bank", {"answer": "hi you", "distractors": []})
    orders = {tuple(t["word"] for t in public_view(q, s, True)["tiles"]) for s in seeds}
    ids = {tuple(t["id"] for t in public_view(q, s, True)["tiles"]) for s in seeds}
    assert orders == {("hi", "you"), ("you", "hi")} and len(ids) == 2
    q = make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"]]})
    orders = {tuple(p["text"] for p in public_view(q, s, True)["right"]) for s in seeds}
    left_ids = {tuple(p["id"] for p in public_view(q, s, True)["left"]) for s in seeds}
    assert orders == {("x", "y"), ("y", "x")} and left_ids == {(0, 1), (1, 0)}
    q = make_q("choice_text", {"options": ["a", "b"], "correct": 0})
    assert {tuple(o["id"] for o in public_view(q, s, True)["options"]) for s in seeds} == {(0, 1), (1, 0)}


def test_match_pairs_ids_depend_on_seed():
    q = make_q("match_pairs", {"pairs": [["a", "x"], ["b", "y"], ["c", "z"], ["d", "w"]]})
    lefts = {tuple(p["id"] for p in public_view(q, str(i), True)["left"]) for i in range(20)}
    assert len(lefts) > 1 and any(l != (0, 1, 2, 3) for l in lefts)


def test_long_text_answer_is_fast_and_wrong():
    import time

    q = make_q("text_input", {"accepted": ["apple"]})
    start = time.perf_counter()
    assert not grade(q, {"text": "a" * 100_000}, "s").correct
    assert not grade(q, {"text": "a" * 301}, "s").correct
    assert time.perf_counter() - start < 0.5


def test_normalize_extra_quotes_and_commas():
    assert normalize_text("it`s fine,") == "it's fine"
    assert normalize_text("it´s") == "it's"


def test_distractor_must_be_single_word():
    with pytest.raises(PayloadError):
        validate_payload("word_bank", {"answer": "a b", "distractors": ["two words"]})


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
    assert not grade(q, {"option": "zzz"}, "s").correct and not grade(q, {}, "s").correct
    assert not grade(q, None, "s").correct
    t = make_q("text_input", {"accepted": ["apple"]})
    assert not grade(t, {"text": 5}, "s").correct and not grade(t, {}, "s").correct
