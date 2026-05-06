/* charts.js — Chart.js wrappers for the doughnut, bar and trend charts. */

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
  if (Number.isInteger(selYear) && Number.isInteger(selMonth)) {
    sub.textContent = `Transactions and revenue across ${trend.length} month(s). Selected month is highlighted.`;
  } else {
    sub.textContent = `Transactions and revenue across ${trend.length} month(s).`;
  }

  const labels = trend.map(t => t.label);
  const counts = trend.map(t => t.count);
  const amts   = trend.map(t => t.amount);
  const colors = trend.map(t => (
    Number.isInteger(selYear) && Number.isInteger(selMonth) && t.year === selYear && t.month === selMonth
      ? "#f47920"
      : "#003087"
  ));

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
