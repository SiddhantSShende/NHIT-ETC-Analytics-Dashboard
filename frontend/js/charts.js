/* charts.js — Chart.js wrappers for the doughnut, bar and trend charts.
 *
 * Restyled for the dark-glass theme: light text on the axes, soft white grid
 * lines, glassy tooltips, and gradient bar fills with rounded corners. */

// Shared colour tokens used by Chart.js options. Tuned for the light theme.
const CHART_TEXT  = "#0f172a";
const CHART_MUTED = "#64748b";
const CHART_GRID  = "rgba(15, 23, 42, 0.06)";
const CHART_BORDER = "rgba(15, 23, 42, 0.10)";

// Build a vertical gradient from one colour for bar fills.
function vGradient(ctx, area, top, bottom) {
  if (!area) return top;
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  return g;
}

const baseTooltip = {
  backgroundColor: "rgba(15, 23, 42, 0.92)",
  titleColor: "#fff",
  bodyColor: "#e2e8f0",
  borderColor: "rgba(255, 255, 255, 0.10)",
  borderWidth: 1,
  padding: 12,
  cornerRadius: 10,
  titleFont: { weight: "600", size: 13 },
  bodyFont: { size: 12 },
  displayColors: true,
  boxPadding: 4,
};

function axisOpts(titleText, tickFn) {
  return {
    type: "linear",
    beginAtZero: true,
    title: {
      display: true,
      text: titleText,
      color: CHART_MUTED,
      font: { size: 11, weight: "600" },
    },
    ticks: { color: CHART_MUTED, callback: tickFn, font: { size: 11 } },
    grid: { color: CHART_GRID, borderColor: CHART_BORDER, drawTicks: false },
    border: { color: CHART_BORDER },
  };
}

// Show a centered "—" placeholder when a chart has no data (e.g. FY
// 2023-24 months that lack per-category breakdown). Returns true if the
// placeholder was rendered, in which case the caller should skip Chart.js.
function _renderEmptyChartPlaceholder(canvasId, label) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return false;
  const ctx = canvas.getContext("2d");
  const w = canvas.width || canvas.clientWidth || 200;
  const h = canvas.height || canvas.clientHeight || 200;
  ctx.clearRect(0, 0, w, h);
  ctx.save();
  ctx.fillStyle = CHART_MUTED;
  ctx.font = "italic 13px system-ui, -apple-system, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("—", w / 2, h / 2 - 10);
  ctx.font = "12px system-ui, -apple-system, sans-serif";
  ctx.fillText(label || "No data", w / 2, h / 2 + 14);
  ctx.restore();
  return true;
}

function drawPie(canvasId, labels, data, chartKey, isMoney) {
  destroy(chartKey);
  if (!data || !data.length || data.every(v => !v)) {
    _renderEmptyChartPlaceholder(canvasId, "No breakdown available");
    return;
  }
  charts[chartKey] = new Chart(document.getElementById(canvasId), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: PIE_COLORS.slice(0, labels.length),
        borderColor: "rgba(255, 255, 255, 0.95)",
        borderWidth: 3,
        hoverOffset: 12,
        hoverBorderColor: "rgba(255, 255, 255, 1)",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: "62%",
      plugins: {
        legend: { display: false },
        tooltip: {
          ...baseTooltip,
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
  if (!cats || !cats.length) {
    _renderEmptyChartPlaceholder("barChart", "No per-category breakdown for this period");
    return;
  }
  charts.barChart = new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: {
      labels: cats.map(c => shortCat(c.name)),
      datasets: [
        {
          label: "Transactions",
          data: cats.map(c => c.count),
          backgroundColor: ctx => vGradient(ctx.chart.ctx, ctx.chart.chartArea, "#4d8bff", "#003087"),
          borderColor: "rgba(0, 48, 135, 0.5)",
          borderWidth: 0,
          yAxisID: "y",
          borderRadius: 8,
          maxBarThickness: 44,
        },
        {
          label: "Revenue (₹)",
          data: cats.map(c => c.amount),
          backgroundColor: ctx => vGradient(ctx.chart.ctx, ctx.chart.chartArea, "#ffa552", "#f47920"),
          borderColor: "rgba(244, 121, 32, 0.5)",
          borderWidth: 0,
          yAxisID: "y1",
          borderRadius: 8,
          maxBarThickness: 44,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: CHART_TEXT, usePointStyle: true, pointStyle: "circle", padding: 18, font: { size: 12 } },
        },
        tooltip: {
          ...baseTooltip,
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
        x: {
          ticks: { color: CHART_MUTED, font: { size: 11 } },
          grid: { display: false, borderColor: CHART_BORDER },
          border: { color: CHART_BORDER },
        },
        y:  { ...axisOpts("Transactions", v => fmtInt(v)), position: "left" },
        y1: { ...axisOpts("Revenue (₹)", v => fmtAmt(v)), position: "right",
              grid: { drawOnChartArea: false, borderColor: CHART_BORDER } },
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
  if (Number.isInteger(selYear) && Number.isInteger(selMonth)) {
    sub.textContent = `Transactions and revenue across ${trend.length} month(s). Selected month is highlighted.`;
  } else {
    sub.textContent = `Transactions and revenue across ${trend.length} month(s).`;
  }

  const labels = trend.map(t => t.label);
  const counts = trend.map(t => t.count);
  const amts   = trend.map(t => t.amount);

  // Per-bar gradient — selected month uses the orange accent, others blue.
  const barColor = ctx => {
    const i = ctx.dataIndex;
    const t = trend[i];
    const area = ctx.chart.chartArea;
    const isSelected = Number.isInteger(selYear) && Number.isInteger(selMonth)
      && t.year === selYear && t.month === selMonth;
    if (isSelected) {
      return vGradient(ctx.chart.ctx, area, "#ffa552", "#f47920");
    }
    return vGradient(ctx.chart.ctx, area, "#4d8bff", "#003087");
  };

  charts.trendChart = new Chart(document.getElementById("trendChart"), {
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Transactions", data: counts,
          backgroundColor: barColor,
          yAxisID: "y", borderRadius: 8, order: 2, maxBarThickness: 36 },
        { type: "line", label: "Revenue (₹)", data: amts,
          borderColor: "#7ab648",
          backgroundColor: ctx => {
            const area = ctx.chart.chartArea;
            if (!area) return "rgba(122, 182, 72, 0.18)";
            const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
            g.addColorStop(0, "rgba(122, 182, 72, 0.35)");
            g.addColorStop(1, "rgba(122, 182, 72, 0.00)");
            return g;
          },
          fill: true,
          yAxisID: "y1",
          tension: 0.35,
          pointRadius: 4,
          pointBackgroundColor: "#7ab648",
          pointBorderColor: "#fff",
          pointBorderWidth: 2,
          pointHoverRadius: 6,
          borderWidth: 2.5,
          order: 1 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: CHART_TEXT, usePointStyle: true, pointStyle: "circle", padding: 18, font: { size: 12 } },
        },
        tooltip: {
          ...baseTooltip,
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
        x: {
          ticks: { color: CHART_MUTED, font: { size: 11 } },
          grid: { display: false, borderColor: CHART_BORDER },
          border: { color: CHART_BORDER },
        },
        y:  { ...axisOpts("Transactions", v => fmtInt(v)), position: "left" },
        y1: { ...axisOpts("Revenue (₹)", v => fmtAmt(v)), position: "right",
              grid: { drawOnChartArea: false, borderColor: CHART_BORDER } },
      },
    },
  });
}

// Set Chart.js global defaults so any chart renders with the right contrast
// and a polished load-in animation.
if (typeof Chart !== "undefined") {
  Chart.defaults.color = CHART_TEXT;
  Chart.defaults.font.family = "'Inter', system-ui, -apple-system, sans-serif";
  Chart.defaults.borderColor = CHART_BORDER;
  // Honour reduced-motion: skip animations entirely.
  const reduced = typeof window !== "undefined"
    && window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  Chart.defaults.animation = reduced ? false : {
    duration: 850,
    easing: "easeOutQuart",
  };
  Chart.defaults.animations = reduced ? {} : {
    // Bars grow up from the baseline rather than fading in horizontally.
    y:           { duration: 850, easing: "easeOutCubic", from: ctx => ctx.chart.scales?.y?.getPixelForValue(0) ?? undefined },
    numbers:     { duration: 850, easing: "easeOutCubic" },
  };
  Chart.defaults.transitions = {
    active: { animation: { duration: 220 } },
  };
}
