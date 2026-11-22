// Общий JS сайта. Отклик кнопок на нажатие сделан на CSS, здесь — только то, без чего не обойтись.
// htmx подключается отдельно (vendor/htmx.min.js).

// ---------- Пароль: кнопка «показать» ----------
// Кнопки в разметке скрыты (hidden): без JS они были бы бесполезны.
function initPasswordToggles(root) {
  root.querySelectorAll("[data-toggle-password]").forEach((button) => {
    const input = document.getElementById(button.dataset.togglePassword);
    if (!input) return;
    button.hidden = false;
    button.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Hide" : "Show";
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
  charset: (p) => /^[!-~]*$/.test(p),
};

function initPasswordRules(root) {
  root.querySelectorAll("[data-rules-for]").forEach((list) => {
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

// Главное меню остаётся на странице, а HTMX обновляет содержимое и счётчики.
const tabNavigation = document.querySelector("[data-tab-navigation]");
if (tabNavigation) {
  const status = document.getElementById("tab-navigation-status");
  const scrollRestoreKey = "englishst:tab-scroll-restore";
  document.addEventListener("htmx:beforeHistoryUpdate", (event) => {
    if (event.detail.boosted && event.detail.history.type === "push") {
      history.replaceState({ ...history.state, tabScrollY: scrollY }, "");
    }
  });
  // При возврате HTMX перезагружает страницу и заменяет history.state.
  // Передаём новой странице только позицию прокрутки, без копии HTML.
  window.addEventListener("popstate", (event) => {
    if (!Number.isFinite(event.state?.tabScrollY)) return;
    try {
      sessionStorage.setItem(scrollRestoreKey, JSON.stringify({ url: location.href, top: event.state.tabScrollY }));
    } catch {
      // Если браузер отключил хранилище, остаётся штатная прокрутка.
    }
  }, true);
  window.addEventListener("pageshow", (event) => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(scrollRestoreKey));
      sessionStorage.removeItem(scrollRestoreKey);
      if (!event.persisted && saved?.url === location.href) {
        history.replaceState({ ...history.state, tabScrollY: saved.top }, "");
        requestAnimationFrame(() => window.scrollTo(0, saved.top));
      }
    } catch {
      // Переход работает и без сохранённой позиции.
    }
  });
  tabNavigation.addEventListener("htmx:beforeRequest", (event) => {
    if (event.target.href === location.href) {
      event.preventDefault();
      return;
    }
    status.textContent = "";
  });
  for (const failure of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
    tabNavigation.addEventListener(failure, () => {
      status.textContent = "Couldn’t open this tab. Try again.";
    });
  }
  document.addEventListener("htmx:afterSettle", (event) => {
    const main = event.target;
    if (main.id !== "main" || !event.detail.boosted) return;
    for (const link of tabNavigation.querySelectorAll(".tabbar-item")) {
      const active = link.pathname === location.pathname;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    initPasswordToggles(main);
    initPasswordRules(main);
    main.focus({ preventScroll: true });
  });
}

// ---------- Подтверждение опасных действий ----------
let confirmationPending = false;
function confirmAction(message, { title = "Confirm action", label = "Continue", danger = false } = {}) {
  const dialog = document.getElementById("action-confirm");
  if (!dialog || confirmationPending || typeof dialog.showModal !== "function") return Promise.resolve(false);
  const trigger = document.activeElement;
  dialog.querySelector("#action-confirm-title").textContent = title;
  dialog.querySelector("#action-confirm-message").textContent = message;
  const button = dialog.querySelector("[data-confirm-button]");
  button.textContent = label;
  button.classList.toggle("btn-danger", danger);
  button.classList.toggle("btn-primary", !danger);
  dialog.showModal();
  confirmationPending = true;
  return new Promise((resolve) => {
    const listeners = new AbortController();
    const finish = (confirmed) => {
      listeners.abort();
      dialog.close();
      confirmationPending = false;
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus({ preventScroll: true });
      resolve(confirmed);
    };
    dialog.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      finish(event.submitter?.value === "confirm");
    }, { signal: listeners.signal });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
    }, { signal: listeners.signal });
  });
}

// Повторяем отправку с исходной кнопкой после согласия, сохраняя обычное поведение формы.
const confirmedForms = new WeakSet();
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.confirm || confirmedForms.has(form)) return;
  event.preventDefault();
  const submitter = event.submitter;
  if (!(await confirmAction(form.dataset.confirm)) || !form.isConnected) return;
  if (submitter && submitter.form !== form) return;
  confirmedForms.add(form);
  try {
    form.requestSubmit(submitter ?? undefined);
  } finally {
    confirmedForms.delete(form);
  }
}, true);

document.addEventListener("change", (event) => {
  const input = event.target;
  if (input instanceof HTMLInputElement && input.hasAttribute("data-avatar-upload") && input.files?.length) {
    input.form?.requestSubmit();
  }
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

// ---------- PWA: service worker кэширует только статику (см. static/js/sw.js) ----------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {}); // без него сайт работает так же
  });
}

initPasswordToggles(document);
initPasswordRules(document);
initLoginSuggest();
