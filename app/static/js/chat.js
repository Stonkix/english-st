// Переписка: сообщения по WebSocket (/ws/chat), догрузка пропущенного после обрыва, история при прокрутке вверх,
// стикеры, фото, реакции, удаление. Данные комнаты — в #chat-data (см. routes/chat.py::room_page).
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
  const emptyReaction = { counts: {}, mine: null };
  const reactionEvents = new Map();
  const pendingReactions = new Set();
  let reactionVersion = 0;
  let reactionTarget = null;
  let press = null;
  let heldMessage = null;
  const reactionMenu = document.createElement("div");
  reactionMenu.id = "chat-reaction-menu";
  reactionMenu.className = "chat-reaction-menu";
  reactionMenu.hidden = true;
  reactionMenu.setAttribute("role", "group");
  reactionMenu.setAttribute("aria-label", "Choose a reaction");
  document.body.append(reactionMenu);
  let firstId = null; // самое старое показанное
  let lastId = 0; // самое новое полученное: от него догружаем после обрыва
  let hasMore = data.has_more;
  let loadingOlder = false;
  let socket = null;
  let retry = 0;
  let pending = new Map(); // client_id -> текст (вернуть в поле, если сервер отказал)

  // ---------- вывод ----------

  const DAY_FMT = new Intl.DateTimeFormat("en", { day: "numeric", month: "long" });

  function dayLabel(day) {
    const today = new Date();
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const yesterday = new Date(today.getTime() - 86400000);
    if (day === iso(today)) return "Today";
    if (day === iso(yesterday)) return "Yesterday";
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

  function addDeletedLabel(body, before = null) {
    body.insertBefore(el("p", "msg-deleted-label", "Deleted"), before);
  }

  function paintReaction(button, { counts, mine }) {
    button.dataset.mine = mine ?? "";
    const visible = data.reactions.filter(({ id }) => counts[id]);
    button.replaceChildren(...visible.map(({ id, emoji }) => {
      const badge = el("span", "msg-reaction-badge", `${emoji} ${counts[id]}`);
      badge.classList.toggle("is-mine", id === mine);
      return badge;
    }));
    if (!visible.length) button.textContent = "☺";
    const summary = visible.map(({ id, label }) => `${label} ${counts[id]}`).join(", ");
    button.setAttribute("aria-label", summary ? `Reactions, ${summary}` : "Choose a reaction");
  }

  function setReaction(id, state) {
    const button = byId.get(id)?.querySelector(".msg-reaction");
    if (button) paintReaction(button, state);
  }

  function recordReaction(id, state) {
    reactionEvents.set(id, { version: ++reactionVersion, state });
    setReaction(id, state);
  }

  function newerReaction(id, since) {
    const event = reactionEvents.get(id);
    return event?.version > since ? event.state : null;
  }

  function render(m, since) {
    const mine = m.author.id === data.me;
    const showDeleted = m.deleted && data.moderator && !m.author.teacher;
    const node = el("div", "msg" + (mine ? " is-mine" : ""));
    node.classList.toggle("is-deleted", m.deleted);
    node.classList.toggle("shows-deleted-content", showDeleted);
    node.dataset.id = m.id;
    node.dataset.day = m.day;
    node.dataset.author = m.author.id;
    node.dataset.studentAuthor = String(!m.author.teacher);
    if (!mine && data.kind === "group") node.append(avatar(m.author));
    const media = !m.deleted || showDeleted ? { sticker: " is-sticker", image: " is-media" }[m.kind] || "" : "";
    const body = el("div", "msg-body" + media);
    if (!mine && data.kind === "group") {
      const name = el("div", "msg-name", m.author.name);
      if (m.author.teacher) name.append(" ", el("span", "msg-teacher", "teacher"));
      body.append(name);
    }
    if (showDeleted) addDeletedLabel(body);
    if (m.deleted && !showDeleted) {
      body.append(el("p", "msg-text", "Message deleted"));
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
      img.alt = "Photo";
      img.loading = "lazy";
      link.append(img);
      body.append(link);
    } else {
      body.append(el("p", "msg-text", m.text || (showDeleted && m.kind === "image" ? "Photo unavailable" : "")));
      if (m.original) {
        const orig = el("p", "msg-original");
        orig.append(el("strong", "", "Filtered. Original: "), m.original);
        body.append(orig);
        if (!m.deleted && !m.author.teacher) {
          // учитель сразу забирает штампик: карточка ученика с готовой причиной
          const take = el("a", "msg-take", "Remove a stamp for swearing");
          take.href = `/teacher/students/${m.author.id}?take_reason=${encodeURIComponent(data.swear_reason)}#take`;
          body.append(take);
        }
      }
    }
    const meta = el("div", "msg-meta");
    if (!m.deleted) {
      const reaction = el("button", "msg-reaction");
      reaction.type = "button";
      reaction.title = "Choose a reaction";
      reaction.setAttribute("aria-controls", reactionMenu.id);
      reaction.setAttribute("aria-expanded", "false");
      paintReaction(reaction, newerReaction(m.id, since) ?? m.reaction ?? emptyReaction);
      meta.append(reaction);
    }
    meta.append(el("time", "", m.time));
    if (!m.deleted && (mine || data.moderator)) {
      const del = el("button", "msg-delete", "Delete");
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
  function append(m, since = 0) {
    const old = byId.get(m.id);
    if (old) {
      const fresh = render(m, since);
      old.replaceWith(fresh);
      byId.set(m.id, fresh);
      return;
    }
    if (m.id < lastId) return; // старее последнего показанного — придёт с историей
    const lastMsg = list.querySelector(".msg:last-of-type");
    if (!lastMsg || lastMsg.dataset.day !== m.day) list.append(separator(m.day));
    const node = render(m, since);
    list.append(node);
    byId.set(m.id, node);
    lastId = Math.max(lastId, m.id);
    if (firstId === null) firstId = m.id;
    hideEmpty();
    regroup();
  }

  // Порция истории сверху; прокрутка остаётся на том же сообщении
  function prepend(messages, since) {
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
      const node = render(m, since);
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
      list.append(el("p", "chat-empty", "No messages yet. Start the conversation!"));
    }
  }

  function hideEmpty() {
    const empty = list.querySelector(".chat-empty");
    if (empty) empty.remove();
  }

  function markDeleted(id) {
    const node = byId.get(id);
    if (!node || node.classList.contains("is-deleted")) return;
    node.classList.add("is-deleted");
    const body = node.querySelector(".msg-body");
    if (reactionTarget && node.contains(reactionTarget)) closeReactionMenu();
    body.querySelector(".msg-reaction")?.remove();
    if (data.moderator && node.dataset.studentAuthor === "true") {
      node.classList.add("shows-deleted-content");
      body.querySelectorAll(".msg-take, .msg-delete").forEach((n) => n.remove());
      addDeletedLabel(body, body.querySelector(".msg-text, .msg-sticker, .msg-image-link, .msg-meta"));
      return;
    }
    body.classList.remove("is-media", "is-sticker");
    body.querySelectorAll(".msg-text, .msg-sticker, .msg-image-link, .msg-original, .msg-take, .msg-delete").forEach((n) => n.remove());
    body.insertBefore(el("p", "msg-text", "Message deleted"), body.querySelector(".msg-meta"));
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
      throw new Error((body && body.error) || "Something went wrong. Try again.");
    }
    return body;
  }

  // Всё, что пришло, пока не было связи
  async function catchUp() {
    for (let guard = 0; guard < 20; guard += 1) {
      const since = reactionVersion;
      const body = await fetchJSON(`/chat/${ROOM}/messages?after=${lastId}`);
      const stick = nearBottom();
      body.messages.forEach((m) => append(m, since));
      if (stick) scrollToBottom();
      if (!body.has_more) break;
    }
    sendRead();
  }

  async function refreshReactions() {
    if (firstId === null) return;
    const first = firstId;
    const last = lastId;
    const since = reactionVersion;
    const states = await fetchJSON(`/chat/${ROOM}/reactions?first=${first}&last=${last}`);
    for (const id of byId.keys()) {
      if (id >= first && id <= last && !newerReaction(id, since)) {
        setReaction(id, states[id] ?? emptyReaction);
      }
    }
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/chat`);
    socket.addEventListener("open", () => {
      retry = 0;
      statusBox.hidden = true;
      sendBtn.disabled = !input.value.trim();
      catchUp().catch(() => {});
      refreshReactions().catch(() => {});
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
      } else if (msg.type === "reaction" && msg.room_id === ROOM) {
        recordReaction(msg.id, msg.state);
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

  function closeReactionMenu(restoreFocus = false) {
    if (!reactionTarget) return;
    reactionTarget.setAttribute("aria-expanded", "false");
    if (restoreFocus) reactionTarget.focus({ preventScroll: true });
    reactionTarget = null;
    reactionMenu.hidden = true;
  }

  function openReactionMenu(button, x, y, focus = false) {
    closeReactionMenu();
    reactionTarget = button;
    button.setAttribute("aria-expanded", "true");
    reactionMenu.replaceChildren(...data.reactions.map(({ id, emoji, label }) => {
      const option = el("button", "chat-reaction-option", emoji);
      option.type = "button";
      option.title = label;
      option.setAttribute("aria-label", label);
      option.setAttribute("aria-pressed", String(button.dataset.mine === id));
      option.dataset.kind = id;
      return option;
    }));
    reactionMenu.style.left = "0px";
    reactionMenu.style.top = "0px";
    reactionMenu.hidden = false;
    const { width, height } = reactionMenu.getBoundingClientRect();
    reactionMenu.style.left = `${Math.max(8, Math.min(x, innerWidth - width - 8))}px`;
    reactionMenu.style.top = `${Math.max(8, Math.min(y - height - 8, innerHeight - height - 8))}px`;
    if (focus) reactionMenu.firstElementChild.focus({ preventScroll: true });
  }

  function cancelPress() {
    if (!press) return;
    clearTimeout(press.timer);
    press = null;
  }

  async function chooseReaction(button, kind) {
    const id = Number(button.closest(".msg").dataset.id);
    if (pendingReactions.has(id)) return;
    const selected = button.dataset.mine === kind ? null : kind;
    const since = reactionVersion;
    pendingReactions.add(id);
    showError("");
    try {
      const state = await fetchJSON(`/chat/messages/${id}/reaction`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: selected }),
      });
      if (!newerReaction(id, since)) recordReaction(id, state);
    } catch (err) {
      showError(err.message);
    } finally {
      pendingReactions.delete(id);
    }
  }

  function sendPayload(payload, text) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      showError("You’re offline. Try sending your message again in a moment.");
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
      showError("Choose a photo of up to 15 MB.");
      return;
    }
    const body = new FormData();
    body.append("image", file);
    showError("");
    statusBox.textContent = "Sending photo…";
    statusBox.hidden = false;
    try {
      const result = await fetchJSON(`/chat/${ROOM}/image`, { method: "POST", body });
      append(result.message);
      scrollToBottom();
    } catch (err) {
      showError(err.message);
    } finally {
      statusBox.textContent = "Disconnected…";
      statusBox.hidden = !!socket && socket.readyState === WebSocket.OPEN;
    }
  });

  list.addEventListener("click", async (event) => {
    if (event.detail > 0 && heldMessage?.node.contains(event.target) && performance.now() < heldMessage.until) {
      event.preventDefault();
      heldMessage = null;
      return;
    }
    const reaction = event.target.closest(".msg-reaction");
    if (reaction) {
      const { left, top } = reaction.getBoundingClientRect();
      openReactionMenu(reaction, left, top, event.detail === 0);
      return;
    }
    const button = event.target.closest("[data-delete]");
    if (!button || !window.confirm("Delete this message for everyone?")) return;
    button.disabled = true;
    try {
      await fetchJSON(`/chat/messages/${button.dataset.delete}/delete`, { method: "POST" });
      markDeleted(Number(button.dataset.delete));
    } catch (err) {
      button.disabled = false;
      showError(err.message);
    }
  });

  list.addEventListener("contextmenu", (event) => {
    const bubble = event.target.closest(".msg-body");
    const reaction = bubble?.querySelector(".msg-reaction");
    if (!reaction) return;
    event.preventDefault();
    if (press) {
      press.open();
      return;
    }
    if (event.pointerType === "touch" || heldMessage?.node === bubble) return;
    openReactionMenu(reaction, event.clientX, event.clientY);
  });

  list.addEventListener("pointerdown", (event) => {
    heldMessage = null;
    if (event.pointerType !== "touch" || !event.isPrimary || event.target.closest("button")) return;
    const bubble = event.target.closest(".msg-body");
    const reaction = bubble?.querySelector(".msg-reaction");
    if (!reaction) return;
    const { pointerId, clientX: x, clientY: y } = event;
    const open = () => {
      cancelPress();
      if (!reaction.isConnected) return;
      heldMessage = { node: bubble, until: Infinity };
      openReactionMenu(reaction, x, y);
    };
    press = { pointerId, x, y, open, timer: setTimeout(open, 500) };
  });
  list.addEventListener("pointermove", (event) => {
    if (press?.pointerId === event.pointerId && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 12) {
      cancelPress();
    }
  });
  for (const type of ["pointerup", "pointercancel"]) {
    list.addEventListener(type, (event) => {
      if (press?.pointerId === event.pointerId) cancelPress();
      if (heldMessage) heldMessage.until = performance.now() + 500;
    });
  }

  reactionMenu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-kind]");
    if (!option || !reactionTarget) return;
    const button = reactionTarget;
    closeReactionMenu(event.detail === 0);
    chooseReaction(button, option.dataset.kind);
  });
  document.addEventListener("pointerdown", (event) => {
    if (reactionTarget && !reactionMenu.contains(event.target) && !reactionTarget.contains(event.target)) {
      closeReactionMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (reactionTarget && event.key === "Escape") closeReactionMenu(true);
  });
  window.addEventListener("resize", () => closeReactionMenu());

  list.addEventListener("scroll", async () => {
    cancelPress();
    closeReactionMenu();
    if (list.scrollTop > 200 || !hasMore || loadingOlder || firstId === null) return;
    loadingOlder = true;
    try {
      const since = reactionVersion;
      const body = await fetchJSON(`/chat/${ROOM}/messages?before=${firstId}`);
      hasMore = body.has_more;
      prepend(body.messages, since);
    } catch {
      // прокрутка повторит попытку
    } finally {
      loadingOlder = false;
    }
  });

  document.addEventListener("visibilitychange", sendRead);

  // ---------- старт ----------
  data.messages.forEach((m) => append(m));
  showEmpty();
  scrollToBottom();
  connect();
})();
