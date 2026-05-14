/* utils.js — shared constants, formatters, DOM helpers.
 *
 * Loaded as a classic <script>; everything declared here lives on the
 * window scope so the other modules (filters, charts, render, app) can
 * use them without a bundler.
 */

// Chart palette — saturated brand-aligned colours that pop on light glass.
const PIE_COLORS = [
  "#003087", "#f47920", "#7ab648", "#0ea5e9",
  "#ec4899", "#8338ec", "#eab308", "#14b8a6",
  "#ef4444", "#1d4ed8", "#f97316", "#10b981",
];

const MONTH_LABELS = [
  "", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// Chart instances are kept off `window` to avoid colliding with elements
// whose `id` attribute the browser exposes as global properties.
const charts = {};

const STATES = ["hintState", "loadingState", "noDataState", "dashboard"];
function showOnly(id) {
  STATES.forEach(s => document.getElementById(s)?.classList.toggle("hidden", s !== id));
}

// ── Formatters ─────────────────────────────────────────────────────────────
function fmtInt(n) {
  n = parseInt(n) || 0;
  if (n >= 1e7) return (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return (n / 1e5).toFixed(2) + " L";
  return n.toLocaleString("en-IN");
}

function fmtAmt(n) {
  n = parseFloat(n) || 0;
  if (n >= 1e7) return (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return (n / 1e5).toFixed(2) + " L";
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtMoney(n) {
  n = parseFloat(n) || 0;
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMonthYear(year, month) {
  if (!year || !month) return "Unknown";
  return `${MONTH_LABELS[month] || "Month"} ${year}`;
}

function short(name) {
  return String(name || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
}

// ── DOM helpers ────────────────────────────────────────────────────────────
function setText(id, txt) {
  const el = document.getElementById(id);
  if (el) el.textContent = txt;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function escapeAttr(s) { return escapeHtml(s); }

function uniqueSorted(arr) {
  return Array.from(new Set(arr.filter(Boolean))).sort();
}

// ── Chart cleanup ──────────────────────────────────────────────────────────
function destroy(key) {
  const c = charts[key];
  if (c && typeof c.destroy === "function") c.destroy();
  charts[key] = null;
}
