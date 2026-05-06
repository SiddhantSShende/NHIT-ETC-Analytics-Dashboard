/* filters.js — selector/dropdown wiring.
 *
 * Cascading flow: SPV → Round → Plaza, plus single-month vs date-range
 * mode toggle. Reads TAXONOMY_ROWS / META populated by app.js bootstrap.
 */

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

function resetSelect(id, values, prevValue, allLabel) {
  const sel = document.getElementById(id);
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    values.map(v => `<option value="${escapeAttr(v)}">${escapeHtml(v)}</option>`).join("");
  // Preserve previous selection if still valid.
  if (values.includes(prevValue)) sel.value = prevValue;
}

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
      .filter(r => (!spv     || r.spv     === spv)
                && (!newRound || r.round   === newRound))
      .map(r => r.excel_plaza)
  );
  resetSelect("plazaSel", plazasForScope, document.getElementById("plazaSel").value, "All Plazas");
}

function toggleRangeMode() {
  const useRange = document.getElementById("rangeToggle").checked;
  document.querySelectorAll(".single-only").forEach(el => {
    el.classList.toggle("hidden", useRange);
  });
  document.querySelectorAll(".range-only").forEach(el => {
    el.classList.toggle("hidden", !useRange);
  });
}
