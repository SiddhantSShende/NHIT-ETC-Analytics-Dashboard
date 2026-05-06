/**
 * NHIT Analytics Chatbot — chatbot.js
 *
 * Sends user questions to /api/chat (server.py), which proxies to
 * OpenRouter free-tier models with the full snapshot context baked in.
 *
 * API key is stored in sessionStorage so it survives page refreshes
 * but is cleared when the tab closes.
 */

const CHAT_API = "/api/chat";
/* ── State ───────────────────────────────────────────────────────────────── */
let chatOpen = false;
let chatHistory = [];   // [{role:"user"|"assistant", content:string}]
let isTyping = false;

/* ── DOM refs ────────────────────────────────────────────────────────────── */
const askBtn    = () => document.getElementById("btnAskAI");
const panel     = () => document.getElementById("chatPanel");
const overlay   = () => document.getElementById("chatOverlay");
const messages  = () => document.getElementById("chatMessages");
const input     = () => document.getElementById("chatInput");
const sendBtn   = () => document.getElementById("chatSend");
const clearBtn  = () => document.getElementById("chatClearBtn");
const closeBtn  = () => document.getElementById("chatCloseBtn");
const status    = () => document.getElementById("chatStatus");
const chips     = () => document.querySelectorAll(".chat-chip");

/* ── Bootstrap ──────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  // Events
  askBtn().addEventListener("click", toggleChat);
  overlay().addEventListener("click", closeChat);
  closeBtn().addEventListener("click", closeChat);
  clearBtn().addEventListener("click", clearConversation);
  sendBtn().addEventListener("click", sendMessage);

  input().addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  input().addEventListener("input", () => {
    autoGrow(input());
    sendBtn().disabled = !input().value.trim() || isTyping;
  });

  chips().forEach(chip => {
    chip.addEventListener("click", () => {
      input().value = chip.dataset.q;
      autoGrow(input());
      sendBtn().disabled = false;
      sendMessage();
    });
  });
});

/* ── Panel open/close ───────────────────────────────────────────────────── */
function toggleChat() {
  chatOpen ? closeChat() : openChat();
}

function openChat() {
  chatOpen = true;
  panel().classList.add("chat-panel--open");
  overlay().classList.add("chat-overlay--visible");
  askBtn().classList.add("btn-ask-ai--active");
  setTimeout(() => input().focus(), 350);
}

function closeChat() {
  chatOpen = false;
  panel().classList.remove("chat-panel--open");
  overlay().classList.remove("chat-overlay--visible");
  askBtn().classList.remove("btn-ask-ai--active");
}



/* ── Conversation clear ─────────────────────────────────────────────────── */
function clearConversation() {
  chatHistory = [];
  const msgs = messages();
  // Keep only the welcome bubble (first child)
  while (msgs.children.length > 1) msgs.removeChild(msgs.lastChild);
  document.getElementById("chatSuggestions").style.display = "";
}

/* ── Send / receive ─────────────────────────────────────────────────────── */
async function sendMessage() {
  const text = input().value.trim();
  if (!text || isTyping) return;

  // Append user bubble
  appendBubble("user", text);
  chatHistory.push({ role: "user", content: text });

  // Reset input
  input().value = "";
  input().style.height = "";
  sendBtn().disabled = true;

  // Hide suggestions after first message
  document.getElementById("chatSuggestions").style.display = "none";

  // Show typing indicator
  const typingEl = showTyping();
  isTyping = true;

  try {
    const resp = await fetch(CHAT_API, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message: text, history: chatHistory.slice(-10) }),
    });

    const data = await resp.json();
    typingEl.remove();
    isTyping = false;

    if (!resp.ok || data.error) {
      const errMsg = data.error || `Server error ${resp.status}`;
      appendBubble("bot", `⚠️ ${errMsg}`, true);
    } else {
      appendBubble("bot", data.reply);
      chatHistory.push({ role: "assistant", content: data.reply });
    }
  } catch (err) {
    typingEl.remove();
    isTyping = false;
    appendBubble("bot", "⚠️ Network error — is the server running?", true);
  }

  sendBtn().disabled = !input().value.trim();
}

/* ── Bubble renderer ────────────────────────────────────────────────────── */
function appendBubble(role, text, isError = false) {
  const isBot = role === "bot" || role === "assistant";
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${isBot ? "chat-msg-bot" : "chat-msg-user"}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble" + (isError ? " chat-bubble--error" : "");

  // Convert simple markdown-like formatting
  bubble.innerHTML = formatText(text);

  const time = document.createElement("div");
  time.className = "chat-time";
  time.textContent = now();

  wrap.appendChild(bubble);
  wrap.appendChild(time);

  messages().appendChild(wrap);
  scrollBottom();

  // Animate in
  requestAnimationFrame(() => wrap.classList.add("chat-msg--in"));
}

function formatText(txt) {
  return txt
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    // Bold **text**
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Bullet points
    .replace(/^[•\-\*] (.+)$/gm, "<span class='chat-li'>• $1</span>")
    // Newlines
    .replace(/\n/g, "<br>");
}

/* ── Typing indicator ───────────────────────────────────────────────────── */
function showTyping() {
  const wrap = document.createElement("div");
  wrap.className = "chat-msg chat-msg-bot chat-msg--in";
  wrap.id = "chatTyping";
  wrap.innerHTML = `
    <div class="chat-bubble chat-bubble--typing">
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
    </div>`;
  messages().appendChild(wrap);
  scrollBottom();
  return wrap;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */
function scrollBottom() {
  const el = messages();
  el.scrollTop = el.scrollHeight;
}

function autoGrow(el) {
  el.style.height = "44px";
  const newHeight = Math.min(el.scrollHeight, 120);
  el.style.height = newHeight + "px";
  el.style.overflowY = el.scrollHeight > 120 ? "auto" : "hidden";
}

function now() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function flashError(el) {
  el.style.borderColor = "#e63946";
  setTimeout(() => (el.style.borderColor = ""), 1500);
}

function showToast(msg) {
  const t = document.createElement("div");
  t.className = "chat-toast";
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("chat-toast--in"));
  setTimeout(() => {
    t.classList.remove("chat-toast--in");
    setTimeout(() => t.remove(), 400);
  }, 3000);
}
