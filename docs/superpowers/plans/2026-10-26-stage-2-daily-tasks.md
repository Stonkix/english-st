# Этап 2: задания — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** пулы вопросов, вопросы пяти типов, тесты, назначения, настройки ежедневного задания группы, плеер задания в стиле Duolingo, результаты для учителя и серия дней.

**Architecture:** чистая логика (схемы вопросов, проверка ответов, подбор задания, серия) — в отдельных модулях без HTTP и легко тестируется; маршруты учителя (`app/routes/tasks.py`) и ученика (`app/routes/daily.py`) тонкие. Плеер — страница с данными вопросов без правильных ответов (`<script type="application/json">`), ответы проверяет сервер (`POST /daily/answer`, JSON).

**Tech Stack:** как в этапе 0–1 (FastAPI, SQLAlchemy 2.0 sync, Alembic, Jinja2, HTMX, свой JS без сборки, Pillow).

**Spec:** `docs/superpowers/specs/2026-10-25-english-st-design.md` — §5.2, §7 (Pool, GroupPool, Question, Test, TestQuestion, Assignment, DailySettings, Attempt, AttemptAnswer, Streak), §8.2–8.4, §9 (экраны ученика и учителя), §10.

**Предыдущий план (стиль, соглашения):** `docs/superpowers/plans/2026-10-25-stage-0-1-foundation-accounts.md`. Существующие модули: `app/routes/teacher.py` (`teacher_only = require_role("teacher")`, `own_group`, `own_student`), `app/timeutil.py` (`utcnow`, `MOSCOW`, `moscow_str`), `app/images.py` (`read_limited`, `InvalidImage`, семафор декодирования), `app/routes/media.py` (строгие пути + X-Accel-Redirect), `app/auth/access.py`, `tests/factories.py`.

## Global Constraints

- Всё из Global Constraints этапа 0–1 (Python 3.12, без Node/CDN, русский UI, коммиты без Co-Authored-By и упоминания Claude, даты в БД — naive UTC, Origin-проверка, никаких inline-скриптов и `on*=`-атрибутов — CSP `script-src 'self'`).
- «Сегодня» = дата по Москве: `moscow_today(now: datetime | None = None) -> date` (смена дня в 00:00 МСК).
- Уровни пула — ровно `("A1", "A1+", "A2", "A2+", "B1", "B1+", "B2", "B2+", "C1", "C1+", "C2", "C2+")`; у пула обязательны название (≤ 80), описание (≤ 500), уровень.
- Типы вопросов — ровно `choice_text`, `choice_image`, `text_input`, `word_bank`, `match_pairs`. Озвучки нет.
- Правильные ответы никогда не попадают в HTML/JSON плеера до ответа; проверка только на сервере.
- Настройки ежедневки группы по умолчанию: `question_count=5` (1–30), `max_attempts=3` (1–10), `shuffle_questions=True`, `shuffle_options=True`. Награды штампиками — этап 4, в этом плане их нет.
- Картинки вопросов: вход ≤ 10 МБ, выход ≤ 1280px по длинной стороне, WebP ≤ 400 КБ; доступ — учитель-автор, админ, ученики групп этого учителя.
- Один процесс uvicorn; SQLite.

## Review Focus

1. Ученик обновил страницу/закрыл телефон посреди задания — продолжает с того же вопроса, набор вопросов на день не меняется (Task 5).
2. Двойное нажатие «Проверить» / повтор `POST /daily/answer` на тот же вопрос — ответ засчитывается один раз, второй запрос возвращает тот же результат (Task 5).
3. Учитель удалил или изменил вопрос, пока ученик проходит задание / после прохождения — попытка и история не ломаются (удаление вопроса с ответами = скрытие) (Task 3, Task 5).
4. У группы нет ни пулов, ни назначения, или в пулах меньше вопросов, чем `question_count` — экран «Сегодня заданий нет» / задание из всех доступных, без 500 (Task 4).
5. Ответ с лишними пробелами, заглавными, `’` вместо `'`, точкой в конце, «ё» — засчитывается (Task 2).

---

## Карта файлов

```
app/models/tasks.py        Pool, GroupPool, Question, Test, TestQuestion, Assignment, DailySettings, Attempt, AttemptAnswer
app/models/accounts.py     + User.current_streak, best_streak, last_completed_date
alembic/versions/0003_tasks.py
app/questions.py           схемы payload, validate_payload, public_view, grade, normalize_text
app/daily.py               moscow_today, settings_for, todays_source, start_or_resume, submit_answer, check_pair
app/streak.py              had_tasks, register_completion, effective_streak
app/images.py              + save_picture
app/auth/access.py         + can_view_question_image
app/routes/media.py        + /media/questions/{owner_id}/{name}
app/routes/tasks.py        учитель: пулы, вопросы, тесты, назначения, настройки группы, результаты
app/routes/daily.py        ученик: главная, старт, плеер, ответ, результат
app/templates/tasks/…      pools.html, pool.html, question_form.html, tests.html, test.html, group_daily.html, results_day.html, student_results.html
app/templates/student/…    home.html (замена заглушки), player.html, result.html
app/static/js/player.js    плеер (5 типов, анимации, плашка результата)
app/static/js/editor.js    редактор вопроса (динамические строки вариантов/пар)
tests/test_questions.py, test_daily.py, test_streak.py, test_tasks_routes.py, test_player.py, test_results.py
```

---

### Task 1: Модели заданий и миграция 0003

**Files:** Create `app/models/tasks.py`, `alembic/versions/0003_tasks.py`; Modify `app/models/__init__.py`, `app/models/accounts.py`; Test `tests/test_task_models.py`.

**Interfaces — Produces:**
- `LEVELS: tuple[str, ...]` (Global Constraints), `QUESTION_TYPES = ("choice_text", "choice_image", "text_input", "word_bank", "match_pairs")`.
- `Pool`: id, owner_id (FK users), title, description, level, created_at; `questions` (только `is_active`, порядок по id).
- `GroupPool`: group_id, pool_id (составной PK, оба FK CASCADE).
- `Question`: id, owner_id, pool_id (FK pools, nullable, `ondelete="SET NULL"`), type, prompt, payload (`JSON`), image (str | None), is_active (default True), created_at.
- `Test`: id, owner_id, title, description, created_at; `TestQuestion`: test_id, question_id, position (PK test_id+question_id; CASCADE по test_id); `Test.questions` — упорядочены по position.
- `Assignment`: id, test_id (FK CASCADE), group_id (FK CASCADE, nullable), student_id (FK users CASCADE, nullable), date (`Date`); CHECK «ровно одно из group_id/student_id»; уникальные (group_id, date) и (student_id, date).
- `DailySettings`: group_id (PK, FK CASCADE), question_count=5, max_attempts=3, shuffle_questions=True, shuffle_options=True.
- `Attempt`: id, student_id (FK CASCADE, index), date (`Date`, index), source (`"test" | "pool"`), test_id (nullable, `SET NULL`), question_ids (JSON list[int], порядок показа), pair_mistakes (JSON dict, default {}), started_at, finished_at (nullable), correct_count=0, total_count.
- `AttemptAnswer`: attempt_id (FK CASCADE), question_id, answer (JSON), is_correct, had_typo, created_at; PK (attempt_id, question_id) — один ответ на вопрос в попытке.
- `User`: `current_streak: int = 0`, `best_streak: int = 0`, `last_completed_date: date | None`.

- [ ] **Step 1: failing tests** — `test_assignment_exactly_one_target` (обе цели / ни одной → IntegrityError), `test_assignment_unique_per_group_day`, `test_answer_once_per_question` (второй AttemptAnswer с тем же (attempt, question) → IntegrityError), `test_pool_questions_hide_inactive`, `test_question_pool_set_null_on_pool_delete`, существующий `test_migration_matches_models` остаётся зелёным.
- [ ] **Step 2: Run** `pytest tests/test_task_models.py -q` — FAIL.
- [ ] **Step 3: Implement** модели и миграцию (`revision="0003"`, `down_revision="0002"`; autogenerate + проверка глазами; CHECK-ограничение назвать `ck_assignment_one_target`).
- [ ] **Step 4: Run** `pytest -q` — PASS.
- [ ] **Step 5: Commit** «Задания: модели и миграция».

---

### Task 2: Вопросы — схемы, публичный вид, проверка ответа

**Files:** Create `app/questions.py`; Test `tests/test_questions.py`.

**Interfaces — Produces:**
- `class PayloadError(ValueError)` — текст по-русски для формы.
- `validate_payload(qtype: str, data: dict) -> dict` — нормализованный payload или `PayloadError`. Правила (§8.3):
  - `choice_text`: `options` 2–6 непустых строк ≤ 100, `correct` — индекс в диапазоне.
  - `choice_image`: `options` 2–4 объекта `{"image": str, "caption": str}` (image — путь из `save_picture`, caption ≤ 50, может быть пустым), `correct` — индекс.
  - `text_input`: `accepted` 1–10 непустых строк ≤ 200.
  - `word_bank`: `answer` — 2–12 слов (через пробел, ≤ 200 символов), `distractors` 0–6 слов.
  - `match_pairs`: `pairs` 2–6 пар `[left, right]`, обе части непустые ≤ 60, левые уникальны, правые уникальны.
- `public_view(question: Question, seed: str, shuffle_options: bool) -> dict` — то, что получает плеер: `{"id", "type", "prompt", "image_url"}` +
  - choice_*: `options: [{"id": исходный индекс, "text"|"image_url"+"caption"}]` (перемешаны по seed, если shuffle_options);
  - text_input: ничего лишнего;
  - word_bank: `tiles: [{"id": i, "word": w}]` — слова ответа + отвлекающие, всегда перемешаны;
  - match_pairs: `left: [{"id": i, "text"}]`, `right: [{"id": i, "text"}]` — правые всегда перемешаны.
  Никаких `correct`/`accepted`/`answer`/`pairs`.
- `normalize_text(s: str) -> str` — lower, `ё→е`, `’ ‘ ʼ → '`, схлопнуть пробелы, срезать по краям, убрать конечные `.!?`.
- `@dataclass GradeResult(correct: bool, typo: bool, correct_answer: str)`; `correct_answer` — для показа (текст варианта / первый accepted / фраза / «left — right; …»).
- `grade(question: Question, answer: dict) -> GradeResult`. Форматы ответа: choice_* `{"option": int}`; text_input `{"text": str}`; word_bank `{"tiles": [int, ...]}` (id плиток по порядку); match_pairs `{}` (результат считается из `pair_mistakes`, см. Task 5: `grade_pairs(mistakes: int) -> GradeResult`). Некорректная форма ответа → `GradeResult(False, False, …)`, не исключение.
- Опечатка: только text_input и только если нормализованный ответ длиной ≥ 5 отличается от какого-то accepted на расстояние Левенштейна 1 → `correct=True, typo=True`.

- [ ] **Step 1: failing tests**

```python
def test_normalize():
    assert normalize_text("  I’m   HAPPY. ") == "i'm happy"
    assert normalize_text("Ёлка!") == "елка"

def test_text_input_variants_and_typo():
    q = make_q("text_input", {"accepted": ["I am", "I'm"]})
    assert grade(q, {"text": "i’m"}).correct and grade(q, {"text": " I AM. "}).correct
    assert not grade(q, {"text": "you are"}).correct

def test_typo_rule():
    q = make_q("text_input", {"accepted": ["apple"]})
    r = grade(q, {"text": "aple"}); assert r.correct and r.typo
    q2 = make_q("text_input", {"accepted": ["cat"]})
    assert not grade(q2, {"text": "cot"}).correct  # короче 5 — без поблажек

def test_public_view_hides_answers():
    for q in all_type_samples():
        blob = json.dumps(public_view(q, "s", True), ensure_ascii=False)
        assert "correct" not in blob and "accepted" not in blob and "pairs" not in blob

def test_choice_shuffle_deterministic():
    q = make_q("choice_text", {"options": list("abcdef"), "correct": 2})
    assert public_view(q, "7:2026-10-26", True) == public_view(q, "7:2026-10-26", True)
    ids = [o["id"] for o in public_view(q, "x", False)["options"]]; assert ids == [0, 1, 2, 3, 4, 5]

def test_word_bank_grade():
    q = make_q("word_bank", {"answer": "I like cats", "distractors": ["dogs"]})
    v = public_view(q, "s", True); by_word = {t["word"]: t["id"] for t in v["tiles"]}
    assert grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["cats"]]}).correct
    assert not grade(q, {"tiles": [by_word["I"], by_word["like"], by_word["dogs"]]}).correct

def test_validate_rejects():
    with pytest.raises(PayloadError): validate_payload("choice_text", {"options": ["a"], "correct": 0})
    with pytest.raises(PayloadError): validate_payload("match_pairs", {"pairs": [["a", "x"], ["a", "y"]]})
    with pytest.raises(PayloadError): validate_payload("unknown", {})

def test_malformed_answer_is_wrong_not_error():
    q = make_q("choice_text", {"options": ["a", "b"], "correct": 1})
    assert not grade(q, {"option": "zzz"}).correct and not grade(q, {}).correct
```

(`make_q` — несохранённый `Question(type=..., prompt="?", payload=validate_payload(...))`; слово-плитки с повторяющимися словами сравниваются по тексту, а не по id.)
- [ ] **Step 2–4:** FAIL → реализация → PASS.
- [ ] **Step 5: Commit** «Задания: схемы вопросов и проверка ответов».

---

### Task 3: Учитель — пулы и редактор вопросов (с картинками)

**Files:** Create `app/routes/tasks.py`, `app/templates/tasks/pools.html`, `pool.html`, `question_form.html`, `app/static/js/editor.js`; Modify `app/images.py`, `app/auth/access.py`, `app/routes/media.py`, `app/main.py`, `app/templates/partials/nav.html`; Test `tests/test_tasks_routes.py`, `tests/test_media.py`.

**Interfaces:**
- Consumes: `teacher_only`, `validate_payload`, `PayloadError`, модели Task 1.
- Produces:
  - `app.images.save_picture(owner_id: int, data: bytes) -> str` — путь `questions/<owner_id>/<12 hex>.webp`, длинная сторона ≤ 1280, ≤ 400 КБ, те же защиты памяти и EXIF, что у аватара (семафор, полосы).
  - `can_view_question_image(viewer: User, owner_id: int, db) -> bool` — viewer — сам автор, админ, или ученик, чей учитель группы == owner_id.
  - `GET /media/questions/{owner_id}/{name}` — те же правила пути/ответа, что у аватаров; путь должен существовать среди `Question.image` или в `choice_image` options вопросов этого автора.
  - `own_pool(db, user, pool_id) -> Pool`, `own_question(db, user, question_id) -> Question` — чужие → 404 (админ — любые).
  - Маршруты: `GET /tasks` (вкладка «Задания»: пулы и тесты учителя), `POST /tasks/pools` (title, description, level), `GET /tasks/pools/{id}`, `POST /tasks/pools/{id}/edit`, `POST /tasks/pools/{id}/delete` (только пустой пул; иначе «Сначала удалите вопросы»), `GET /tasks/pools/{id}/questions/new?type=…`, `POST /tasks/pools/{id}/questions`, `GET /tasks/questions/{id}/edit`, `POST /tasks/questions/{id}`, `POST /tasks/questions/{id}/delete` (есть ответы в AttemptAnswer → `is_active=False`, иначе удалить и картинки).
  - Форма вопроса: поля зависят от типа; повторяющиеся строки (варианты, пары, accepted) — `name="options"` и т. п. списками, добавление/удаление строк через `editor.js`; для `choice_image` — файл на каждый вариант; ошибки `PayloadError` показываются над формой с сохранением введённого.
  - В меню учителя (и админа) пункт «Задания» → `/tasks`.

- [ ] **Step 1: failing tests** — `test_create_pool_requires_level_from_list`, `test_pool_foreign_404`, `test_create_each_question_type` (по одному на тип, `choice_image` с двумя PNG), `test_payload_error_keeps_input`, `test_delete_answered_question_hides_it`, `test_delete_nonempty_pool_refused`, `test_student_forbidden_on_tasks` (403), `test_question_image_visibility` (автор 200, ученик его группы 200, ученик чужого учителя 404, аноним → /login), `test_question_image_size` (≤ 1280, ≤ 400 КБ, без EXIF).
- [ ] **Step 2–4:** FAIL → реализация → PASS; руками 375px: создать пул A1, по вопросу каждого типа.
- [ ] **Step 5: Commit** «Задания: пулы и редактор вопросов».

---

### Task 4: Тесты, назначения, настройки ежедневки, подбор задания на день

**Files:** Create `app/daily.py` (часть подбора), `app/templates/tasks/tests.html`, `test.html`, `group_daily.html`; Modify `app/routes/tasks.py`, `app/templates/teacher/group.html` (ссылка «Задания группы»); Test `tests/test_daily.py`, `tests/test_tasks_routes.py`.

**Interfaces — Produces:**
- `moscow_today(now: datetime | None = None) -> date` (в `app/timeutil.py`).
- `settings_for(db, group_id: int) -> DailySettings` — создаёт со значениями по умолчанию, если нет.
- `@dataclass TodaySource(source: str, test_id: int | None, question_ids: list[int])`.
- `todays_source(db, student: User, day: date) -> TodaySource | None`:
  1. назначение ученику на `day` → тест; 2. назначение группе → тест; вопросы теста по position, только активные;
  3. иначе пулы группы → `random.Random(f"{student.id}:{day.isoformat()}")`, `question_count` активных вопросов; сначала из не встречавшихся ученику в попытках за 7 дней до `day`, недостающее — из встречавшихся; если вопросов меньше `question_count` — все;
  4. ничего (или 0 вопросов) → `None`.
- Маршруты учителя: тесты (`GET/POST /tasks/tests`, `GET /tasks/tests/{id}`, добавить вопрос из своих пулов / создать вопрос прямо в тесте (pool_id=None), порядок ↑↓, удалить из теста), `GET /tasks/groups/{group_id}` — страница «Задания группы»: назначенные пулы (галочки из пулов учителя), настройки ежедневки (числа в допустимых пределах, иначе ошибка «Число вопросов — от 1 до 30» / «Попыток — от 1 до 10»), назначения тестов на даты (группе или ученику группы; конфликт уникальности → «На эту дату уже назначен тест»).

- [ ] **Step 1: failing tests**

```python
def test_moscow_day_boundary():
    assert moscow_today(datetime(2026, 10, 25, 20, 59)) == date(2026, 10, 25)  # 23:59 МСК
    assert moscow_today(datetime(2026, 10, 25, 21, 0)) == date(2026, 10, 26)   # 00:00 МСК

def test_student_assignment_beats_group(db): ...
def test_pool_selection_deterministic_and_sized(db):  # 12 вопросов в пуле, count=5 → 5, одинаково при повторе
def test_pool_avoids_last_7_days(db):  # попытка вчера с вопросами 1–5 → сегодня другие
def test_fewer_questions_than_count(db):  # 3 вопроса, count=5 → 3
def test_no_source_returns_none(db):
def test_inactive_questions_excluded(db):
def test_settings_validation(client, db):
def test_assignment_conflict_friendly(client, db):
def test_foreign_group_tasks_404(client, db):
```
- [ ] **Step 2–4:** FAIL → реализация → PASS.
- [ ] **Step 5: Commit** «Задания: тесты, назначения, настройки группы, подбор на день».

---

### Task 5: Попытки и плеер ученика

**Files:** Modify `app/daily.py`; Create `app/routes/daily.py`, `app/templates/student/player.html`, `result.html`, `app/static/js/player.js`; Modify `app/templates/student/home.html`, `app/routes/auth.py` (главная ученика берёт данные из `daily`), `app/static/css/app.css`; Test `tests/test_player.py`.

**Interfaces — Produces (`app/daily.py`):**
- `class NoAttemptsLeft(Exception)`, `class NothingToday(Exception)`.
- `todays_status(db, student, now=None) -> dict` — `{"state": "none"|"ready"|"in_progress"|"done", "attempts_used", "max_attempts", "best": (correct, total) | None, "streak": int}`.
- `start_or_resume(db, student, now=None) -> Attempt` — есть незавершённая сегодняшняя → она; иначе если попыток < max → новая: `question_ids` = из первой сегодняшней попытки (если была) иначе `todays_source`; порядок перемешан по `f"{student.id}:{day}:{n}"`, если `shuffle_questions`; иначе `NoAttemptsLeft` / `NothingToday`.
- `submit_answer(db, attempt, question_id: int, answer: dict, now=None) -> dict` — вопрос должен быть следующим неотвеченным в `question_ids` (ответ на уже отвеченный → вернуть сохранённый результат, без второй записи; на «чужой» вопрос → `ValueError`); сохраняет `AttemptAnswer`; для `match_pairs` — `grade_pairs(attempt.pair_mistakes.get(str(qid), 0))`; на последнем вопросе — `finished_at`, `correct_count`, `streak.register_completion`; возвращает `{"correct", "typo", "correct_answer", "finished", "next_index"}`.
- `check_pair(db, attempt, question_id, left: int, right: int) -> bool` — верна ли пара; неверная увеличивает `pair_mistakes[qid]`.
- Вопрос, удалённый/скрытый после старта попытки, всё равно показывается и проверяется (берётся по id без фильтра is_active); физически удалённый (нет строки) — пропускается и не считается в total.

**Маршруты (`app/routes/daily.py`, только роль student):**
- `GET /` (главная ученика): серия, карточка «Задание на сегодня» по `todays_status` (Начать / Продолжить / Пройдено k из n, осталось попыток m / «Сегодня заданий нет»).
- `POST /daily/start` → 303 `/daily/play`; `NoAttemptsLeft` → главная с «Попытки на сегодня закончились»; `NothingToday` → главная.
- `GET /daily/play` — нет активной попытки → 303 `/`; страница с `<script type="application/json" id="player-data">` (публичные виды вопросов с `seed=f"{student.id}:{day}"`, индекс текущего вопроса, attempt_id) и `player.js`.
- `POST /daily/answer` (JSON `{attempt_id, question_id, answer}`) → JSON из `submit_answer`; чужая/завершённая попытка → 404/409.
- `POST /daily/pair` (JSON `{attempt_id, question_id, left, right}`) → `{"ok": bool}`.
- `GET /daily/result/{attempt_id}` — только своя: «Верно k из n», серия, «Ещё раз» если попытки остались.

**Плеер (`player.js`):** сверху крестик (выход на главную с подтверждением) и полоса прогресса; варианты/карточки/плитки/пары крупные; «Проверить» неактивна, пока нет ответа; после проверки выезжает плашка (зелёная «Отлично!» / «Почти! Правильно: …» при опечатке / красная «Правильный ответ: …») и кнопка «Дальше»; лёгкая тряска при ошибке; word_bank — тап по плитке переносит её в строку ответа и обратно; match_pairs — тап левая+правая, верная пара гаснет зелёным, неверная мигает красным; кнопка «Проверить» блокируется после нажатия (защита от двойного тапа); на результате — CSS-конфетти; `prefers-reduced-motion` уважается.

- [ ] **Step 1: failing tests**

```python
def test_full_flow(client, db):  # пул 3 вопроса, start → play содержит player-data без "accepted"/"correct" → 3 ответа → finished, result "Верно"
def test_resume_after_reload(client, db):  # ответил 1 из 3, GET /daily/play → current index 1, тот же набор
def test_double_submit_counted_once(client, db):  # тот же answer дважды → одна строка AttemptAnswer, одинаковый ответ
def test_attempt_limit(client, db):  # max_attempts=1 → второй start → «Попытки на сегодня закончились»
def test_second_attempt_same_questions(client, db):
def test_question_deleted_mid_attempt(client, db):  # учитель скрыл вопрос → попытка доходит до конца
def test_match_pairs_mistake_makes_wrong(client, db):
def test_foreign_attempt_404(client, db):
def test_day_rollover_new_set(db):  # now = 23:59 МСК и 00:01 МСК следующего дня → разные дни, новый набор
def test_teacher_cannot_play(client, db):  # 403
```
- [ ] **Step 2–4:** FAIL → реализация → PASS; руками 375px: пройти задание каждого типа, обновить страницу на середине, двойной тап.
- [ ] **Step 5: Commit** «Задания: плеер ученика и попытки».

---

### Task 6: Серия дней

**Files:** Create `app/streak.py`; Test `tests/test_streak.py`.

**Interfaces — Produces:**
- `had_tasks(db, student, day: date) -> bool` — у группы есть хотя бы один пул или назначение тесту группе/ученику на `day`.
- `register_completion(db, student, day: date) -> None` — первая завершённая попытка дня: если `last_completed_date == day` — ничего; если между `last_completed_date` и `day` нет дней с `had_tasks` — `current += 1`, иначе `current = 1`; `best = max(best, current)`; `last_completed_date = day`.
- `effective_streak(db, student, today: date) -> int` — `current_streak`, если после `last_completed_date` до вчера включительно нет пропущенных дней с заданиями, иначе 0.

Решение: история назначения пулов не хранится, поэтому пулы считаются постоянными — «день без заданий» = у группы сейчас нет пулов и на этот день нет назначения теста группе/ученику.

- [ ] **Step 1: failing tests** — `test_consecutive_days`, `test_gap_with_tasks_resets`, `test_gap_without_tasks_keeps` (группа без пулов, пропущенный день без назначения — серия сохраняется), `test_best_kept`, `test_effective_streak_zero_after_missed_day`, `test_same_day_twice_counts_once`.
- [ ] **Step 2–4, 5: Commit** «Задания: серия дней».

> **Порядок выполнения: Task 6 выполняется до Task 5** (Task 5 вызывает `register_completion` и `effective_streak`).

---

### Task 7: Результаты для учителя

**Files:** Create `app/templates/tasks/results_day.html`, `student_results.html`; Modify `app/routes/tasks.py`, `app/templates/teacher/group.html`, `app/templates/teacher/student.html`; Test `tests/test_results.py`.

**Interfaces — Produces:**
- `GET /tasks/groups/{group_id}/results?day=YYYY-MM-DD` (по умолчанию сегодня МСК) — по ученику: статус (не начинал / в процессе / пройдено), лучший результат k из n, число попыток, серия; ссылки на ученика; переключение дня ←/→.
- `GET /tasks/students/{student_id}/results` — последние 14 дней: попытки с датой, источником (тест «название» / пул), k из n; раскрытие попытки — список вопросов с ответом ученика (текстом), верно/неверно, правильный ответ.
- На странице группы — блок «Сегодня: прошли X из Y» со ссылкой на результаты.

- [ ] **Step 1: failing tests** — `test_day_results_statuses`, `test_attempt_detail_shows_answers`, `test_foreign_results_404`, `test_day_param_invalid_falls_back_to_today`.
- [ ] **Step 2–4, 5: Commit** «Задания: результаты для учителя».

---

### Task 8: Финальная проверка этапа

- [ ] `pytest -q` зелёный.
- [ ] Локально 375×812 и ≥ 900px: учитель — пул A1, по вопросу каждого типа, тест, назначение на завтра, настройки группы; ученик — задание, обновление страницы посреди, повторная попытка, результат, серия; учитель — результаты дня и ученика.
- [ ] README: раздел «Задания» (что умеет учитель и ученик).
- [ ] Отчёт заказчику: сценарии и что не проверено.

---

## Покрытие спеки

| Спека | Задача |
|---|---|
| §5.2 обновление 00:00 МСК | 4 (`moscow_today`), 5 |
| §5.2 настройки группы: число вопросов, попытки, перемешивание | 4, 5 |
| §5.2 штампики за задание | этап 4 (вне плана) |
| §5.2 серия дней | 6, 5 |
| §5.2 пять типов вопросов | 2, 3, 5 |
| §5.2 тесты, назначение конкретного теста, иначе пул | 4 |
| §5.2 несколько пулов с уровнем A1…C2+, назначение группам | 1, 3, 4 |
| §8.2 детерминированный подбор, исключение 7 дней | 4 |
| §8.3 нормализация, опечатка, проверка на сервере | 2, 5 |
| §8.4 плеер: прогресс, «Проверить», плашка, результат, конфетти, продолжение после обновления | 5 |
| §9 экраны ученика и учителя | 3, 4, 5, 7 |
| §10 доступ к картинкам вопросов | 3 |
