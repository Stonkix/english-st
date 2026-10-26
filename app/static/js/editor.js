// Редактор вопросов: добавление и удаление строк (варианты, пары, ответы). Inline-скриптов нет: CSP.
// Кнопки в разметке скрыты (hidden): без JS форма работает с теми строками, что нарисовал сервер.

function renumber(box) {
  const rows = box.querySelectorAll(":scope > [data-row]");
  rows.forEach((row, i) => {
    // радиокнопка «правильный» хранит номер строки, файловое поле — имя вида option_file_<номер>
    row.querySelectorAll("[data-index-value]").forEach((el) => { el.value = String(i); });
    row.querySelectorAll("[data-name-prefix]").forEach((el) => { el.name = el.dataset.namePrefix + i; });
  });
  const min = Number(box.dataset.min);
  const max = Number(box.dataset.max);
  rows.forEach((row) => {
    row.querySelectorAll("[data-row-remove]").forEach((b) => { b.hidden = rows.length <= min; });
  });
  box.querySelectorAll(":scope > [data-row-add]").forEach((b) => { b.hidden = rows.length >= max; });
}

function initRows() {
  document.querySelectorAll("[data-rows]").forEach((box) => {
    const template = box.querySelector(":scope > template[data-row-template]");
    renumber(box);
    box.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest("[data-row-remove]")) {
        target.closest("[data-row]").remove();
        renumber(box);
      } else if (target.closest("[data-row-add]") && template) {
        const row = template.content.firstElementChild.cloneNode(true);
        template.before(row);
        renumber(box);
        const first = row.querySelector("input:not([type=radio]):not([type=hidden])");
        if (first) first.focus();
      }
    });
  });
}

// Выбрали новый файл — старый предпросмотр рядом уже не показывает то, что будет сохранено
function initThumbs() {
  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    const holder = input.closest("[data-row]") || input.closest("details");
    if (holder) holder.querySelectorAll("[data-thumb]").forEach((img) => { img.hidden = true; });
  });
}

initRows();
initThumbs();
