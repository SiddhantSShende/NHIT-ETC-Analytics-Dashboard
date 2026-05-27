/* NHIT | Browser-side data layer.
 *
 * Replaces the Flask /api/{meta,taxonomy,aggregate,aggregate-range,aggregate-trend}
 * endpoints. The frontend now fetches JSON files directly from
 * downloads/json/, caches them via the Cache Storage API, and replicates
 * analytics.py's category enrichment + range aggregation in JS.
 *
 * Public surface (UMD-ish, attached to window.DataLayer):
 *   getMeta(): {plazas, years, months}
 *   getTaxonomy(): {rows, spvs, rounds, unmatched}
 *   aggregate({spv, round, plaza, year, month}): {record, prev_year}
 *     — prev_year may be null when comparison data is missing.
 *   aggregateRange({spv, round, plaza, start_year, start_month, end_year, end_month}): {record}
 *   aggregateTrend({spv, round, plaza}): {trend}
 *
 * The shape returned by aggregate / aggregateRange / aggregateTrend matches
 * the old API responses byte-for-byte so app.js can stay structurally the same.
 */
(function () {
  "use strict";

  const BASE = "downloads/json";
  // Bump this whenever the JSON shape changes OR a deploy refreshes data files
  // that may already be sitting in users' Cache Storage. The cache has no
  // server-revalidation step, so the only way to force a refetch is to switch
  // to a new cache name; the previous one is then orphaned (and the cleanup
  // sweep below evicts it on next page load).
  const CACHE_NAME = "nhit-data-v4";
  const MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  const memCache = new Map();   // url -> Promise<json>
  let cachePromise = null;

  function _getCacheStore() {
    if (cachePromise) return cachePromise;
    if (typeof caches === "undefined") {
      cachePromise = Promise.resolve(null);
    } else {
      cachePromise = caches.open(CACHE_NAME).then(async (store) => {
        // Evict orphaned nhit-data-vN caches from earlier deploys so they
        // don't permanently occupy ~25MB of disk on every returning visitor.
        try {
          const keys = await caches.keys();
          await Promise.all(
            keys.filter(k => k.startsWith("nhit-data-") && k !== CACHE_NAME)
                .map(k => caches.delete(k))
          );
        } catch (_) { /* private mode / unsupported — non-fatal */ }
        return store;
      }).catch(() => null);
    }
    return cachePromise;
  }

  async function fetchJson(path) {
    if (memCache.has(path)) return memCache.get(path);
    const url = `${BASE}/${path}`;
    const p = (async () => {
      const store = await _getCacheStore();
      if (store) {
        const hit = await store.match(url);
        if (hit) return hit.clone().json();
      }
      // "no-cache" forces a conditional request (If-Modified-Since /
      // If-None-Match) so the browser's HTTP cache can't pin a stale 404
      // from a pre-deploy visit (e.g. monthly/2026-04.json before April was
      // shipped) for the full 24h max-age. 304s are still fast — body comes
      // from the cache — but content changes and prior failures revalidate.
      const resp = await fetch(url, { cache: "no-cache" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status} ${url}`);
      if (store) {
        try { await store.put(url, resp.clone()); } catch (_) { /* private mode etc. */ }
      }
      return resp.json();
    })();
    memCache.set(path, p);
    return p;
  }

  let _indexP = null;
  let _taxP = null;
  let _periodsSetP = null;
  function getIndex()    { return _indexP ||= fetchJson("_index.json"); }
  function getTaxonomy() { return _taxP   ||= fetchJson("_taxonomy.json"); }
  function getMonthFile(period) { return fetchJson(`monthly/${period}.json`); }
  // Resolves once; subsequent checks are sync against the cached Set.
  // We use this to short-circuit fetches for periods we know don't exist
  // (e.g. user picks Feb 2023 — data starts at Apr 2023) so the dashboard
  // doesn't spend ~1s per file on revalidating 404s with cache: "no-cache".
  function getPeriodsSet() {
    return _periodsSetP ||= getIndex().then(idx => new Set(idx.periods || []));
  }

  // ── Meta (plazas + years + months + per-year months) ───────────────────────
  // `monthsByYear` lets the UI restrict the month dropdown to the months
  // actually present for the selected year (e.g. 2023 only has Apr-Dec).
  async function getMeta() {
    const idx = await getIndex();
    const plazas = idx.plazas.map(p => p.plaza_name);
    const periods = idx.periods || [];
    const years = Array.from(new Set(periods.map(p => parseInt(p.split("-")[0])))).sort();
    const seenMonths = new Map();
    const monthsByYear = {};
    for (const p of periods) {
      const [yStr, mStr] = p.split("-");
      const y = parseInt(yStr);
      const m = parseInt(mStr);
      if (!seenMonths.has(m)) seenMonths.set(m, MONTH_NAMES[m]);
      (monthsByYear[y] ||= []).push(m);
    }
    for (const y of Object.keys(monthsByYear)) {
      monthsByYear[y] = Array.from(new Set(monthsByYear[y])).sort((a, b) => a - b);
    }
    const months = Array.from(seenMonths.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([num, name]) => ({ num, name }));
    return { plazas, years, months, monthsByYear, periods };
  }

  // ── Filter taxonomy rows the same way server.py does ──────────────────────
  function _filterTaxonomyRows(tax, { spv, round: round_, project, plaza }) {
    const rows = tax.rows || [];
    return rows.filter(r => {
      if (!r.canonical_plaza) return false;
      if (spv     && (r.spv     || "").toLowerCase() !== spv.toLowerCase()) return false;
      if (round_  && (r.round   || "").toLowerCase() !== round_.toLowerCase()) return false;
      if (project && (r.project || "").toLowerCase() !== project.toLowerCase()) return false;
      if (plaza   && (r.excel_plaza || "").toLowerCase() !== plaza.toLowerCase()) return false;
      return true;
    });
  }

  function _scopeLabel({ spv, round: round_, project, plaza }) {
    if (plaza)   return plaza;
    if (project) return `${project} (${round_ || "All Rounds"}, ${spv || "All SPVs"})`;
    if (round_)  return `${spv || "All SPVs"} · ${round_}`;
    if (spv)     return `${spv} · All Rounds`;
    return "All NHIT Plazas";
  }

  // Resolve the candidate canonical plaza set for a filter.
  // Mirrors server.py's behaviour: if no filter is set OR no taxonomy match,
  // fall back to the full plaza universe.
  async function _resolvePlazaScope(filters) {
    const tax = await getTaxonomy();
    const idx = await getIndex();
    const allPlazas = idx.plazas.map(p => p.plaza_name);
    const rows = _filterTaxonomyRows(tax, filters);
    const noFilters = !filters.spv && !filters.round && !filters.project && !filters.plaza;
    let plazas;
    if (noFilters || rows.length === 0) {
      plazas = allPlazas.slice().sort();
    } else {
      plazas = Array.from(new Set(rows.map(r => r.canonical_plaza))).sort();
    }
    return { plazas, scopeRows: rows };
  }

  // ── Period helpers ────────────────────────────────────────────────────────
  function _iterMonths(sy, sm, ey, em) {
    const out = [];
    let y = sy, m = sm;
    while (y < ey || (y === ey && m <= em)) {
      out.push([y, m]);
      m += 1;
      if (m > 12) { y += 1; m = 1; }
    }
    return out;
  }


  // ── Per-plaza category extraction from a monthly file ─────────────────────
  // The new monthly files store `vehicles` as {label: {count, amount}} per
  // plaza. We need to convert that into the same `categories` array shape
  // that analytics.py emitted.
  function _extractCategoriesForPlaza(plazaRec) {
    if (!plazaRec || !plazaRec.vehicles) return [];
    return Object.entries(plazaRec.vehicles).map(([name, v]) => ({
      name,
      count: v.count || 0,
      amount: v.amount || 0,
    }));
  }

  function _buildPlazaIndex(monthlyDoc) {
    const map = new Map();
    for (const p of monthlyDoc.plazas || []) {
      map.set(p.plaza_name, p);
    }
    return map;
  }

  // ── Single-month aggregation (port of analytics.aggregate_plazas_for_month)
  // FY-summary periods (2023-04..2024-03) carry totals only — no vehicles.
  // We track those contributions separately so KPIs/trend stay populated
  // while category-driven widgets simply show no data for those months.
  function _aggregateMonth(plazaIndex, plazas, year, month) {
    const catMap = new Map();
    const plazasIncluded = [];
    let extraCount = 0, extraAmount = 0;
    for (const plaza of plazas) {
      const rec = plazaIndex.get(plaza);
      if (!rec) continue;
      const cats = _extractCategoriesForPlaza(rec);
      if (cats.length) {
        plazasIncluded.push(plaza);
        for (const c of cats) {
          const slot = catMap.get(c.name) || { count: 0, amount: 0 };
          slot.count += c.count;
          slot.amount += c.amount;
          catMap.set(c.name, slot);
        }
      } else if (rec.total && (rec.total.count || rec.total.amount)) {
        plazasIncluded.push(plaza);
        extraCount  += rec.total.count  || 0;
        extraAmount += rec.total.amount || 0;
      }
    }
    if (!catMap.size && extraCount === 0 && extraAmount === 0) return null;
    return _enrich(catMap, plazasIncluded, year, month, { extraCount, extraAmount });
  }

  function _enrich(catMap, plazasIncluded, year, month, extra = {}) {
    // Canonical display order (CJV → OSV); unknown names go last.
    const orderIx = n => {
      const i = CATEGORY_ORDER.indexOf(n);
      return i === -1 ? 999 : i;
    };
    const cats = Array.from(catMap.entries())
      .map(([name, v]) => ({
        name,
        count: v.count,
        amount: Math.round(v.amount * 100) / 100,
      }))
      .sort((a, b) => orderIx(a.name) - orderIx(b.name));

    // `extraCount` / `extraAmount` carry totals from FY-summary plazas that
    // lack a per-category breakdown — they're added to the KPI totals so
    // 2023-24 months still register, while the categories list stays
    // truthful (empty when no VC breakdown is available).
    const extraCount  = extra.extraCount  || 0;
    const extraAmount = extra.extraAmount || 0;
    const totalCount  = cats.reduce((a, c) => a + c.count, 0) + extraCount;
    const totalAmount = Math.round(
      (cats.reduce((a, c) => a + c.amount, 0) + extraAmount) * 100
    ) / 100;

    const enriched = cats.map(c => ({
      name: c.name,
      count: c.count,
      amount: c.amount,
      share_count:  totalCount  ? Math.round(c.count  / totalCount  * 10000) / 100 : 0,
      share_amount: totalAmount ? Math.round(c.amount / totalAmount * 10000) / 100 : 0,
      avg_fare:     c.count     ? Math.round(c.amount / c.count     * 100)   / 100 : 0,
    }));

    // Kept for backward compatibility — chatbot engine still reads these
    // even though the dashboard no longer renders the corresponding cards.
    // For FY-summary periods with no breakdown, enriched is [] so neither
    // top has meaningful data; emit safe placeholders.
    const topAmt = enriched.length
      ? enriched.reduce((a, b) => (b.amount > a.amount ? b : a), enriched[0])
      : { name: "", amount: 0 };
    const topCnt = enriched.length
      ? enriched.reduce((a, b) => (b.count  > a.count  ? b : a), enriched[0])
      : { name: "", count: 0 };

    // Days-in-period: range mode passes pre-summed daysCount; single month
    // uses the actual calendar days. Defensive fallback = 30.
    let days;
    if (extra.daysCount && extra.daysCount > 0) {
      days = extra.daysCount;
    } else if (year && month) {
      days = daysInMonth(year, month);
    } else {
      days = 30;
    }

    return {
      year,
      month,
      month_name: MONTH_NAMES[month] || "",
      categories: enriched,
      total_count: totalCount,
      total_amount: totalAmount,
      avg_per_txn: totalCount ? Math.round(totalAmount / totalCount * 100) / 100 : 0,
      avg_count_per_day:  totalCount  ? Math.round(totalCount / days)               : 0,
      avg_revenue_per_day: totalAmount ? Math.round(totalAmount / days * 100) / 100 : 0,
      days_in_period: days,
      category_count: enriched.length,
      top_by_amount: { name: topAmt.name, amount: topAmt.amount },
      top_by_count:  { name: topCnt.name, count: topCnt.count },
      plazas_included: plazasIncluded,
      ...(extra.period ? { period: extra.period, months_included: extra.monthsIncluded } : {}),
    };
  }

  // ── Internal: aggregate a single (year, month) for given plazas. Returns
  // the enriched record or null on missing data / missing month file.
  async function _tryAggregateSingle(plazas, year, month) {
    if (!year || !month) return null;
    const period = `${year}-${String(month).padStart(2, "0")}`;
    const periods = await getPeriodsSet();
    if (!periods.has(period)) return null;
    let doc;
    try { doc = await getMonthFile(period); } catch (_) { return null; }
    return _aggregateMonth(_buildPlazaIndex(doc), plazas, year, month);
  }

  // ── Public: aggregate for a single month with filters ─────────────────────
  // Also returns prev_year aggregate (computed with the same resolved plaza
  // scope) so the dashboard can render YoY deltas without making separate
  // API-style calls. prev_year may be null when the corresponding month file
  // does not exist or contains no matching plaza data — callers must handle
  // null gracefully.
  async function aggregate({ spv = "", round = "", project = "", plaza = "", year, month }) {
    if (!year || !month) throw new Error("year and month are required");
    const filters = { spv, round, project, plaza };
    const { plazas } = await _resolvePlazaScope(filters);
    if (!plazas.length) {
      const err = new Error("No plazas match the selected filters.");
      err.status = 404; throw err;
    }

    const [mainRec, prevYearRec] = await Promise.all([
      _tryAggregateSingle(plazas, year,     month),
      _tryAggregateSingle(plazas, year - 1, month),
    ]);

    if (!mainRec) {
      const period = `${year}-${String(month).padStart(2, "0")}`;
      const err = new Error(`No data found for the selected filters in ${period}.`);
      err.status = 404; throw err;
    }
    mainRec.scope = {
      label:   _scopeLabel(filters),
      spv:     spv     || null,
      round:   round   || null,
      project: project || null,
      plaza:   plaza   || null,
      plaza_count: plazas.length,
    };
    return {
      record:    mainRec,
      prev_year: prevYearRec,
    };
  }

  // ── Public: aggregate across a range ──────────────────────────────────────
  async function aggregateRange({
    spv = "", round = "", project = "", plaza = "",
    start_year, start_month, end_year, end_month,
  }) {
    if (!start_year || !start_month || !end_year || !end_month) {
      throw new Error("start/end year+month required");
    }
    if (start_year > end_year || (start_year === end_year && start_month > end_month)) {
      throw new Error("start date must be before end date");
    }
    const filters = { spv, round, project, plaza };
    const { plazas } = await _resolvePlazaScope(filters);
    if (!plazas.length) {
      const err = new Error("No plazas match the selected filters."); err.status = 404; throw err;
    }

    const months = _iterMonths(start_year, start_month, end_year, end_month);
    const catMap = new Map();
    const plazasSet = new Set();
    const monthsIncluded = [];
    let extraCount = 0, extraAmount = 0;
    const knownPeriods = await getPeriodsSet();

    // Load each month file lazily; missing periods are skipped.
    for (const [y, m] of months) {
      const period = `${y}-${String(m).padStart(2, "0")}`;
      if (!knownPeriods.has(period)) continue;
      let doc;
      try { doc = await getMonthFile(period); } catch (_) { continue; }
      const plazaIndex = _buildPlazaIndex(doc);
      let monthHasData = false;
      for (const p of plazas) {
        const rec = plazaIndex.get(p);
        if (!rec) continue;
        const cats = _extractCategoriesForPlaza(rec);
        if (cats.length) {
          plazasSet.add(p);
          monthHasData = true;
          for (const c of cats) {
            const slot = catMap.get(c.name) || { count: 0, amount: 0 };
            slot.count += c.count;
            slot.amount += c.amount;
            catMap.set(c.name, slot);
          }
        } else if (rec.total && (rec.total.count || rec.total.amount)) {
          plazasSet.add(p);
          monthHasData = true;
          extraCount  += rec.total.count  || 0;
          extraAmount += rec.total.amount || 0;
        }
      }
      if (monthHasData) {
        monthsIncluded.push({
          year: y, month: m,
          label: `${MONTH_NAMES[m].slice(0, 3)} ${y}`,
        });
      }
    }
    if (!catMap.size && extraCount === 0 && extraAmount === 0) {
      const err = new Error("No data found for the selected filters in the range.");
      err.status = 404; throw err;
    }
    const daysCount = monthsIncluded.reduce(
      (acc, mi) => acc + daysInMonth(mi.year, mi.month), 0
    );
    const rec = _enrich(catMap, plazas.filter(p => plazasSet.has(p)), null, null, {
      daysCount: Math.max(daysCount, 1),
      monthsIncluded,
      extraCount,
      extraAmount,
      period: {
        start: { year: start_year, month: start_month },
        end:   { year: end_year,   month: end_month },
      },
    });
    rec.scope = {
      label:   _scopeLabel(filters),
      spv:     spv     || null,
      round:   round   || null,
      project: project || null,
      plaza:   plaza   || null,
      plaza_count: plazas.length,
    };
    return { record: rec };
  }

  // ── Public: monthly trend (count + amount per period) ─────────────────────
  async function aggregateTrend({ spv = "", round = "", project = "", plaza = "" }) {
    const filters = { spv, round, project, plaza };
    const { plazas } = await _resolvePlazaScope(filters);
    if (!plazas.length) return { trend: [], scope: _scopeLabel(filters) };

    const idx = await getIndex();
    const trend = [];
    for (const period of idx.periods || []) {
      const [y, m] = period.split("-").map(Number);
      let doc;
      try { doc = await getMonthFile(period); } catch (_) { continue; }
      const plazaIndex = _buildPlazaIndex(doc);
      let count = 0, amount = 0, hasData = false;
      for (const p of plazas) {
        const rec = plazaIndex.get(p);
        if (!rec) continue;
        if (rec.vehicles) {
          for (const v of Object.values(rec.vehicles)) {
            count += v.count || 0;
            amount += v.amount || 0;
          }
          hasData = true;
        } else if (rec.total && (rec.total.count || rec.total.amount)) {
          // FY-summary plaza: totals only, no per-class breakdown.
          count += rec.total.count || 0;
          amount += rec.total.amount || 0;
          hasData = true;
        }
      }
      if (hasData && (count > 0 || amount > 0)) {
        trend.push({
          year: y, month: m,
          month_name: MONTH_NAMES[m] || "",
          label: `${MONTH_NAMES[m].slice(0, 3)} ${y}`,
          count,
          amount: Math.round(amount * 100) / 100,
        });
      }
    }
    trend.sort((a, b) => (a.year - b.year) || (a.month - b.month));
    return { trend, scope: _scopeLabel(filters) };
  }

  // Expose globally for app.js (no module bundler in this project).
  window.DataLayer = {
    getMeta,
    getTaxonomy,
    aggregate,
    aggregateRange,
    aggregateTrend,
    // exposed for diagnostics
    _internals: { fetchJson, getIndex, getMonthFile },
  };
})();
