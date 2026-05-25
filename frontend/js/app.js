/* app.js — bootstrap + analytics-load orchestration.
 *
 * Hierarchical filter flow:
 *   SPV → Round → Plaza → Year + Month (or date range)
 *
 * Each filter has an "All …" option. Leaving a level blank means
 * "include everything below it"; window.DataLayer (data-layer.js) sums
 * across every plaza that matches by reading the static JSON files in
 * downloads/json/ and caching them in the browser.
 */

// Taxonomy held in memory so cascading dropdowns don't need round-trips.
let TAXONOMY_ROWS = [];
let META = { plazas: [], years: [], months: [] };

window.addEventListener("DOMContentLoaded", () => {
  bootstrap();
  document.getElementById("btnView").addEventListener("click", loadData);
  document.getElementById("rangeToggle").addEventListener("change", toggleRangeMode);

  ["spvSel", "roundSel"].forEach(id => {
    document.getElementById(id).addEventListener("change", refreshCascade);
  });
});

// ── BOOTSTRAP ─────────────────────────────────────────────────────────────
async function bootstrap() {
  try {
    const [meta, taxonomy] = await Promise.all([
      DataLayer.getMeta(),
      DataLayer.getTaxonomy(),
    ]);
    META = meta;
    TAXONOMY_ROWS = taxonomy.rows || [];

    // Year + Month — populated once.
    fillSelect("yearSel",       meta.years  || [], y => ({ v: y, t: y }),         "-- Year --");
    fillSelect("monthSel",      meta.months || [], m => ({ v: m.num, t: m.name }), "-- Month --");
    fillSelect("startYearSel",  meta.years  || [], y => ({ v: y, t: y }),         "-- Year --");
    fillSelect("startMonthSel", meta.months || [], m => ({ v: m.num, t: m.name }), "-- Month --");
    fillSelect("endYearSel",    meta.years  || [], y => ({ v: y, t: y }),         "-- Year --");
    fillSelect("endMonthSel",   meta.months || [], m => ({ v: m.num, t: m.name }), "-- Month --");

    // SPV first — once chosen it filters the rest. We seed the others empty
    // and let `refreshCascade` populate them based on current selection.
    fillSelect("spvSel", taxonomy.spvs || [], v => ({ v, t: v }), "All SPVs");
    refreshCascade();
    toggleRangeMode();

    const sub = document.getElementById("hintSub");
    sub.textContent =
      `${TAXONOMY_ROWS.length} plazas across ${(taxonomy.spvs || []).length} SPVs · ` +
      `${(meta.years || []).length} year(s) · ${(meta.months || []).length} month(s) of data ready.`;
  } catch (e) {
    console.error(e);
    document.getElementById("hintSub").textContent =
      "Cannot load data files from downloads/json/. Re-run scripts/build_json_export.py.";
  }
}

// ── ANALYTICS LOAD ────────────────────────────────────────────────────────
async function loadData() {
  const spv    = document.getElementById("spvSel").value;
  const round_ = document.getElementById("roundSel").value;
  const plaza  = document.getElementById("plazaSel").value;
  const useRange = document.getElementById("rangeToggle").checked;
  const year   = parseInt(document.getElementById("yearSel").value);
  const month  = parseInt(document.getElementById("monthSel").value);
  const startYear  = parseInt(document.getElementById("startYearSel").value);
  const startMonth = parseInt(document.getElementById("startMonthSel").value);
  const endYear    = parseInt(document.getElementById("endYearSel").value);
  const endMonth   = parseInt(document.getElementById("endMonthSel").value);

  if (!useRange && (!year || !month)) {
    alert("Please select a Year and Month.");
    return;
  }
  if (useRange && (!startYear || !startMonth || !endYear || !endMonth)) {
    alert("Please select a Start and End date.");
    return;
  }

  showOnly("loadingState");

  const filterArgs = { spv, round: round_, plaza };
  const opts = useRange
    ? { mode: "range", startYear, startMonth, endYear, endMonth }
    : { mode: "single", year, month };

  try {
    const dataPromise = useRange
      ? DataLayer.aggregateRange({
          ...filterArgs,
          start_year: startYear, start_month: startMonth,
          end_year:   endYear,   end_month:   endMonth,
        })
      : DataLayer.aggregate({ ...filterArgs, year, month });

    const [dataJson, trendJson] = await Promise.all([
      dataPromise.catch(err => ({ __error: err })),
      DataLayer.aggregateTrend(filterArgs).catch(() => ({ trend: [] })),
    ]);

    if (dataJson.__error) {
      document.getElementById("errorTitle").textContent = "No Data Found";
      document.getElementById("errorTxt").textContent = useRange
        ? `No transaction data matched the selected filters in the chosen range.`
        : `No transaction data matched the selected filters in ${month}/${year}.`;
      showOnly("noDataState");
      return;
    }
    if (!dataJson.record || !dataJson.record.categories?.length) {
      document.getElementById("errorTitle").textContent = "No Data Found";
      document.getElementById("errorTxt").textContent = useRange
        ? `No transaction data matched the selected filters in the chosen range.`
        : `No transaction data matched the selected filters in ${month}/${year}.`;
      showOnly("noDataState");
      return;
    }

    showOnly("dashboard");
    try {
      renderResult(
        dataJson.record,
        trendJson.trend || [],
        opts,
        { prevMonth: dataJson.prev_month || null, prevYear: dataJson.prev_year || null },
      );
    } catch (renderErr) {
      console.error("Render failed:", renderErr);
      document.getElementById("errorTitle").textContent = "Render Error";
      document.getElementById("errorTxt").textContent =
        renderErr.message || "Failed to render the dashboard.";
      showOnly("noDataState");
    }
  } catch (e) {
    console.error(e);
    document.getElementById("errorTitle").textContent = "Data Error";
    document.getElementById("errorTxt").textContent =
      e.message || "Could not load data files from downloads/json/.";
    showOnly("noDataState");
  }
}
