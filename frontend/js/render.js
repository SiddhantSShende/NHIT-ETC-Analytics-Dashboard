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

  // MoM/YoY top KPI cards + section-card pills — single-month mode only.
  const momCard = document.getElementById("kpiMom")?.closest(".kpi-card");
  const yoyCard = document.getElementById("kpiYoy")?.closest(".kpi-card");
  const sectionPillWraps = document.querySelectorAll(".sec-growth-pills");
  if (isRange) {
    momCard?.classList.add("hidden");
    yoyCard?.classList.add("hidden");
    sectionPillWraps.forEach(w => w.classList.add("hidden"));
  } else {
    momCard?.classList.remove("hidden");
    yoyCard?.classList.remove("hidden");
    sectionPillWraps.forEach(w => w.classList.remove("hidden"));
    const { prevMonth, prevYear } = deltas;
    const pmLabel = prevMonthLabel(rec.year, rec.month);
    const pyLabel = prevYearLabel(rec.year, rec.month);
    const fmtTxn  = v => fmtTxnL(v);
    const fmtRev  = v => "₹" + fmtRevCr(v);
    renderDelta("kpiMom", "kpiMomSub", null,
      rec.total_amount, prevMonth?.total_amount, pmLabel, fmtRev);
    renderDelta("kpiYoy", "kpiYoySub", null,
      rec.total_amount, prevYear?.total_amount,  pyLabel, fmtRev);

    // Section-card pills — Transaction Count uses counts, Revenue uses amounts.
    renderDelta("countMom", "countMomSub", null,
      rec.total_count,  prevMonth?.total_count,  pmLabel, fmtTxn);
    renderDelta("countYoy", "countYoySub", null,
      rec.total_count,  prevYear?.total_count,   pyLabel, fmtTxn);
    renderDelta("amtMom",   "amtMomSub",   null,
      rec.total_amount, prevMonth?.total_amount, pmLabel, fmtRev);
    renderDelta("amtYoy",   "amtYoySub",   null,
      rec.total_amount, prevYear?.total_amount,  pyLabel, fmtRev);
  }

  // Section totals
  setVal("totalCount",  fmtTxnL(rec.total_count));
  setVal("totalAmount", "₹" + fmtRevCr(rec.total_amount));

  // Tables — strict unit rule: transactions always in Lakhs, revenue always in Crores.
  // Per-category MoM/YoY: prior-period category arrays come from the same
  // aggregate() call that populates the section pills; in range mode no
  // single prior period is meaningful, so we suppress the columns entirely
  // (CSS hides .mom-col / .yoy-col when the table has .is-range).
  const cats = rec.categories;
  const { prevMonth: prevMonthRec, prevYear: prevYearRec } = deltas || {};
  const pmCats = isRange ? null : prevMonthRec?.categories;
  const pyCats = isRange ? null : prevYearRec?.categories;

  document.getElementById("countTable")?.classList.toggle("is-range", isRange);
  document.getElementById("amtTable")?.classList.toggle("is-range", isRange);

  renderShareTable("countTbody", cats, rec.total_count,  c => c.count,  fmtTxnL,                pmCats, pyCats);
  renderShareTable("amtTbody",   cats, rec.total_amount, c => c.amount, v => "₹" + fmtRevCr(v), pmCats, pyCats);

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

function renderShareTable(bodyId, cats, total, valueFn, fmtFn, prevMonthCats, prevYearCats) {
  const max = Math.max(...cats.map(valueFn), 1);
  const pmByName = new Map((prevMonthCats || []).map(c => [c.name, c]));
  const pyByName = new Map((prevYearCats  || []).map(c => [c.name, c]));

  const deltaCell = (curr, prev, colClass) => {
    // Short-circuit when the category was absent in the prior period —
    // otherwise valueFn(undefined) would throw on c.count / c.amount.
    const prevVal = prev != null ? valueFn(prev) : null;
    const pct = pctDelta(curr, prevVal);
    if (pct === null) {
      return `<td class="delta-cell ${colClass}" aria-label="No comparison data">—</td>`;
    }
    const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "•";
    const dirCls = pct > 0 ? "delta-up" : pct < 0 ? "delta-down" : "";
    const verb   = pct > 0 ? "increase" : pct < 0 ? "decrease" : "no change";
    return `<td class="delta-cell ${colClass} ${dirCls}" aria-label="${fmtPct(pct)} ${verb}">${arrow} ${fmtPct(pct)}</td>`;
  };

  document.getElementById(bodyId).innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    const v   = valueFn(c);
    const pct = total ? (v / total * 100).toFixed(1) : "0.0";
    const bar = (v / max * 100).toFixed(1);
    return `<tr style="--i:${i}">
      <td><span class="cat-dot" style="background:${color};color:${color}"></span>${escapeHtml(shortCat(c.name))}</td>
      <td>${fmtFn(v)}</td>
      <td>${pct}%</td>
      ${deltaCell(v, pmByName.get(c.name), "mom-col")}
      ${deltaCell(v, pyByName.get(c.name), "yoy-col")}
      <td style="width:140px">
        <div class="share-bar-bg"><div class="share-bar-fill" style="width:${bar}%;background:${color};color:${color}"></div></div>
      </td>
    </tr>`;
  }).join("");
}

// ── MoM / YoY delta rendering ───────────────────────────────────────────────
// Writes the signed percentage into valueId, a description sentence into
// metaId, and (optionally) the previous absolute value into prevId. When the
// value element is inside a `.kpi-growth` KPI card, this also flips the
// card's themed halo + value gradient via `kpi-up` / `kpi-down`.
function renderDelta(valueId, metaId, prevId, curr, prev, comparisonLabel, fmt) {
  const valEl  = document.getElementById(valueId);
  const metaEl = document.getElementById(metaId);
  const prevEl = prevId ? document.getElementById(prevId) : null;
  if (!valEl || !metaEl) return;

  const kpiCard = valEl.closest(".kpi-card, .sec-growth-pill");
  const pct = pctDelta(curr, prev);

  valEl.classList.remove("delta-up", "delta-down");
  if (kpiCard) kpiCard.classList.remove("kpi-up", "kpi-down");

  if (pct === null) {
    valEl.textContent = "—";
    metaEl.textContent = prev == null
      ? "No comparison data available"
      : `vs ${comparisonLabel}`;
    if (prevEl) {
      prevEl.innerHTML = (prev != null && fmt)
        ? `Previous: <strong>${fmt(prev)}</strong>`
        : "&nbsp;";
    }
    return;
  }

  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "•";
  valEl.textContent = `${arrow} ${fmtPct(pct)}`;
  if (pct > 0) {
    valEl.classList.add("delta-up");
    if (kpiCard) kpiCard.classList.add("kpi-up");
  } else if (pct < 0) {
    valEl.classList.add("delta-down");
    if (kpiCard) kpiCard.classList.add("kpi-down");
  }

  const verb = pct > 0 ? "increase" : pct < 0 ? "decrease" : "change";
  // When a separate prev element exists, meta gets the verb sentence and
  // prev gets the value. When no prev element is provided (e.g. top-of-page
  // KPI cards that only have a sub line), fold the prev value into the meta
  // so users still see the comparison number.
  if (prevEl) {
    metaEl.textContent = `${verb} vs ${comparisonLabel}`;
    prevEl.innerHTML = (prev != null && fmt)
      ? `${comparisonLabel}: <strong>${fmt(prev)}</strong>`
      : "&nbsp;";
  } else {
    metaEl.textContent = (prev != null && fmt)
      ? `${verb} vs ${comparisonLabel} (${fmt(prev)})`
      : `${verb} vs ${comparisonLabel}`;
  }
}

function prevMonthLabel(year, month) {
  const m = month === 1 ? 12 : month - 1;
  const y = month === 1 ? year - 1 : year;
  return `${MONTH_LABELS[m].slice(0, 3)} ${y}`;
}

function prevYearLabel(year, month) {
  return `${MONTH_LABELS[month].slice(0, 3)} ${year - 1}`;
}
