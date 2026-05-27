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

  // YoY top KPI card + section-card pills — works in both single-month and
  // range mode. For range mode, prevYear is the same start..end window
  // shifted back by one year; if that window doesn't exist (e.g. anything
  // before Jan 2022), prevYear is null and renderDelta falls back to
  // "No comparison data available".
  const yoyCard = document.getElementById("kpiYoy")?.closest(".kpi-card");
  const sectionPillWraps = document.querySelectorAll(".sec-growth-pills");
  yoyCard?.classList.remove("hidden");
  sectionPillWraps.forEach(w => w.classList.remove("hidden"));
  const { prevYear } = deltas || {};
  const pyLabel = isRange
    ? rangePrevYearLabel(start, end)
    : prevYearLabel(rec.year, rec.month);
  const fmtTxn  = v => fmtTxnL(v);
  const fmtRev  = v => "₹" + fmtRevCr(v);
  renderDelta("kpiYoy", "kpiYoySub", null,
    rec.total_amount, prevYear?.total_amount,  pyLabel, fmtRev);
  renderDelta("countYoy", "countYoySub", null,
    rec.total_count,  prevYear?.total_count,   pyLabel, fmtTxn);
  renderDelta("amtYoy",   "amtYoySub",   null,
    rec.total_amount, prevYear?.total_amount,  pyLabel, fmtRev);

  // Average revenue per day — already computed by data-layer.js as
  // total_amount / days_in_period (single-month: calendar days; range:
  // summed days across all months in the window).
  const days = rec.days_in_period || 0;
  setVal("kpiAvgDaily", "₹" + fmtRevCr(rec.avg_revenue_per_day || 0));
  setText(
    "kpiAvgDailySub",
    isRange
      ? `Daily average across ${days} day${days === 1 ? "" : "s"} in selected range`
      : `Daily average · ${days} days in ${rec.month_name} ${rec.year}`
  );

  // Section totals
  setVal("totalCount",  fmtTxnL(rec.total_count));
  setVal("totalAmount", "₹" + fmtRevCr(rec.total_amount));

  // Tables — strict unit rule: transactions always in Lakhs, revenue always in Crores.
  // Per-category YoY: prior-period category array comes from the same
  // aggregate() / aggregateRange() call that populates the section pill.
  // Works in both single-month mode (vs same month last year) and range
  // mode (vs the same range shifted back one year).
  const cats = rec.categories;
  const pyCats = prevYear?.categories || null;

  document.getElementById("countTable")?.classList.remove("is-range");
  document.getElementById("amtTable")?.classList.remove("is-range");

  renderShareTable("countTbody", cats, rec.total_count,  c => c.count,  fmtTxnL,                pyCats);
  renderShareTable("amtTbody",   cats, rec.total_amount, c => c.amount, v => "₹" + fmtRevCr(v), pyCats);

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

function renderShareTable(bodyId, cats, total, valueFn, fmtFn, prevYearCats) {
  const body = document.getElementById(bodyId);
  // FY 2023-24 / 2024-25 source PDFs only carry plaza totals — no
  // VC4..VC11+ breakdown. Show a single placeholder row so the table
  // doesn't render as an empty void below the KPI total.
  if (!cats || !cats.length) {
    body.innerHTML = `
      <tr class="no-breakdown-row">
        <td colspan="5" style="text-align:center; padding:24px 12px; color:var(--text2); font-style:italic;">
          —&nbsp;&nbsp;Per-category breakdown not available for this period&nbsp;&nbsp;—
        </td>
      </tr>`;
    return;
  }

  const max = Math.max(...cats.map(valueFn), 1);
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

  body.innerHTML = cats.map((c, i) => {
    const color = PIE_COLORS[i % PIE_COLORS.length];
    const v   = valueFn(c);
    const pct = total ? (v / total * 100).toFixed(1) : "0.0";
    const bar = (v / max * 100).toFixed(1);
    return `<tr style="--i:${i}">
      <td><span class="cat-dot" style="background:${color};color:${color}"></span>${escapeHtml(shortCat(c.name))}</td>
      <td>${fmtFn(v)}</td>
      <td>${pct}%</td>
      ${deltaCell(v, pyByName.get(c.name), "yoy-col")}
      <td style="width:140px">
        <div class="share-bar-bg"><div class="share-bar-fill" style="width:${bar}%;background:${color};color:${color}"></div></div>
      </td>
    </tr>`;
  }).join("");
}

// ── YoY delta rendering ─────────────────────────────────────────────────────
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

function prevYearLabel(year, month) {
  return `${MONTH_LABELS[month].slice(0, 3)} ${year - 1}`;
}

function rangePrevYearLabel(start, end) {
  const sm = MONTH_LABELS[start.month].slice(0, 3);
  const em = MONTH_LABELS[end.month].slice(0, 3);
  return `${sm} ${start.year - 1} – ${em} ${end.year - 1}`;
}
