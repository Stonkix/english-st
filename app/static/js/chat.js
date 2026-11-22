// Переписка: сообщения по WebSocket (/ws/chat), догрузка пропущенного после обрыва, история при прокрутке вверх,
// стикеры, фото, реакции, удаление. Данные комнаты — в #chat-data (см. routes/chat.py::room_page).
(() => {
  const data = JSON.parse(document.getElementById("chat-data").textContent);
  const list = document.getElementById("chat-messages");
  const roomLayout = document.getElementById("chat-room-layout");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const errorBox = document.getElementById("chat-error");
  const noticeBox = document.getElementById("chat-notice");
  const outbox = document.getElementById("chat-outbox");
  const replyBox = document.getElementById("chat-reply-draft");
  const replySummary = document.getElementById("chat-reply-summary");
  const statusBox = document.getElementById("chat-status");
  const stickerBtn = document.getElementById("chat-sticker-btn");
  const stickerPanel = document.getElementById("chat-stickers");
  const imageInput = document.getElementById("chat-image");

  const ROOM = data.room_id;
  const byId = new Map(); // id -> элемент сообщения
  const emptyReaction = { counts: {}, mine: null };
  const reactionEvents = new Map();
  const deletedMessages = new Set();
  const pendingReactions = new Set();
  const pendingDeletes = new Set();
  let reactionVersion = 0;
  let menuTarget = null;
  let press = null;
  let heldMessage = null;
  let lastPointerType = "";
  const messageMenu = document.createElement("div");
  messageMenu.id = "chat-message-menu";
  messageMenu.className = "chat-message-menu";
  messageMenu.hidden = true;
  messageMenu.setAttribute("role", "group");
  messageMenu.setAttribute("aria-label", "Message actions");
  document.body.append(messageMenu);
  let firstId = null; // самое старое показанное
  let lastId = 0; // самое новое полученное: от него догружаем после обрыва
  let hasMore = data.has_more;
  let olderRequest = null;
  let replyDraft = null;
  let jumpingTo = null;
  let socket = null;
  let retry = 0;
  const pending = new Map();
  let noticeTimer;

  // ---------- вывод ----------

  const DAY_FMT = new Intl.DateTimeFormat("en", { day: "numeric", month: "long" });
  const GROUP_GAP_MINUTES = 5;

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

  function paintReaction(bubble, { counts, mine }) {
    bubble.dataset.mine = mine ?? "";
    const reactions = bubble.querySelector(".msg-reactions");
    const visible = data.reactions.filter(({ id }) => counts[id]);
    reactions.hidden = !visible.length;
    reactions.replaceChildren(...visible.map(({ id, emoji }) => {
      const badge = el("span", "msg-reaction-badge", `${emoji} ${counts[id]}`);
      badge.classList.toggle("is-mine", id === mine);
      return badge;
    }));
    const summary = visible.map(({ id, label }) => `${label} ${counts[id]}`).join(", ");
    reactions.setAttribute("aria-label", `Reactions: ${summary}`);
  }

  function setReaction(id, state) {
    const bubble = byId.get(id)?.querySelector(".msg-body[aria-controls]");
    if (bubble) paintReaction(bubble, state);
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
    if (deletedMessages.has(m.id)) m = { ...m, deleted: true };
    const mine = m.author.id === data.me;
    const showDeleted = m.deleted && data.moderator && !m.author.teacher;
    const node = el("div", "msg" + (mine ? " is-mine" : ""));
    node.classList.toggle("is-deleted", m.deleted);
    node.classList.toggle("shows-deleted-content", showDeleted);
    node.dataset.id = m.id;
    node.dataset.day = m.day;
    node.dataset.author = m.author.id;
    node.dataset.authorName = m.author.name;
    const [hours, minutes] = m.time.split(":").map(Number);
    node.dataset.minute = hours * 60 + minutes;
    node.dataset.studentAuthor = String(!m.author.teacher);
    node.dataset.kind = m.kind;
    node.dataset.canDelete = String(mine || data.moderator);
    if (!mine && data.kind === "group") node.append(avatar(m.author));
    const media = !m.deleted || showDeleted ? { sticker: " is-sticker", image: " is-media" }[m.kind] || "" : "";
    const body = el("div", "msg-body" + media);
    if (!mine && data.kind === "group") {
      const name = el("div", "msg-name", m.author.name);
      if (m.author.teacher) name.append(" ", el("span", "msg-teacher", "teacher"));
      body.append(name);
    }
    if (showDeleted) addDeletedLabel(body);
    if (!m.deleted && m.reply) {
      const quote = el("button", "msg-quote");
      quote.type = "button";
      quote.dataset.quoteId = m.reply.id;
      quote.disabled = m.reply.deleted || deletedMessages.has(m.reply.id);
      if (!quote.disabled && m.reply.author) quote.append(el("strong", "", m.reply.author));
      quote.append(el("span", "", quote.disabled ? "Message deleted" : m.reply.text));
      body.append(quote);
    }
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
      body.tabIndex = 0;
      body.setAttribute("role", "group");
      body.setAttribute("aria-label", "Message, press Enter for actions");
      body.setAttribute("aria-controls", messageMenu.id);
      body.setAttribute("aria-expanded", "false");
      const reactions = el("div", "msg-reactions");
      reactions.setAttribute("role", "group");
      meta.append(reactions);
    }
    const details = el("div", "msg-meta-details");
    const time = el("time", "", m.time);
    time.dateTime = m.time;
    details.append(time);
    meta.append(details);
    body.append(meta);
    if (!m.deleted) paintReaction(body, newerReaction(m.id, since) ?? m.reaction ?? emptyReaction);
    node.append(body);
    return node;
  }

  function updateGrouping(node) {
    if (!node?.classList.contains("msg")) return;
    const previous = node.previousElementSibling;
    const gap = Number(node.dataset.minute) - Number(previous?.dataset.minute);
    const continuation = previous?.dataset.author === node.dataset.author &&
      previous.dataset.day === node.dataset.day && gap >= 0 && gap <= GROUP_GAP_MINUTES &&
      !previous.classList.contains("is-deleted") && !node.classList.contains("is-deleted");
    node.classList.toggle("is-cont", continuation);
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
      if (menuTarget && old.contains(menuTarget)) closeMessageMenu();
      old.replaceWith(fresh);
      byId.set(m.id, fresh);
      updateGrouping(fresh);
      updateGrouping(fresh.nextElementSibling);
      return;
    }
    if (m.id < lastId) return; // старее последнего показанного — придёт с историей
    const lastMsg = byId.get(lastId);
    if (!lastMsg || lastMsg.dataset.day !== m.day) list.append(separator(m.day));
    const node = render(m, since);
    list.append(node);
    byId.set(m.id, node);
    lastId = Math.max(lastId, m.id);
    if (firstId === null) firstId = m.id;
    hideEmpty();
    updateGrouping(node);
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
    for (const node of list.children) updateGrouping(node);
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
    deletedMessages.add(id);
    for (const quote of list.querySelectorAll(`[data-quote-id="${id}"]`)) {
      quote.disabled = true;
      quote.replaceChildren(el("span", "", "Message deleted"));
    }
    if (replyDraft === id) setReply(null);
    const node = byId.get(id);
    if (!node || node.classList.contains("is-deleted")) return;
    node.classList.add("is-deleted");
    updateGrouping(node);
    updateGrouping(node.nextElementSibling);
    const body = node.querySelector(".msg-body");
    if (menuTarget && node.contains(menuTarget)) closeMessageMenu();
    if (body === document.activeElement) input.focus({ preventScroll: true });
    for (const attr of ["tabindex", "role", "aria-label", "aria-controls", "aria-expanded"]) {
      body.removeAttribute(attr);
    }
    body.querySelector(".msg-reactions")?.remove();
    if (data.moderator && node.dataset.studentAuthor === "true") {
      node.classList.add("shows-deleted-content");
      body.querySelectorAll(".msg-take, .msg-quote").forEach((n) => n.remove());
      addDeletedLabel(body, body.querySelector(".msg-text, .msg-sticker, .msg-image-link, .msg-meta"));
      return;
    }
    body.classList.remove("is-media", "is-sticker");
    body.querySelectorAll(".msg-text, .msg-sticker, .msg-image-link, .msg-original, .msg-take, .msg-quote").forEach((n) => n.remove());
    body.insertBefore(el("p", "msg-text", "Message deleted"), body.querySelector(".msg-meta"));
  }

  function showError(text) {
    clearTimeout(noticeTimer);
    noticeBox.hidden = true;
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }

  function showNotice(text) {
    clearTimeout(noticeTimer);
    noticeBox.textContent = text;
    noticeBox.hidden = false;
    noticeTimer = setTimeout(() => { noticeBox.hidden = true; }, 2500);
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

  function updateRoomPreview(message) {
    const room = roomLayout?.querySelector(`[data-chat-room="${message.room_id}"]`);
    if (!room || message.id <= Number(room.dataset.lastMessage)) return;
    room.dataset.lastMessage = message.id;
    const mine = message.author.id === data.me;
    const preview = { image: "Photo", sticker: "Sticker" }[message.kind] ?? message.text ?? "";
    room.querySelector(".chat-preview").textContent = (mine ? "You: " : "") + preview;
    room.querySelector("[data-room-time]").textContent = message.time;
    if (mine || message.room_id === ROOM) return;
    const count = Number(room.dataset.roomUnread) + 1;
    room.dataset.roomUnread = count;
    let badge = room.querySelector(".chat-unread");
    if (!badge) {
      badge = el("span", "chat-unread");
      room.querySelector(".chat-list-meta").append(badge);
    }
    badge.textContent = count < 100 ? count : "99+";
    badge.setAttribute("aria-label", `Unread: ${count}`);
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
      if (msg.type === "message") {
        updateRoomPreview(msg.message);
        if (msg.message.room_id !== ROOM) return;
        if (msg.message.author.id === data.me) acknowledge(msg.client_id);
        const stick = nearBottom() || msg.message.author.id === data.me;
        append(msg.message);
        if (stick) scrollToBottom();
        sendRead();
      } else if (msg.type === "ack" && msg.room_id === ROOM) {
        acknowledge(msg.client_id);
        catchUp().catch(() => {});
      } else if (msg.type === "deleted" && msg.room_id === ROOM) {
        markDeleted(msg.id);
      } else if (msg.type === "reaction" && msg.room_id === ROOM) {
        recordReaction(msg.id, msg.state);
      } else if (msg.type === "error") {
        const draft = pending.get(msg.client_id);
        if (draft) failSend(draft, msg.error);
        else showError(msg.error);
      }
    });
    socket.addEventListener("close", () => {
      statusBox.hidden = false;
      for (const draft of pending.values()) {
        if (!draft.error) failSend(draft, "Connection lost. Retry when connected.");
      }
      // пауза растёт: 1, 2, 4… до 30 секунд, с разбросом, чтобы весь класс не переподключался разом
      const delay = Math.min(30000, 1000 * 2 ** retry) * (0.75 + Math.random() / 2);
      retry += 1;
      setTimeout(connect, delay);
    });
  }

  // ---------- действия ----------

  function closeMessageMenu(restoreFocus = false) {
    if (!menuTarget) return;
    menuTarget.setAttribute("aria-expanded", "false");
    if (restoreFocus) menuTarget.focus({ preventScroll: true });
    menuTarget = null;
    messageMenu.hidden = true;
  }

  function openMessageMenu(bubble, x, y, focus = false) {
    closeMessageMenu();
    menuTarget = bubble;
    bubble.setAttribute("aria-expanded", "true");
    const row = bubble.closest(".msg");
    const reactions = el("div", "chat-message-reactions");
    reactions.setAttribute("role", "group");
    reactions.setAttribute("aria-label", "Reactions");
    reactions.append(...data.reactions.map(({ id, emoji, label }) => {
      const option = el("button", "chat-reaction-option", emoji);
      option.type = "button";
      option.title = label;
      option.setAttribute("aria-label", label);
      option.setAttribute("aria-pressed", String(bubble.dataset.mine === id));
      option.dataset.kind = id;
      return option;
    }));
    messageMenu.replaceChildren(reactions);
    const actions = el("div", "chat-message-actions");
    const reply = el("button", "chat-message-action", "Reply");
    reply.type = "button";
    reply.dataset.action = "reply";
    actions.append(reply);
    if (row.dataset.kind === "text" && row.querySelector(".msg-text")?.textContent) {
      const copy = el("button", "chat-message-action", "Copy text");
      copy.type = "button";
      copy.dataset.action = "copy";
      actions.append(copy);
    }
    if (row.dataset.canDelete === "true") {
      const remove = el("button", "chat-message-action is-danger", "Delete");
      remove.type = "button";
      remove.dataset.action = "delete";
      actions.append(remove);
    }
    if (actions.childElementCount) messageMenu.append(actions);
    messageMenu.style.left = "0px";
    messageMenu.style.top = "0px";
    messageMenu.hidden = false;
    const { width, height } = messageMenu.getBoundingClientRect();
    messageMenu.style.left = `${Math.max(8, Math.min(x, innerWidth - width - 8))}px`;
    const top = y - height - 8;
    messageMenu.style.top = `${Math.max(8, Math.min(top >= 8 ? top : y + 8, innerHeight - height - 8))}px`;
    if (focus) messageMenu.querySelector("button")?.focus({ preventScroll: true });
  }

  function cancelPress() {
    if (!press) return;
    clearTimeout(press.timer);
    press = null;
  }

  async function chooseReaction(bubble, kind) {
    const id = Number(bubble.closest(".msg").dataset.id);
    if (pendingReactions.has(id)) return;
    const selected = bubble.dataset.mine === kind ? null : kind;
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

  async function copyMessage(row) {
    showError("");
    try {
      await navigator.clipboard.writeText(row.querySelector(".msg-text").textContent);
      showNotice("Message copied.");
    } catch {
      showError("Couldn’t copy the message.");
    }
  }

  function setReply(row) {
    replyDraft = row ? Number(row.dataset.id) : null;
    replyBox.hidden = !row;
    replySummary.replaceChildren();
    if (!row) return;
    const preview = row.dataset.kind === "image" ? "Photo" : row.dataset.kind === "sticker" ? "Sticker" :
      row.querySelector(".msg-text").textContent.slice(0, 150);
    replySummary.append(el("strong", "", `Reply to ${row.dataset.authorName}`), el("span", "", preview));
    input.focus();
  }

  document.getElementById("chat-reply-cancel").addEventListener("click", () => {
    setReply(null);
    input.focus();
  });

  async function jumpToMessage(id) {
    if (jumpingTo === id) return;
    jumpingTo = id;
    showError("");
    try {
      while (jumpingTo === id && !byId.has(id) && hasMore && firstId > id) {
        await loadOlder();
      }
      if (jumpingTo !== id) return;
      const original = byId.get(id);
      if (!original || original.classList.contains("is-deleted")) {
        showError("The original message is no longer available.");
        return;
      }
      original.scrollIntoView({ block: "center" });
      original.querySelector(".msg-body").focus({ preventScroll: true });
      original.classList.add("is-highlighted");
      setTimeout(() => original.classList.remove("is-highlighted"), 1500);
    } catch {
      showError("Couldn’t load the original message. Try again.");
    } finally {
      if (jumpingTo === id) jumpingTo = null;
    }
  }

  async function deleteMessage(id) {
    if (pendingDeletes.has(id)) return;
    pendingDeletes.add(id);
    try {
      const confirmed = await confirmAction("This message will be deleted for everyone.", {
        title: "Delete message?",
        label: "Delete",
        danger: true,
      });
      if (!confirmed || byId.get(id)?.classList.contains("is-deleted")) return;
      showError("");
      await fetchJSON(`/chat/messages/${id}/delete`, { method: "POST" });
      markDeleted(id);
    } catch (err) {
      showError(err.message);
    } finally {
      pendingDeletes.delete(id);
    }
  }

  function drawOutbox() {
    outbox.replaceChildren(...[...pending.values()].map((draft) => {
      const row = el("div", "chat-outbox-row");
      const summary = el("div", "chat-outbox-summary");
      summary.append(el("strong", "", draft.payload.text ?? "Sticker"));
      const state = el("span", "", draft.error || "Sending…");
      state.setAttribute("role", "status");
      summary.append(state);
      row.append(summary);
      if (draft.error) {
        const retryButton = el("button", "btn btn-secondary", "Retry");
        retryButton.type = "button";
        retryButton.addEventListener("click", () => transmit(draft));
        const dismiss = el("button", "btn btn-secondary", "Dismiss");
        dismiss.type = "button";
        dismiss.addEventListener("click", () => {
          pending.delete(draft.id);
          drawOutbox();
        });
        row.append(retryButton, dismiss);
      }
      return row;
    }));
    outbox.hidden = !pending.size;
  }

  function failSend(draft, error) {
    clearTimeout(draft.timer);
    draft.error = error;
    drawOutbox();
  }

  function acknowledge(id) {
    const draft = pending.get(id);
    if (!draft) return;
    clearTimeout(draft.timer);
    pending.delete(id);
    drawOutbox();
    showNotice("Message sent.");
  }

  function transmit(draft) {
    showError("");
    if (socket?.readyState !== WebSocket.OPEN) {
      failSend(draft, "You’re offline. Retry when connected.");
      return;
    }
    draft.error = "";
    draft.timer = setTimeout(() => failSend(draft, "No confirmation. You can retry."), 12000);
    try {
      socket.send(JSON.stringify({ type: "send", room_id: ROOM, client_id: draft.id, ...draft.payload }));
      drawOutbox();
    } catch {
      failSend(draft, "Connection lost. Retry when connected.");
    }
  }

  function sendPayload(payload) {
    const id = crypto.randomUUID?.() ??
      Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16).padStart(8, "0")).join("");
    const draft = { id, payload: { ...payload, reply_to: replyDraft } };
    pending.set(id, draft);
    setReply(null);
    transmit(draft);
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
    sendPayload({ text });
    input.value = "";
    autosize();
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
    sendPayload({ sticker: button.dataset.sticker });
    stickerPanel.hidden = true;
    stickerBtn.setAttribute("aria-expanded", "false");
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
    const replyingTo = replyDraft;
    if (replyingTo !== null) body.append("reply_to", replyingTo);
    showError("");
    statusBox.textContent = "Sending photo…";
    statusBox.hidden = false;
    try {
      const result = await fetchJSON(`/chat/${ROOM}/image`, { method: "POST", body });
      if (replyDraft === replyingTo) setReply(null);
      append(result.message);
      scrollToBottom();
    } catch (err) {
      showError(err.message);
    } finally {
      statusBox.textContent = "Disconnected…";
      statusBox.hidden = !!socket && socket.readyState === WebSocket.OPEN;
    }
  });

  list.addEventListener("click", (event) => {
    if (event.detail > 0 && heldMessage?.node.contains(event.target) && performance.now() < heldMessage.until) {
      event.preventDefault();
      heldMessage = null;
      return;
    }
    const quote = event.target.closest("[data-quote-id]");
    if (quote && !quote.disabled) jumpToMessage(Number(quote.dataset.quoteId));
  });

  (roomLayout ?? document.getElementById("chat")).addEventListener("contextmenu", (event) => {
    if (event.target.closest("input, textarea, [contenteditable]")) return;
    event.preventDefault();
    const bubble = event.target.closest(".msg-body[aria-controls]");
    if (!bubble) return;
    if (press?.node === bubble) {
      press.open();
      return;
    }
    if ((event.pointerType || lastPointerType) === "touch" || heldMessage?.node === bubble) return;
    const keyboard = event.clientX === 0 && event.clientY === 0;
    const { left, top } = bubble.getBoundingClientRect();
    openMessageMenu(bubble, keyboard ? left : event.clientX, keyboard ? top : event.clientY, keyboard);
  });
  list.addEventListener("selectstart", (event) => {
    if (lastPointerType === "touch") event.preventDefault();
  });

  list.addEventListener("pointerdown", (event) => {
    lastPointerType = event.pointerType;
    heldMessage = null;
    if (event.pointerType !== "touch" || !event.isPrimary || event.target.closest("button")) return;
    const bubble = event.target.closest(".msg-body[aria-controls]");
    if (!bubble) return;
    const { pointerId, clientX: x, clientY: y } = event;
    const open = () => {
      cancelPress();
      if (!bubble.isConnected || !bubble.hasAttribute("aria-controls")) return;
      heldMessage = { node: bubble, until: Infinity };
      openMessageMenu(bubble, x, y);
    };
    press = { node: bubble, pointerId, x, y, open, timer: setTimeout(open, 500) };
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

  messageMenu.addEventListener("click", (event) => {
    const option = event.target.closest("button");
    if (!option || !menuTarget) return;
    const bubble = menuTarget;
    const row = bubble.closest(".msg");
    closeMessageMenu(true);
    if (option.dataset.kind) chooseReaction(bubble, option.dataset.kind);
    else if (option.dataset.action === "reply") setReply(row);
    else if (option.dataset.action === "copy") copyMessage(row);
    else if (option.dataset.action === "delete") deleteMessage(Number(row.dataset.id));
  });
  document.addEventListener("pointerdown", (event) => {
    if (menuTarget && !messageMenu.contains(event.target) && !menuTarget.contains(event.target)) {
      closeMessageMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (menuTarget && event.key === "Escape") closeMessageMenu(true);
  });
  list.addEventListener("keydown", (event) => {
    const bubble = event.target;
    if (!bubble.matches(".msg-body[aria-controls]") || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    const { left, top } = bubble.getBoundingClientRect();
    openMessageMenu(bubble, left, top, true);
  });
  window.addEventListener("resize", () => closeMessageMenu());

  function loadOlder() {
    if (olderRequest) return olderRequest;
    const since = reactionVersion;
    olderRequest = fetchJSON(`/chat/${ROOM}/messages?before=${firstId}`).then((body) => {
      hasMore = body.has_more && body.messages.length > 0;
      prepend(body.messages, since);
    }).finally(() => {
      olderRequest = null;
    });
    return olderRequest;
  }

  list.addEventListener("scroll", () => {
    cancelPress();
    closeMessageMenu();
    if (list.scrollTop > 200 || !hasMore || olderRequest || firstId === null) return;
    loadOlder().catch(() => {});
  });

  document.addEventListener("visibilitychange", sendRead);

  // ---------- старт ----------
  data.messages.forEach((m) => append(m));
  showEmpty();
  scrollToBottom();
  connect();
})();
