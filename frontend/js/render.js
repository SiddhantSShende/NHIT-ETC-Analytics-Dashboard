/* render.js — populates the dashboard DOM from an aggregated record. */

function renderResult(rec, trendArr, opts = {}) {
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

  // KPI row — values animate from 0 via Anim.setCount when available, so
  // every dashboard re-render gets a fresh count-up. Falls back to plain
  // setText if animations.js isn't loaded.
  const setVal = (id, txt) => (window.Anim ? window.Anim.setCount(id, txt) : setText(id, txt));

  setVal("kpiCount", fmtInt(rec.total_count));
  setText(
    "kpiCountSub",
    isRange
      ? `Total ETC transactions from ${formatMonthYear(start.year, start.month)} - ${formatMonthYear(end.year, end.month)}`
      : `Total ETC transactions in ${rec.month_name} ${rec.year}`
  );

  setVal("kpiAmount",    "₹" + fmtAmt(rec.total_amount));
  setText("kpiAmountSub", "Toll revenue collected via FASTag");

  setVal("kpiAvg",     "₹" + fmtMoney(rec.avg_per_txn));
  setText("kpiAvgSub",  "Mean fare across all categories");

  setVal("kpiCats",    String(rec.category_count));
  setText("kpiCatsSub", "Distinct vehicle categories recorded");

  // Highlight strip
  setText("hlTopAmt",     short(rec.top_by_amount.name));
  setText("hlTopAmtMeta", "₹" + fmtAmt(rec.top_by_amount.amount));

  setText("hlTopCnt",     short(rec.top_by_count.name));
  setText("hlTopCntMeta", fmtInt(rec.top_by_count.count) + " transactions");

  setVal("hlAvgRev",     "₹" + fmtAmt(rec.avg_revenue_per_day));
  setVal("hlAvgCnt",     fmtInt(rec.avg_count_per_day));

  // Section totals
  setVal("totalCount",  fmtInt(rec.total_count));
  setVal("totalAmount", "₹" + fmtAmt(rec.total_amount));

  // Tables
  const cats = rec.categories;
  renderShareTable("countTbody", cats, rec.total_count,  c => c.count,  fmtInt);
  renderShareTable("amtTbody",   cats, rec.total_amount, c => c.amount, v => "₹" + fmtAmt(v));
  renderDetailTable(cats);

  // Pies
  drawPie("cntPie", cats.map(c => short(c.name)), cats.map(c => c.count),  "cntChart", false);
  drawPie("amtPie", cats.map(c => short(c.name)), cats.map(c => c.amount), "amtChart", true);

  // Bar comparison + trend
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
      <td><span class="cat-dot" style="background:${color};color:${color}"></span>${escapeHtml(c.name)}</td>
      <td>${fmtFn(v)}</td>
      <td>${pct}%</td>
      <td style="width:140px">
        <div class="share-bar-bg"><div class="share-bar-fill" style="width:${bar}%;background:${color};color:${color}"></div></div>
      </td>
    </tr>`;
  }).join("");
}

function renderDetailTable(cats) {
  document.getElementById("detailTbody").innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    return `<tr style="--i:${i}">
      <td><span class="cat-dot" style="background:${color};color:${color}"></span>${escapeHtml(c.name)}</td>
      <td>${fmtInt(c.count)}</td>
      <td>${c.share_count.toFixed(2)}%</td>
      <td>₹${fmtAmt(c.amount)}</td>
      <td>${c.share_amount.toFixed(2)}%</td>
      <td>₹${fmtMoney(c.avg_fare)}</td>
    </tr>`;
  }).join("");
}
