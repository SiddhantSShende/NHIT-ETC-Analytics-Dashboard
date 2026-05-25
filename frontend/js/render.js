/* render.js — populates the dashboard DOM from an aggregated record. */

function renderResult(rec, trendArr, opts = {}, deltas = {}) {
  const isRange = opts.mode === "range" || !!rec.period;
  const start = rec.period?.start || { year: opts.startYear, month: opts.startMonth };
  const end   = rec.period?.end   || { year: opts.endYear,   month: opts.endMonth };
  const rangeText = isRange
    ? `${formatMonthYear(start.year, start.month)} - ${formatMonthYear(end.year, end.month)}`
    : "";

  // Header
  setText("resultTitle", rec.scope?.label || "All NHIT Plazas");
  if (isRange) {
    setText("resultSub", `${rangeText} · ETC FASTag Transaction Report`);
  } else {
    setText("resultSub", `${rec.month_name} ${rec.year} · ETC FASTag Transaction Report`);
  }
  setText("scopeMeta", buildScopeMeta(rec));

  // KPI row — Total Transactions in Lakhs, Total Revenue in Crores. Values
  // animate via Anim.setCount when available, falling back to plain setText.
  const setVal = (id, txt) => (window.Anim ? window.Anim.setCount(id, txt) : setText(id, txt));

  setVal("kpiCount", fmtTxnL(rec.total_count));
  setText(
    "kpiCountSub",
    isRange
      ? `Total ETC transactions from ${formatMonthYear(start.year, start.month)} - ${formatMonthYear(end.year, end.month)}`
      : `Total ETC transactions in ${rec.month_name} ${rec.year}`
  );

  setVal("kpiAmount",    "₹" + fmtRevCr(rec.total_amount));
  setText("kpiAmountSub", "Toll revenue collected via FASTag");

  // Highlight strip — Avg daily uses the actual days-in-period.
  const days = rec.days_in_period || 30;
  const dayMeta = isRange
    ? `across ${days} days in this range`
    : `across ${days} days in ${rec.month_name}`;
  setVal("hlAvgCnt", fmtTxnL(rec.avg_count_per_day));
  setVal("hlAvgRev", "₹" + fmtRevCr(rec.avg_revenue_per_day));
  setText("hlAvgCntMeta", dayMeta);
  setText("hlAvgRevMeta", dayMeta);

  // MoM/YoY cards only make sense in single-month mode. Range mode hides
  // them and collapses the highlight row to a 2-col grid.
  const row = document.getElementById("highlightRow");
  if (row) {
    if (isRange) {
      row.setAttribute("data-mode", "range");
      row.querySelectorAll("[data-mom-yoy]").forEach(c => c.classList.add("hidden"));
    } else {
      row.removeAttribute("data-mode");
      row.querySelectorAll("[data-mom-yoy]").forEach(c => c.classList.remove("hidden"));
      const { prevMonth, prevYear } = deltas;
      const pmLabel = prevMonthLabel(rec.year, rec.month);
      const pyLabel = prevYearLabel(rec.year, rec.month);
      renderDelta("hlTxnMom", "hlTxnMomMeta", rec.total_count,  prevMonth?.total_count,  pmLabel);
      renderDelta("hlTxnYoy", "hlTxnYoyMeta", rec.total_count,  prevYear?.total_count,   pyLabel);
      renderDelta("hlRevMom", "hlRevMomMeta", rec.total_amount, prevMonth?.total_amount, pmLabel);
      renderDelta("hlRevYoy", "hlRevYoyMeta", rec.total_amount, prevYear?.total_amount,  pyLabel);
    }
  }

  // Section totals
  setVal("totalCount",  fmtTxnL(rec.total_count));
  setVal("totalAmount", "₹" + fmtRevCr(rec.total_amount));

  // Tables — per-category share tables keep the smart fmtInt/fmtAmt so
  // small per-slice values stay readable.
  const cats = rec.categories;
  renderShareTable("countTbody", cats, rec.total_count,  c => c.count,  fmtInt);
  renderShareTable("amtTbody",   cats, rec.total_amount, c => c.amount, v => "₹" + fmtAmt(v));

  // Pies + bar comparison + trend — use short labels everywhere.
  drawPie("cntPie", cats.map(c => shortCat(c.name)), cats.map(c => c.count),  "cntChart", false);
  drawPie("amtPie", cats.map(c => shortCat(c.name)), cats.map(c => c.amount), "amtChart", true);

  drawBar(cats);
  drawTrend(trendArr, isRange ? null : rec.year, isRange ? null : rec.month);

  // Reset scroll-reveal so KPIs/sections animate in fresh on every render.
  // Then re-bind the cursor spotlight in case new spotlight cards landed.
  if (window.Anim) {
    document.querySelectorAll(".dashboard .reveal").forEach(el => {
      el.classList.remove("reveal--in");
      el.style.removeProperty("--i");
    });
    window.Anim.observeReveals(document.getElementById("dashboard") || document);
    window.Anim.bindSpotlight(document.getElementById("dashboard") || document);
  }
}

function buildScopeMeta(rec) {
  const sc = rec.scope || {};
  const bits = [];
  if (sc.spv)   bits.push(`SPV ${sc.spv}`);
  if (sc.round) bits.push(`Round ${sc.round}`);
  if (rec.months_included?.length) bits.push(`${rec.months_included.length} month(s) aggregated`);
  const plazasN = rec.plazas_included?.length || sc.plaza_count || 0;
  bits.push(plazasN === 1 ? `1 plaza` : `${plazasN} plazas aggregated`);
  return bits.join(" · ");
}

function renderShareTable(bodyId, cats, total, valueFn, fmtFn) {
  const max = Math.max(...cats.map(valueFn), 1);
  document.getElementById(bodyId).innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    const v   = valueFn(c);
    const pct = total ? (v / total * 100).toFixed(1) : "0.0";
    const bar = (v / max * 100).toFixed(1);
    return `<tr style="--i:${i}">
      <td><span class="cat-dot" style="background:${color};color:${color}"></span>${escapeHtml(shortCat(c.name))}</td>
      <td>${fmtFn(v)}</td>
      <td>${pct}%</td>
      <td style="width:140px">
        <div class="share-bar-bg"><div class="share-bar-fill" style="width:${bar}%;background:${color};color:${color}"></div></div>
      </td>
    </tr>`;
  }).join("");
}

// ── MoM / YoY delta rendering ───────────────────────────────────────────────
function renderDelta(valueId, metaId, curr, prev, comparisonLabel) {
  const valEl = document.getElementById(valueId);
  const metaEl = document.getElementById(metaId);
  if (!valEl || !metaEl) return;
  const pct = pctDelta(curr, prev);
  valEl.classList.remove("delta-up", "delta-down");
  if (pct === null) {
    valEl.textContent = "—";
    metaEl.textContent = prev == null
      ? "No comparison data"
      : `vs ${comparisonLabel}`;
    return;
  }
  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "•";
  valEl.textContent = `${arrow} ${fmtPct(pct)}`;
  if (pct > 0) valEl.classList.add("delta-up");
  else if (pct < 0) valEl.classList.add("delta-down");
  metaEl.textContent = `vs ${comparisonLabel}`;
}

function prevMonthLabel(year, month) {
  const m = month === 1 ? 12 : month - 1;
  const y = month === 1 ? year - 1 : year;
  return `${MONTH_LABELS[m].slice(0, 3)} ${y}`;
}

function prevYearLabel(year, month) {
  return `${MONTH_LABELS[month].slice(0, 3)} ${year - 1}`;
}
