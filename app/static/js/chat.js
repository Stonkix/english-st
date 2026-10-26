// Переписка: сообщения по WebSocket (/ws/chat), догрузка пропущенного после обрыва, история при прокрутке вверх,
// стикеры, фото, удаление. Данные комнаты — в #chat-data (см. routes/chat.py::room_page).
(() => {
  const data = JSON.parse(document.getElementById("chat-data").textContent);
  const list = document.getElementById("chat-messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const errorBox = document.getElementById("chat-error");
  const statusBox = document.getElementById("chat-status");
  const stickerBtn = document.getElementById("chat-sticker-btn");
  const stickerPanel = document.getElementById("chat-stickers");
  const imageInput = document.getElementById("chat-image");

  const ROOM = data.room_id;
  const byId = new Map(); // id -> элемент сообщения
  let firstId = null; // самое старое показанное
  let lastId = 0; // самое новое полученное: от него догружаем после обрыва
  let hasMore = data.has_more;
  let loadingOlder = false;
  let socket = null;
  let retry = 0;
  let pending = new Map(); // client_id -> текст (вернуть в поле, если сервер отказал)

  // ---------- вывод ----------

  const DAY_FMT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });

  function dayLabel(day) {
    const today = new Date();
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const yesterday = new Date(today.getTime() - 86400000);
    if (day === iso(today)) return "Сегодня";
    if (day === iso(yesterday)) return "Вчера";
    const [y, m, d] = day.split("-").map(Number);
    return DAY_FMT.format(new Date(y, m - 1, d));
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function avatar(author) {
    const box = el("span", "avatar avatar-sm");
    box.setAttribute("aria-hidden", "true");
    if (author.avatar) {
      const img = el("img");
      img.src = author.avatar;
      img.alt = "";
      img.width = img.height = 36;
      box.append(img);
    } else {
      box.textContent = author.initials;
    }
    return box;
  }

  function render(m) {
    const mine = m.author.id === data.me;
    const node = el("div", "msg" + (mine ? " is-mine" : "") + (m.deleted ? " is-deleted" : ""));
    node.dataset.id = m.id;
    node.dataset.day = m.day;
    node.dataset.author = m.author.id;
    if (!mine && data.kind === "group") node.append(avatar(m.author));
    const media = m.deleted ? "" : { sticker: " is-sticker", image: " is-media" }[m.kind] || "";
    const body = el("div", "msg-body" + media);
    if (!mine && data.kind === "group") {
      const name = el("div", "msg-name", m.author.name);
      if (m.author.teacher) name.append(" ", el("span", "msg-teacher", "учитель"));
      body.append(name);
    }
    if (m.deleted) {
      body.append(el("p", "msg-text", "Сообщение удалено"));
    } else if (m.kind === "sticker") {
      const img = el("img", "msg-sticker");
      img.src = m.sticker;
      img.alt = m.label;
      img.width = img.height = 112;
      body.append(img);
    } else if (m.kind === "image" && m.image) {
      const link = el("a", "msg-image-link");
      link.href = m.image;
      link.target = "_blank";
      link.rel = "noopener";
      const img = el("img", "msg-image");
      img.src = m.image;
      img.alt = "Фото";
      img.loading = "lazy";
      link.append(img);
      body.append(link);
    } else {
      body.append(el("p", "msg-text", m.text || ""));
      if (m.original) {
        const orig = el("p", "msg-original");
        orig.append(el("strong", "", "Отфильтровано. Было: "), m.original);
        body.append(orig);
        if (!m.author.teacher) {
          // учитель сразу забирает штампик: карточка ученика с готовой причиной
          const take = el("a", "msg-take", "Забрать штампик за мат");
          take.href = `/teacher/students/${m.author.id}?take_reason=${encodeURIComponent(data.swear_reason)}#take`;
          body.append(take);
        }
      }
    }
    const meta = el("div", "msg-meta");
    meta.append(el("time", "", m.time));
    if (!m.deleted && (mine || data.moderator)) {
      const del = el("button", "msg-delete", "Удалить");
      del.type = "button";
      del.dataset.delete = m.id;
      meta.append(del);
    }
    body.append(meta);
    node.append(body);
    return node;
  }

  // Подряд идущие сообщения одного автора за день: имя и фото — только у первого
  function regroup() {
    let prev = null;
    for (const node of list.children) {
      const msg = node.classList.contains("msg");
      node.classList.toggle("is-cont", msg && !!prev && prev.dataset.author === node.dataset.author);
      prev = msg ? node : null;
    }
  }

  function separator(day) {
    const sep = el("div", "chat-day", dayLabel(day));
    sep.dataset.sep = day;
    return sep;
  }

  function nearBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 120;
  }

  function scrollToBottom() {
    list.scrollTop = list.scrollHeight;
  }

  // Новое сообщение снизу (или замена уже показанного — так дубль после догрузки не появится)
  function append(m) {
    const old = byId.get(m.id);
    if (old) {
      const fresh = render(m);
      old.replaceWith(fresh);
      byId.set(m.id, fresh);
      return;
    }
    if (m.id < lastId) return; // старее последнего показанного — придёт с историей
    const lastMsg = list.querySelector(".msg:last-of-type");
    if (!lastMsg || lastMsg.dataset.day !== m.day) list.append(separator(m.day));
    const node = render(m);
    list.append(node);
    byId.set(m.id, node);
    lastId = Math.max(lastId, m.id);
    if (firstId === null) firstId = m.id;
    hideEmpty();
    regroup();
  }

  // Порция истории сверху; прокрутка остаётся на том же сообщении
  function prepend(messages) {
    if (!messages.length) return;
    const before = list.scrollHeight;
    const firstSep = list.querySelector("[data-sep]");
    if (firstSep && firstSep.dataset.sep === messages[messages.length - 1].day) firstSep.remove();
    const frag = document.createDocumentFragment();
    let day = null;
    for (const m of messages) {
      if (byId.has(m.id)) continue;
      if (m.day !== day) {
        frag.append(separator(m.day));
        day = m.day;
      }
      const node = render(m);
      frag.append(node);
      byId.set(m.id, node);
    }
    list.prepend(frag);
    firstId = messages[0].id;
    regroup();
    list.scrollTop += list.scrollHeight - before;
  }

  function showEmpty() {
    if (!byId.size && !list.querySelector(".chat-empty")) {
      list.append(el("p", "chat-empty", "Сообщений пока нет. Напиши первым!"));
    }
  }

  function hideEmpty() {
    const empty = list.querySelector(".chat-empty");
    if (empty) empty.remove();
  }

  function markDeleted(id) {
    const node = byId.get(id);
    if (!node) return;
    node.classList.add("is-deleted");
    const body = node.querySelector(".msg-body");
    body.classList.remove("is-media", "is-sticker");
    body.querySelectorAll(".msg-text, .msg-sticker, .msg-image-link, .msg-original, .msg-take, .msg-delete").forEach((n) => n.remove());
    body.insertBefore(el("p", "msg-text", "Сообщение удалено"), body.querySelector(".msg-meta"));
  }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }

  // ---------- связь ----------

  function sendRead() {
    if (lastId && document.visibilityState === "visible" && socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "read", room_id: ROOM, message_id: lastId }));
    }
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!response.ok || body === null) {
      throw new Error((body && body.error) || "Не получилось. Попробуй ещё раз.");
    }
    return body;
  }

  // Всё, что пришло, пока не было связи
  async function catchUp() {
    for (let guard = 0; guard < 20; guard += 1) {
      const body = await fetchJSON(`/chat/${ROOM}/messages?after=${lastId}`);
      const stick = nearBottom();
      body.messages.forEach(append);
      if (stick) scrollToBottom();
      if (!body.has_more) break;
    }
    sendRead();
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/chat`);
    socket.addEventListener("open", () => {
      retry = 0;
      statusBox.hidden = true;
      sendBtn.disabled = !input.value.trim();
      catchUp().catch(() => {});
    });
    socket.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "message" && msg.message.room_id === ROOM) {
        pending.delete(msg.client_id);
        const stick = nearBottom() || msg.message.author.id === data.me;
        append(msg.message);
        if (stick) scrollToBottom();
        sendRead();
      } else if (msg.type === "deleted" && msg.room_id === ROOM) {
        markDeleted(msg.id);
      } else if (msg.type === "error") {
        const text = pending.get(msg.client_id);
        pending.delete(msg.client_id);
        if (text !== undefined && !input.value) {
          input.value = text;
          autosize();
        }
        showError(msg.error);
      }
    });
    socket.addEventListener("close", () => {
      statusBox.hidden = false;
      // пауза растёт: 1, 2, 4… до 30 секунд, с разбросом, чтобы весь класс не переподключался разом
      const delay = Math.min(30000, 1000 * 2 ** retry) * (0.75 + Math.random() / 2);
      retry += 1;
      setTimeout(connect, delay);
    });
  }

  // ---------- действия ----------

  function sendPayload(payload, text) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      showError("Нет связи. Сообщение не отправлено — попробуй ещё раз через минуту.");
      return false;
    }
    const clientId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    if (text !== undefined) pending.set(clientId, text);
    socket.send(JSON.stringify({ type: "send", room_id: ROOM, client_id: clientId, ...payload }));
    showError("");
    return true;
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    sendBtn.disabled = !input.value.trim();
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    if (sendPayload({ text }, text)) {
      input.value = "";
      autosize();
    }
    input.focus();
  });

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    // Enter отправляет, Shift+Enter — новая строка (на телефоне Enter остаётся переносом строки)
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && window.matchMedia("(hover: hover)").matches) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  for (const s of data.stickers) {
    const button = el("button", "chat-sticker");
    button.type = "button";
    button.dataset.sticker = s.id;
    button.setAttribute("aria-label", s.label);
    const img = el("img");
    img.src = s.url;
    img.alt = "";
    img.width = img.height = 56;
    button.append(img);
    stickerPanel.append(button);
  }
  stickerBtn.addEventListener("click", () => {
    stickerPanel.hidden = !stickerPanel.hidden;
    stickerBtn.setAttribute("aria-expanded", String(!stickerPanel.hidden));
  });
  stickerPanel.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sticker]");
    if (!button) return;
    if (sendPayload({ sticker: button.dataset.sticker })) {
      stickerPanel.hidden = true;
      stickerBtn.setAttribute("aria-expanded", "false");
    }
  });

  imageInput.addEventListener("change", async () => {
    const file = imageInput.files[0];
    imageInput.value = "";
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) {
      showError("Фото больше 15 МБ. Выбери другое.");
      return;
    }
    const body = new FormData();
    body.append("image", file);
    showError("");
    statusBox.textContent = "Отправляем фото…";
    statusBox.hidden = false;
    try {
      const result = await fetchJSON(`/chat/${ROOM}/image`, { method: "POST", body });
      append(result.message);
      scrollToBottom();
    } catch (err) {
      showError(err.message);
    } finally {
      statusBox.textContent = "Нет связи…";
      statusBox.hidden = !!socket && socket.readyState === WebSocket.OPEN;
    }
  });

  list.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete]");
    if (!button || !window.confirm("Удалить сообщение у всех?")) return;
    button.disabled = true;
    try {
      await fetchJSON(`/chat/messages/${button.dataset.delete}/delete`, { method: "POST" });
      markDeleted(Number(button.dataset.delete));
    } catch (err) {
      button.disabled = false;
      showError(err.message);
    }
  });

  list.addEventListener("scroll", async () => {
    if (list.scrollTop > 200 || !hasMore || loadingOlder || firstId === null) return;
    loadingOlder = true;
    try {
      const body = await fetchJSON(`/chat/${ROOM}/messages?before=${firstId}`);
      hasMore = body.has_more;
      prepend(body.messages);
    } catch {
      // прокрутка повторит попытку
    } finally {
      loadingOlder = false;
    }
  });

  document.addEventListener("visibilitychange", sendRead);

  // ---------- старт ----------
  data.messages.forEach(append);
  showEmpty();
  scrollToBottom();
  connect();
})();
