import random

import pytest

from app.profanity import clean_text, compile_filter, contains_profanity
from app.profanity_defaults import BAD_WORDS, EXCEPTIONS, REPLACEMENTS

F = compile_filter(BAD_WORDS, EXCEPTIONS, REPLACEMENTS)


@pytest.mark.parametrize("text", [
    "хуй", "ХУЙ", "xyй", "х.у.й", "х у й", "х-у-й", "бляяяя", "бляtь", "6лять", "сука", "ссука",
    "пиздец", "пиздёж", "заебал", "отъебись", "по-хуй",
    "fuck", "FUUUCK", "sh1t", "f.u.c.k", "мудак", "пидорас", "ёбаный",
])
def test_bad_words_caught(text):
    assert contains_profanity(text, F), text


@pytest.mark.parametrize("text", [
    "рубля", "употреблять", "застрахуй", "страхует", "психую", "хлеб", "хлебать", "небо", "мандарин",
    "сукно", "команда", "погребал", "скипидар", "Hello, how are you?", "Я и ты", "I like dogs",
    "оскорблять", "корабля", "assignment", "classic",
])
def test_ordinary_words_pass(text):
    assert not contains_profanity(text, F), text


def test_replacement_keeps_punctuation_and_case():
    shown, filtered = clean_text("Привет, Блять! как дела", F, random.Random(1))
    assert filtered
    word = shown.split(", ")[1].split("!")[0]
    assert word.lower() in REPLACEMENTS and word[0].isupper()
    assert shown.startswith("Привет, ") and shown.endswith("! как дела")


def test_spaced_letters_replaced_as_one_word():
    shown, filtered = clean_text("ну х у й", F, random.Random(2))
    assert filtered and shown.startswith("ну ") and len(shown.split()) == 2


def test_clean_text_untouched():
    assert clean_text("Hello, teacher!", F) == ("Hello, teacher!", False)


def test_exception_and_anchor_from_admin_lists():
    f = compile_filter(["^кот", "пёс"], ["скотина"], ["мяч"])
    assert contains_profanity("котлета", f) and not contains_profanity("скотина", f)
    assert contains_profanity("пес", f)  # ё и е — одно и то же
    assert clean_text("пёс", f)[0] == "мяч"


def test_empty_replacements_fallback():
    f = compile_filter(["кот"], [], [])
    assert clean_text("кот", f)[0] == "ромашка"
