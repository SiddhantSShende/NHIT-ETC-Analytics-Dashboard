/* NHIT | ETC Analytics Dashboard — frontend.
 *
 * Hierarchical filter flow:
 *   SPV → Round → Project → Plaza → Year + Month
 *
 * Each filter has an "All …" option. Leaving a level blank means
 * "include everything below it"; the backend (`/api/aggregate`) sums
 * across every plaza that matches.
 */

const API = "";   // same-origin (Flask serves index.html on :5050)

const PIE_COLORS = [
  "#003087", "#f47920", "#7ab648", "#00adef",
  "#e63946", "#8338ec", "#ffd60a", "#06d6a0",
  "#ef476f", "#118ab2", "#ffa552", "#4cc9f0",
];

// Chart instances — kept off `window` to avoid colliding with elements
// whose `id` attribute the browser exposes as global properties.
const charts = {};
const STATES = ["hintState", "loadingState", "noDataState", "dashboard"];
function showOnly(id) {
  STATES.forEach(s => document.getElementById(s)?.classList.toggle("hidden", s !== id));
}

// Taxonomy held in memory so cascading dropdowns don't need round-trips.
let TAXONOMY_ROWS = [];
let META = { plazas: [], years: [], months: [] };

window.addEventListener("DOMContentLoaded", () => {
  bootstrap();
  document.getElementById("btnView").addEventListener("click", loadData);

  ["spvSel", "roundSel"].forEach(id => {
    document.getElementById(id).addEventListener("change", refreshCascade);
  });
});

// ── BOOTSTRAP ────────────────────────────────────────────────────────────────
async function bootstrap() {
  try {
    const [meta, taxonomy] = await Promise.all([
      fetch(`${API}/api/meta`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      fetch(`${API}/api/taxonomy`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
    ]);
    META = meta;
    TAXONOMY_ROWS = taxonomy.rows || [];

    // Year + Month — populated once.
    fillSelect("yearSel",  meta.years  || [], y => ({ v: y, t: y }), "-- Year --");
    fillSelect("monthSel", meta.months || [], m => ({ v: m.num, t: m.name }), "-- Month --");

    // SPV first — once chosen it filters the rest. We seed the others empty
    // and let `refreshCascade` populate them based on current selection.
    fillSelect("spvSel",     taxonomy.spvs   || [], v => ({ v, t: v }), "All SPVs");
    refreshCascade();

    const sub = document.getElementById("hintSub");
    sub.textContent =
      `${TAXONOMY_ROWS.length} plazas across ${(taxonomy.spvs || []).length} SPVs · ` +
      `${(meta.years || []).length} year(s) · ${(meta.months || []).length} month(s) of data ready.`;
  } catch (e) {
    console.error(e);
    document.getElementById("hintSub").textContent =
      "Cannot reach API. Run `python server.py` and reload.";
  }
}

// ── CASCADING DROPDOWNS ─────────────────────────────────────────────────────
function refreshCascade() {
  const spv    = document.getElementById("spvSel").value;
  const round_ = document.getElementById("roundSel").value;

  // Round options: only rounds present in taxonomy under the chosen SPV.
  const roundsForSpv = uniqueSorted(
    TAXONOMY_ROWS.filter(r => !spv || r.spv === spv).map(r => r.round)
  );
  resetSelect("roundSel", roundsForSpv, round_, "All Rounds");

  // Plaza options: filtered by SPV + Round.
  const newRound = document.getElementById("roundSel").value;
  const plazasForScope = uniqueSorted(
    TAXONOMY_ROWS
      .filter(r => (!spv || r.spv === spv)
                && (!newRound || r.round === newRound))
      .map(r => r.excel_plaza)
  );
  resetSelect("plazaSel", plazasForScope, document.getElementById("plazaSel").value, "All Plazas");
}

function resetSelect(id, values, prevValue, allLabel) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    values.map(v => `<option value="${escapeAttr(v)}">${escapeHtml(v)}</option>`).join("");
  // Preserve previous selection if it's still valid.
  if (values.includes(prevValue)) sel.value = prevValue;
}

function uniqueSorted(arr) {
  return Array.from(new Set(arr.filter(Boolean))).sort();
}

// ── ANALYTICS LOAD ──────────────────────────────────────────────────────────
async function loadData() {
  const spv    = document.getElementById("spvSel").value;
  const round_ = document.getElementById("roundSel").value;
  const plaza  = document.getElementById("plazaSel").value;
  const year   = parseInt(document.getElementById("yearSel").value);
  const month  = parseInt(document.getElementById("monthSel").value);

  if (!year || !month) {
    alert("Please select a Year and Month.");
    return;
  }

  showOnly("loadingState");

  const filterQs   = new URLSearchParams({ spv, round: round_, plaza });
  const dataQs     = new URLSearchParams({ spv, round: round_, plaza, year, month });

  try {
    const [dataRes, trendRes] = await Promise.all([
      fetch(`${API}/api/aggregate?${dataQs}`),
      fetch(`${API}/api/aggregate-trend?${filterQs}`),
    ]);

    const dataJson = await dataRes.json().catch(() => ({}));

    if (!dataRes.ok) {
      document.getElementById("errorTitle").textContent = "No Data";
      document.getElementById("errorTxt").textContent =
        dataJson.error || `HTTP ${dataRes.status}`;
      showOnly("noDataState");
      return;
    }
    if (!dataJson.record || !dataJson.record.categories?.length) {
      document.getElementById("errorTitle").textContent = "No Data Found";
      document.getElementById("errorTxt").textContent =
        `No transaction data matched the selected filters in ${month}/${year}.`;
      showOnly("noDataState");
      return;
    }

    let trendArr = [];
    if (trendRes.ok) {
      const tJson = await trendRes.json().catch(() => ({}));
      trendArr = tJson.trend || [];
    }

    showOnly("dashboard");
    try {
      renderResult(dataJson.record, trendArr);
    } catch (renderErr) {
      console.error("Render failed:", renderErr);
      document.getElementById("errorTitle").textContent = "Render Error";
      document.getElementById("errorTxt").textContent =
        renderErr.message || "Failed to render the dashboard.";
      showOnly("noDataState");
    }
  } catch (e) {
    console.error(e);
    document.getElementById("errorTitle").textContent = "Network Error";
    document.getElementById("errorTxt").textContent =
      "Could not reach the API. Is server.py running?";
    showOnly("noDataState");
  }
}

// ── RENDER ───────────────────────────────────────────────────────────────────
function renderResult(rec, trendArr) {
  // Header
  setText("resultTitle", rec.scope?.label || "All NHIT Plazas");
  setText("resultSub",  `${rec.month_name} ${rec.year} · ETC FASTag Transaction Report`);
  setText("scopeMeta",  buildScopeMeta(rec));

  // KPI row
  setText("kpiCount",     fmtInt(rec.total_count));
  setText("kpiCountSub",  `Total ETC transactions in ${rec.month_name} ${rec.year}`);

  setText("kpiAmount",    "₹" + fmtAmt(rec.total_amount));
  setText("kpiAmountSub", "Toll revenue collected via FASTag");

  setText("kpiAvg",       "₹" + fmtMoney(rec.avg_per_txn));
  setText("kpiAvgSub",    "Mean fare across all categories");

  setText("kpiCats",      rec.category_count);
  setText("kpiCatsSub",   "Distinct vehicle categories recorded");

  // Highlight strip
  setText("hlTopAmt",     short(rec.top_by_amount.name));
  setText("hlTopAmtMeta", "₹" + fmtAmt(rec.top_by_amount.amount));

  setText("hlTopCnt",     short(rec.top_by_count.name));
  setText("hlTopCntMeta", fmtInt(rec.top_by_count.count) + " transactions");

  setText("hlAvgRev",     "₹" + fmtAmt(rec.avg_revenue_per_day));
  setText("hlAvgCnt",     fmtInt(rec.avg_count_per_day));

  // Section totals
  setText("totalCount",  fmtInt(rec.total_count));
  setText("totalAmount", "₹" + fmtAmt(rec.total_amount));

  // Tables
  const cats = rec.categories;
  renderShareTable("countTbody", cats, rec.total_count, c => c.count, fmtInt);
  renderShareTable("amtTbody",   cats, rec.total_amount, c => c.amount, v => "₹" + fmtAmt(v));
  renderDetailTable(cats);

  // Pies
  drawPie("cntPie", cats.map(c => short(c.name)), cats.map(c => c.count),  "cntChart", false);
  drawPie("amtPie", cats.map(c => short(c.name)), cats.map(c => c.amount), "amtChart", true);

  // Bar comparison + trend
  drawBar(cats);
  drawTrend(trendArr, rec.year, rec.month);
}

function buildScopeMeta(rec) {
  const sc = rec.scope || {};
  const bits = [];
  if (sc.spv)   bits.push(`SPV ${sc.spv}`);
  if (sc.round) bits.push(`Round ${sc.round}`);
  const plazasN = rec.plazas_included?.length || sc.plaza_count || 0;
  bits.push(plazasN === 1
    ? `1 plaza`
    : `${plazasN} plazas aggregated`);
  return bits.join(" · ");
}

function renderShareTable(bodyId, cats, total, valueFn, fmtFn) {
  const max = Math.max(...cats.map(valueFn), 1);
  document.getElementById(bodyId).innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    const v = valueFn(c);
    const pct = total ? (v / total * 100).toFixed(1) : "0.0";
    const bar = (v / max * 100).toFixed(1);
    return `<tr>
      <td><span class="cat-dot" style="background:${color}"></span>${escapeHtml(c.name)}</td>
      <td>${fmtFn(v)}</td>
      <td>${pct}%</td>
      <td style="width:140px">
        <div class="share-bar-bg"><div class="share-bar-fill" style="width:${bar}%;background:${color}"></div></div>
      </td>
    </tr>`;
  }).join("");
}

function renderDetailTable(cats) {
  document.getElementById("detailTbody").innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    return `<tr>
      <td><span class="cat-dot" style="background:${color}"></span>${escapeHtml(c.name)}</td>
      <td>${fmtInt(c.count)}</td>
      <td>${c.share_count.toFixed(2)}%</td>
      <td>₹${fmtAmt(c.amount)}</td>
      <td>${c.share_amount.toFixed(2)}%</td>
      <td>₹${fmtMoney(c.avg_fare)}</td>
    </tr>`;
  }).join("");
}

// ── CHARTS ──────────────────────────────────────────────────────────────────
function drawPie(canvasId, labels, data, chartKey, isMoney) {
  destroy(chartKey);
  charts[chartKey] = new Chart(document.getElementById(canvasId), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: PIE_COLORS.slice(0, labels.length),
        borderWidth: 0,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: "58%",
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#1a2332",
          titleColor: "#fff",
          bodyColor: "#cbd5e1",
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = total ? (ctx.parsed / total * 100).toFixed(1) : "0.0";
              const v = isMoney ? ("₹" + fmtAmt(ctx.parsed)) : fmtInt(ctx.parsed);
              return ` ${ctx.label}: ${v} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

function drawBar(cats) {
  destroy("barChart");
  charts.barChart = new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: {
      labels: cats.map(c => short(c.name)),
      datasets: [
        {
          label: "Transactions",
          data: cats.map(c => c.count),
          backgroundColor: "#003087",
          yAxisID: "y",
          borderRadius: 6,
        },
        {
          label: "Revenue (₹)",
          data: cats.map(c => c.amount),
          backgroundColor: "#f47920",
          yAxisID: "y1",
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.dataset.label.includes("Revenue")
                ? "₹" + fmtAmt(ctx.parsed.y)
                : fmtInt(ctx.parsed.y);
              return ` ${ctx.dataset.label}: ${v}`;
            },
          },
        },
      },
      scales: {
        y:  { type: "linear", position: "left",  beginAtZero: true,
              title: { display: true, text: "Transactions" },
              ticks: { callback: v => fmtInt(v) } },
        y1: { type: "linear", position: "right", beginAtZero: true,
              title: { display: true, text: "Revenue (₹)" },
              grid: { drawOnChartArea: false },
              ticks: { callback: v => fmtAmt(v) } },
      },
    },
  });
}

function drawTrend(trend, selYear, selMonth) {
  const sub = document.getElementById("trendSub");
  destroy("trendChart");

  if (!trend || !trend.length) {
    sub.textContent = "No additional months available for this scope.";
    return;
  }
  sub.textContent = `Transactions and revenue across ${trend.length} month(s). Selected month is highlighted.`;

  const labels = trend.map(t => t.label);
  const counts = trend.map(t => t.count);
  const amts   = trend.map(t => t.amount);
  const colors = trend.map(t => (t.year === selYear && t.month === selMonth) ? "#f47920" : "#003087");

  charts.trendChart = new Chart(document.getElementById("trendChart"), {
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Transactions", data: counts,
          backgroundColor: colors, yAxisID: "y", borderRadius: 6, order: 2 },
        { type: "line", label: "Revenue (₹)", data: amts,
          borderColor: "#7ab648", backgroundColor: "#7ab648",
          yAxisID: "y1", tension: 0.3, pointRadius: 4, order: 1 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.dataset.label.includes("Revenue")
                ? "₹" + fmtAmt(ctx.parsed.y)
                : fmtInt(ctx.parsed.y);
              return ` ${ctx.dataset.label}: ${v}`;
            },
          },
        },
      },
      scales: {
        y:  { type: "linear", position: "left",  beginAtZero: true,
              title: { display: true, text: "Transactions" },
              ticks: { callback: v => fmtInt(v) } },
        y1: { type: "linear", position: "right", beginAtZero: true,
              title: { display: true, text: "Revenue (₹)" },
              grid: { drawOnChartArea: false },
              ticks: { callback: v => fmtAmt(v) } },
      },
    },
  });
}

// ── UTIL ─────────────────────────────────────────────────────────────────────
function destroy(key) {
  const c = charts[key];
  if (c && typeof c.destroy === "function") c.destroy();
  charts[key] = null;
}

function fillSelect(id, items, mapper, allLabel) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="">${allLabel ?? "-- Select --"}</option>`;
  items.forEach(item => {
    const { v, t } = mapper(item);
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    sel.appendChild(o);
  });
}

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

function short(name) {
  return String(name || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
}

function setText(id, txt) {
  const el = document.getElementById(id);
  if (el) el.textContent = txt;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function escapeAttr(s) { return escapeHtml(s); }
