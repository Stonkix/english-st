// Плеер ежедневного задания. Вопросы (без правильных ответов) лежат в #player-data,
// ответ проверяет сервер: POST /daily/answer, для пар — POST /daily/pair.
// Всё состояние попытки хранится на сервере: обновление страницы продолжает с того же вопроса.
(() => {
  const dataEl = document.getElementById("player-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const questions = data.questions;

  const stage = document.getElementById("player-stage");
  const fill = document.getElementById("player-progress");
  const bar = fill.parentElement;
  const checkBtn = document.getElementById("player-check");
  const net = document.getElementById("player-net");
  const sheet = document.getElementById("player-sheet");
  const sheetTitle = document.getElementById("sheet-title");
  const sheetText = document.getElementById("sheet-text");
  const nextBtn = document.getElementById("player-next");

  let index = data.index;
  let getAnswer = () => null; // ответ на текущий вопрос или null, пока ответа нет
  let busy = false; // идёт проверка или показан результат: второй тап ничего не делает
  let result = null; // ответ сервера на текущий вопрос

  // ---------- помощники ----------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text; // только textContent: тексты учителя не становятся разметкой
    return node;
  }

  function button(className, text) {
    const b = el("button", className, text);
    b.type = "button";
    return b;
  }

  function setProgress(done) {
    fill.style.width = `${(done / questions.length) * 100}%`;
    bar.setAttribute("aria-valuenow", String(done));
  }

  function refreshCheck() {
    checkBtn.disabled = busy || getAnswer() === null;
  }

  function shake(node) {
    node.classList.remove("shake");
    void node.offsetWidth; // перезапуск анимации
    node.classList.add("shake");
  }

  // Ответ не JSON или ошибка (попытка закончилась, вышла сессия, вопрос удалили) —
  // перезагружаем страницу: сервер сам покажет нужный экран. Нет связи — исключение.
  async function post(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    });
    const json = (resp.headers.get("content-type") || "").includes("application/json");
    if (!resp.ok || resp.redirected || !json) {
      window.location.reload();
      return null;
    }
    return resp.json();
  }

  // ---------- типы вопросов ----------
  function choice(q, body, withImages) {
    const list = el("div", withImages ? "options options-images" : "options");
    let selected = null;
    q.options.forEach((option, i) => {
      const b = button(withImages ? "option option-card" : "option");
      if (withImages) {
        const img = el("img");
        img.src = option.image_url;
        img.alt = option.caption || `Картинка ${i + 1}`;
        img.loading = "lazy";
        b.append(img);
        if (option.caption) b.append(el("span", "option-caption", option.caption));
      } else {
        b.textContent = option.text;
      }
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => {
        if (busy) return;
        list.querySelectorAll(".option").forEach((o) => {
          o.classList.remove("is-selected");
          o.setAttribute("aria-pressed", "false");
        });
        b.classList.add("is-selected");
        b.setAttribute("aria-pressed", "true");
        selected = { id: option.id, node: b };
        refreshCheck();
      });
      list.append(b);
    });
    body.append(list);
    getAnswer = () => (selected ? { option: selected.id } : null);
    return (ok) => selected && selected.node.classList.add(ok ? "is-right" : "is-wrong");
  }

  function textInput(q, body) {
    const input = el("input", "answer-input");
    Object.assign(input, { type: "text", maxLength: 200, autocomplete: "off", spellcheck: false, placeholder: "Напиши ответ" });
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("enterkeyhint", "done");
    input.setAttribute("aria-label", "Ответ");
    input.addEventListener("input", refreshCheck);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (result) next();
      else check();
    });
    body.append(input);
    getAnswer = () => (input.value.trim() ? { text: input.value } : null);
    setTimeout(() => input.focus({ preventScroll: true }), 50);
    return (ok) => {
      input.readOnly = true;
      input.classList.add(ok ? "is-right" : "is-wrong");
    };
  }

  function wordBank(q, body) {
    const line = el("div", "wb-line");
    line.setAttribute("aria-label", "Твой ответ");
    const bank = el("div", "wb-bank");
    q.tiles.forEach((tile) => {
      const slot = el("span", "wb-slot");
      const b = button("tile", tile.word);
      b.dataset.id = String(tile.id);
      b.addEventListener("click", () => {
        if (busy) return;
        if (b.parentElement === line) {
          slot.append(b); // обратно на своё место в банке
          slot.classList.remove("is-empty");
          slot.style.width = "";
        } else {
          slot.style.width = `${b.offsetWidth}px`; // на месте плитки остаётся «тень» — банк не прыгает
          slot.classList.add("is-empty");
          line.append(b);
        }
        refreshCheck();
      });
      slot.append(b);
      bank.append(slot);
    });
    body.append(line, bank);
    getAnswer = () => {
      const ids = [...line.querySelectorAll(".tile")].map((t) => Number(t.dataset.id));
      return ids.length ? { tiles: ids } : null;
    };
    return (ok) => line.classList.add(ok ? "is-right" : "is-wrong");
  }

  function matchPairs(q, body) {
    const grid = el("div", "pairs");
    const cols = { left: el("div", "pair-col"), right: el("div", "pair-col") };
    const picked = { left: null, right: null };
    let matched = 0;
    let waiting = false;

    function pick(side, item, b) {
      if (busy || waiting || b.disabled) return;
      if (picked[side]) picked[side].b.classList.remove("is-selected");
      if (picked[side] && picked[side].b === b) {
        picked[side] = null; // повторный тап снимает выбор
        return;
      }
      picked[side] = { id: item.id, b };
      b.classList.add("is-selected");
      if (picked.left && picked.right) send();
    }

    async function send() {
      const { left, right } = picked;
      picked.left = picked.right = null;
      waiting = true;
      net.hidden = true;
      let res = null;
      try {
        res = await post("/daily/pair", { attempt_id: data.attempt_id, question_id: q.id, left: left.id, right: right.id });
      } catch {
        net.hidden = false;
      }
      waiting = false;
      [left.b, right.b].forEach((b) => b.classList.remove("is-selected"));
      if (!res) return;
      if (res.ok) {
        [left.b, right.b].forEach((b) => {
          b.classList.add("is-matched");
          b.disabled = true;
        });
        matched += 1;
        refreshCheck();
        if (matched === q.left.length) check(); // все пары собраны — проверяем сами
      } else {
        [left.b, right.b].forEach((b) => {
          b.classList.remove("is-wrong");
          void b.offsetWidth;
          b.classList.add("is-wrong");
          setTimeout(() => b.classList.remove("is-wrong"), 600);
        });
        shake(grid);
      }
    }

    for (const side of ["left", "right"]) {
      q[side].forEach((item) => {
        const b = button("option pair", item.text);
        b.addEventListener("click", () => pick(side, item, b));
        cols[side].append(b);
      });
      grid.append(cols[side]);
    }
    body.append(el("p", "muted q-hint", "Нажми на слово слева, потом на его пару справа"), grid);
    getAnswer = () => (matched === q.left.length ? {} : null);
    return () => {};
  }

  // ---------- ход игры ----------
  let markAnswer = () => {};

  function render() {
    const q = questions[index];
    if (!q) {
      window.location.reload(); // вопросы кончились, а результата нет — пусть решит сервер
      return;
    }
    busy = false;
    result = null;
    net.hidden = true;
    sheet.classList.remove("is-open", "is-good", "is-bad");
    sheet.hidden = true;
    setProgress(index);

    const body = el("div", "question");
    if (q.image_url) {
      const img = el("img", "q-image");
      img.src = q.image_url;
      img.alt = "";
      body.append(img);
    }
    body.append(el("h1", "q-prompt", q.prompt));
    if (q.type === "choice_text") markAnswer = choice(q, body, false);
    else if (q.type === "choice_image") markAnswer = choice(q, body, true);
    else if (q.type === "text_input") markAnswer = textInput(q, body);
    else if (q.type === "word_bank") markAnswer = wordBank(q, body);
    else markAnswer = matchPairs(q, body);
    stage.replaceChildren(body);
    window.scrollTo(0, 0);
    refreshCheck();
  }

  async function check() {
    const answer = getAnswer();
    if (busy || answer === null) return;
    busy = true; // защита от двойного тапа: второй клик сюда уже не пройдёт
    checkBtn.disabled = true;
    net.hidden = true;
    let res;
    try {
      res = await post("/daily/answer", { attempt_id: data.attempt_id, question_id: questions[index].id, answer });
    } catch {
      busy = false; // нет связи: ответ не потерян, можно нажать ещё раз
      net.hidden = false;
      refreshCheck();
      return;
    }
    if (res) showResult(res);
  }

  function showResult(res) {
    result = res;
    markAnswer(res.correct);
    setProgress(res.next_index);
    if (res.correct && !res.typo) {
      sheetTitle.textContent = "Отлично!";
      sheetText.textContent = "";
    } else if (res.correct) {
      sheetTitle.textContent = "Почти!";
      sheetText.textContent = `Правильно пишется: ${res.correct_answer}`;
    } else {
      sheetTitle.textContent = "Не совсем так";
      sheetText.textContent = `Правильный ответ: ${res.correct_answer}`;
      shake(stage);
    }
    sheetText.hidden = sheetText.textContent === "";
    sheet.classList.add(res.correct ? "is-good" : "is-bad");
    nextBtn.className = `btn btn-block btn-big ${res.correct ? "btn-primary" : "btn-danger"}`;
    nextBtn.textContent = res.finished ? "К результату" : "Дальше";
    sheet.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => sheet.classList.add("is-open")));
    nextBtn.focus({ preventScroll: true });
  }

  function next() {
    const res = result;
    if (!res) return;
    result = null; // второй тап по «Дальше» не перепрыгнет вопрос
    if (res.finished) {
      window.location.href = `/daily/result/${data.attempt_id}`;
      return;
    }
    index = res.next_index;
    render();
  }

  checkBtn.addEventListener("click", check);
  nextBtn.addEventListener("click", next);
  render();
})();
