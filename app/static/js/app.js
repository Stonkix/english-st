// Общий JS сайта. Отклик кнопок на нажатие сделан на CSS, здесь — только то, без чего не обойтись.
// htmx подключается отдельно (vendor/htmx.min.js).

// ---------- Пароль: кнопка «показать» ----------
// Кнопки в разметке скрыты (hidden): без JS они были бы бесполезны.
function initPasswordToggles() {
  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    const input = document.getElementById(button.dataset.togglePassword);
    if (!input) return;
    button.hidden = false;
    button.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "скрыть" : "показать";
      button.setAttribute("aria-pressed", String(show));
    });
  });
}

// ---------- Пароль: правила подсвечиваются по мере ввода ----------
// Те же проверки, что в app/auth/passwords.py::validate_password — менять только вместе.
const PASSWORD_CHECKS = {
  length: (p) => p.length >= 10,
  lower: (p) => /[a-z]/.test(p),
  upper: (p) => /[A-Z]/.test(p),
  digit: (p) => /[0-9]/.test(p),
  charset: (p) => /^[\x21-\x7E]*$/.test(p),
};

function initPasswordRules() {
  document.querySelectorAll("[data-rules-for]").forEach((list) => {
    const input = document.getElementById(list.dataset.rulesFor);
    if (!input) return;
    const update = () => {
      const value = input.value;
      list.querySelectorAll("[data-rule]").forEach((item) => {
        const check = PASSWORD_CHECKS[item.dataset.rule];
        // пока поле пустое, ничего не подсвечиваем
        item.classList.toggle("is-ok", value !== "" && !!check && check(value));
      });
    };
    input.addEventListener("input", update);
    update();
  });
}

// ---------- Подтверждение опасных действий ----------
// <form data-confirm="Текст вопроса"> — без согласия форма не отправляется (inline-обработчиков нет: CSP).
document.addEventListener("submit", (event) => {
  const message = event.target instanceof HTMLFormElement ? event.target.dataset.confirm : undefined;
  if (message && !window.confirm(message)) event.preventDefault();
});

// ---------- Защита от двойного нажатия ----------
// <form data-once>: после отправки кнопка блокируется, чтобы второй тап не создал дубль.
// Если страница вернулась из кэша «назад», кнопку возвращаем к жизни.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (event.defaultPrevented || !(form instanceof HTMLFormElement) || form.dataset.once === undefined) return;
  // блокируем после того, как браузер соберёт данные формы
  setTimeout(() => form.querySelectorAll("[type=submit]:not(:disabled)").forEach((b) => {
    b.disabled = true;
    b.dataset.onceDisabled = "";
  }), 0);
});
// возвращаем только те кнопки, что заблокировали сами (выключенные сервером — например, «не хватает штампиков» — не трогаем)
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-once] [data-once-disabled]").forEach((b) => {
    b.disabled = false;
    delete b.dataset.onceDisabled;
  });
});

// ---------- Подсказка логина (форма нового ученика) ----------
// Поля имени и фамилии (data-login-suggest) через htmx просят логин, ответ — простой текст.
// hx-sync на форме отменяет устаревший запрос; #login не заменяется, а только получает value.
// Если учитель правил логин руками (data-manual), подсказку не применяем; очистил поле — подсказки снова работают.
function initLoginSuggest() {
  const login = document.getElementById("login");
  if (!login || !document.querySelector("[data-login-suggest]")) return;
  // логин, пришедший с сервера (форма с ошибками), считаем введённым вручную
  if (login.value !== "") login.dataset.manual = "1";
  login.addEventListener("input", () => {
    if (login.value === "") delete login.dataset.manual;
    else login.dataset.manual = "1";
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    if (!event.target.matches("[data-login-suggest]") || !event.detail.successful) return;
    if (login.dataset.manual) return;
    login.value = event.detail.xhr.responseText;
  });
}

initPasswordToggles();
initPasswordRules();
initLoginSuggest();
