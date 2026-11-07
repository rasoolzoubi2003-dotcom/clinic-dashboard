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

  // Model replies are shown as plain text (never as HTML). Strip markdown bold that small models like to add.
  const clean = (text) => (text || "").replace(/\*\*(.+?)\*\*/g, "$1").trim();

  function addMessage(role, text, { error = false } = {}) {
    const item = document.createElement("li");
    item.className = role === "user" ? "flex justify-end" : "flex";
    const bubble = document.createElement("div");
    const base = "max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm";
    if (role === "user") bubble.className = `${base} rounded-br-sm bg-blue-600 text-white`;
    else if (error) bubble.className = `${base} rounded-bl-sm border border-red-200 bg-red-50 text-red-700`;
    else bubble.className = `${base} rounded-bl-sm bg-slate-100 text-slate-800`;
    bubble.textContent = clean(text);
    item.appendChild(bubble);
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return item;
  }

  function showTyping() {
    const item = addMessage("assistant", "Thinking…");
    const bubble = item.firstChild;
    bubble.classList.add("text-slate-500", "italic");
    const started = Date.now();
    // Local models on a CPU can take a while; show elapsed time so it doesn't look frozen.
    const timer = setInterval(() => {
      const seconds = Math.round((Date.now() - started) / 1000);
      if (seconds >= 5) bubble.textContent = `Thinking… ${seconds}s (local models can be slow on a CPU)`;
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
      ["Doctor", `${pending.doctor} (${pending.specialty})`],
      ["Date", pending.date_label || pending.date],
      ["Time", `${pending.start} – ${pending.end}`],
      ["Patient", pending.patient_name],
      ["Phone", pending.phone_number],
    ];
    const details = el("pending-details");
    details.replaceChildren();
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.className = "font-medium";
      dt.textContent = label;
      const dd = document.createElement("dd");
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
      if (statusDot) statusDot.className = `h-2 w-2 rounded-full ${status.ready ? "bg-emerald-500" : "bg-amber-500"}`;
      if (!status.ready && status.message) {
        banner.textContent = status.message;
        banner.classList.remove("hidden");
      }
    } catch {
      /* the chat itself reports errors */
    }
  }

  checkStatus();
})();
