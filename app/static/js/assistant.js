// Chat UI for the AI booking assistant. Plain JavaScript, no build step.
(() => {
  const el = (id) => document.getElementById(id);
  const messages = el("messages");
  const form = el("chat-form");
  const input = el("chat-input");
  const sendButton = el("send");
  const pendingBox = el("pending");
  const banner = el("status-banner");
  const statusDot = el("status-dot");
  let busy = false;

  // Message text is always inserted with textContent, never as HTML.
  // Strip markdown bold that small models like to add; replies are shown as plain text.
  const clean = (text) => (text || "").replace(/\*\*(.+?)\*\*/g, "$1").trim();

  function avatar(role, error) {
    const span = document.createElement("span");
    const tone =
      role === "user"
        ? "bg-slate-800 text-white"
        : error
          ? "bg-rose-100 text-rose-600"
          : "bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-md shadow-indigo-500/30";
    span.className = `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${tone}`;
    span.append(el(role === "user" ? "icon-user" : "icon-assistant").content.cloneNode(true));
    return span;
  }

  function addRow(role, { error = false } = {}) {
    const item = document.createElement("li");
    item.className = role === "user" ? "flex flex-row-reverse items-end gap-2.5" : "flex items-end gap-2.5";
    const bubble = document.createElement("div");
    const base = "animate-pop max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm";
    if (role === "user")
      bubble.className = `${base} rounded-br-md bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-md shadow-indigo-500/25`;
    else if (error) bubble.className = `${base} rounded-bl-md bg-rose-50 text-rose-800 ring-1 ring-rose-200`;
    else bubble.className = `${base} rounded-bl-md bg-white text-slate-800 shadow-md shadow-slate-900/5 ring-1 ring-slate-900/5`;
    item.append(avatar(role, error), bubble);
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return { item, bubble };
  }

  function addMessage(role, text, options) {
    addRow(role, options).bubble.textContent = clean(text);
  }

  function showTyping() {
    const { item, bubble } = addRow("assistant");
    bubble.classList.add("flex", "items-center", "gap-3");
    const dots = document.createElement("span");
    dots.className = "flex items-center gap-1";
    for (let i = 0; i < 3; i++) {
      const dot = document.createElement("span");
      dot.className = "typing-dot h-2 w-2 rounded-full bg-indigo-400";
      dots.appendChild(dot);
    }
    const note = document.createElement("span");
    note.className = "text-xs text-slate-400";
    bubble.append(dots, note);
    const started = Date.now();
    // Local models on a CPU can take a while; show elapsed time so it doesn't look frozen.
    const timer = setInterval(() => {
      const seconds = Math.round((Date.now() - started) / 1000);
      if (seconds >= 5) note.textContent = `${seconds}s · local models can be slow on a CPU`;
    }, 1000);
    return () => {
      clearInterval(timer);
      item.remove();
    };
  }

  function renderPending(pending) {
    if (!pending) {
      pendingBox.classList.add("hidden");
      return;
    }
    el("pending-title").textContent =
      pending.action === "cancel" ? "Confirm cancellation" : "Confirm booking";
    const rows = [
      ["Doctor", `${pending.doctor} · ${pending.specialty}`],
      ["Date", pending.date_label || pending.date],
      ["Time", `${pending.start} – ${pending.end}`],
      ["Patient", pending.patient_name],
      ["Phone", pending.phone_number],
    ];
    const details = el("pending-details");
    details.replaceChildren();
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.className = "text-slate-500";
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.className = "font-medium text-slate-900";
      dd.textContent = value;
      details.append(dt, dd);
    }
    pendingBox.classList.remove("hidden");
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
    for (const button of pendingBox.querySelectorAll("button")) button.disabled = value;
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Something went wrong. Please try again.");
    return data;
  }

  async function run(request) {
    setBusy(true);
    const stopTyping = showTyping();
    try {
      const data = await request();
      stopTyping();
      addMessage("assistant", data.reply, { error: data.error });
      renderPending(data.pending);
    } catch (err) {
      stopTyping();
      addMessage("assistant", err.message || "Network error. Please try again.", { error: true });
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  function send(text) {
    text = text.trim();
    if (!text || busy) return;
    el("suggestions").classList.add("hidden");
    addMessage("user", text);
    input.value = "";
    run(() => post("/api/assistant/chat", { message: text }));
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });

  for (const button of document.querySelectorAll("[data-suggestion]")) {
    button.addEventListener("click", () => send(button.dataset.suggestion));
  }

  el("confirm-yes").addEventListener("click", () => {
    if (busy) return;
    addMessage("user", "Yes, confirm.");
    run(() => post("/api/assistant/confirm", { accept: true }));
  });
  el("confirm-no").addEventListener("click", () => {
    if (busy) return;
    addMessage("user", "No, don't do it.");
    run(() => post("/api/assistant/confirm", { accept: false }));
  });

  el("reset").addEventListener("click", async () => {
    if (busy) return;
    await post("/api/assistant/reset").catch(() => {});
    messages.replaceChildren(messages.firstElementChild);
    renderPending(null);
    el("suggestions").classList.remove("hidden");
    input.focus();
  });

  async function checkStatus() {
    try {
      const response = await fetch("/api/assistant/status");
      const status = await response.json();
      statusDot.classList.remove("bg-slate-300", "bg-emerald-400", "bg-amber-400");
      statusDot.classList.add(status.ready ? "bg-emerald-400" : "bg-amber-400");
      el("status-text").textContent = status.ready
        ? `Online · ${status.provider}`
        : status.provider
          ? `Offline · ${status.provider}`
          : "Not configured";
      if (!status.ready && status.message) {
        el("status-message").textContent = status.message;
        banner.classList.remove("hidden");
        banner.classList.add("flex");
      }
    } catch {
      el("status-text").textContent = "Status unknown";
    }
  }

  checkStatus();
})();
