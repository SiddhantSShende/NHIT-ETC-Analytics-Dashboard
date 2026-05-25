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

// Canonical display order for vehicle categories. Used across tables,
// pies, and bar charts so the sequence is identical everywhere.
const CATEGORY_ORDER = [
  "Car / Jeep / Van (VC4)",
  "Light Commercial Vehicle (VC5)",
  "Bus / Truck - 2 Axle (VC6)",
  "3-Axle Vehicle (VC7)",
  "4-6 Axle (VC8/9/10)",
  "Oversized Vehicle (VC11+)",
];

// Short labels for compact UI surfaces (dashboard tables, pies, bars).
const CATEGORY_SHORT = {
  "Car / Jeep / Van (VC4)":         "CJV",
  "Light Commercial Vehicle (VC5)": "LCV",
  "Bus / Truck - 2 Axle (VC6)":     "BUS/2A-Truck",
  "3-Axle Vehicle (VC7)":           "3A-Truck",
  "4-6 Axle (VC8/9/10)":            "MAV",
  "Oversized Vehicle (VC11+)":      "OSV",
};

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

// Always-Lakhs formatter for headline transaction counts (e.g. "0.05 L", "12.34 L").
function fmtTxnL(n) {
  n = parseFloat(n) || 0;
  return (n / 1e5).toFixed(2) + " L";
}

// Always-Crores formatter for headline revenue (e.g. "0.12 Cr", "3.45 Cr").
function fmtRevCr(n) {
  n = parseFloat(n) || 0;
  return (n / 1e7).toFixed(2) + " Cr";
}

// Signed percentage with one decimal: "+8.2%", "-3.5%", "0.0%".
function fmtPct(n) {
  n = parseFloat(n);
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

// % delta between curr and prev. Returns null when no comparison is possible.
function pctDelta(curr, prev) {
  curr = parseFloat(curr);
  prev = parseFloat(prev);
  if (!Number.isFinite(curr) || !Number.isFinite(prev) || prev === 0) return null;
  return (curr - prev) / prev * 100;
}

// Days in a given month (1-indexed). Handles leap years correctly.
function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function formatMonthYear(year, month) {
  if (!year || !month) return "Unknown";
  return `${MONTH_LABELS[month] || "Month"} ${year}`;
}

function short(name) {
  return String(name || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
}

// Canonical short label for a category (e.g. "Car / Jeep / Van (VC4)" -> "CJV").
function shortCat(name) {
  return CATEGORY_SHORT[name] || short(name);
}

// Sort an array of {name, ...} in canonical category order. Unknown names go last.
function sortByCategoryOrder(cats) {
  const ix = n => {
    const i = CATEGORY_ORDER.indexOf(n);
    return i === -1 ? 999 : i;
  };
  return cats.slice().sort((a, b) => ix(a.name) - ix(b.name));
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
