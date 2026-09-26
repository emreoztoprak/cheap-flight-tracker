// Small progressive enhancements; every page also works without JavaScript.

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-toggle-secret]");
  if (toggle) {
    const input = toggle.parentElement.querySelector("input");
    input.type = input.type === "password" ? "text" : "password";
    toggle.textContent = input.type === "password" ? "Show" : "Hide";
  }
  const place = event.target.closest("[data-place]");
  if (place) {
    const input = document.querySelector(`input[name="${place.dataset.target}"]`);
    if (place.dataset.target === "to") {
      const parts = input.value.split(",").map((p) => p.trim()).filter(Boolean);
      parts.pop();
      parts.push(place.dataset.place);
      input.value = parts.join(", ") + ", ";
    } else {
      input.value = place.dataset.place;
    }
    place.parentElement.innerHTML = "";
    input.focus();
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const preset = event.target.closest("[data-cron]");
  if (preset) {
    const input = document.querySelector("[data-cron-input]");
    input.value = preset.dataset.cron;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
});

function syncForm(root) {
  root.querySelectorAll("form").forEach((form) => {
    const trip = form.querySelector("[data-trip]");
    form.querySelectorAll("[data-round-trip-only]").forEach((el) => {
      el.hidden = trip && trip.value !== "round-trip";
    });
    const mode = form.querySelector("[data-window]:checked");
    form.querySelectorAll("[data-window-next]").forEach((el) => { el.hidden = mode && mode.value !== "next"; });
    form.querySelectorAll("[data-window-range]").forEach((el) => { el.hidden = mode && mode.value !== "range"; });
  });
}
document.addEventListener("change", () => syncForm(document));

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function drawSparklines() {
  if (!window.Chart) return;
  document.querySelectorAll("canvas[data-spark]").forEach((canvas) => {
    const spark = JSON.parse(canvas.dataset.spark);
    new Chart(canvas, {
      type: "line",
      data: { labels: spark.labels, datasets: [{ data: spark.data, borderColor: cssVar("--accent"), borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false }] },
      options: { plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { display: false } }, animation: false, responsive: true, maintainAspectRatio: false },
    });
  });
}

let historyChart;
async function drawHistory() {
  const select = document.querySelector("[data-history-route]");
  const canvas = document.getElementById("history-chart");
  if (!select || !canvas || !window.Chart) return;
  const response = await fetch(`/history/data/${encodeURIComponent(select.value)}`);
  const data = await response.json();
  const palette = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626"];
  if (historyChart) historyChart.destroy();
  historyChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: data.labels,
      datasets: data.datasets.map((d, i) => ({ ...d, borderColor: palette[i % palette.length], backgroundColor: palette[i % palette.length], spanGaps: true, tension: 0.25, pointRadius: 2 })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y} ${data.currency}` } } },
      scales: { y: { title: { display: true, text: data.currency } } },
    },
  });
}

window.addEventListener("load", () => {
  syncForm(document);
  drawSparklines();
  drawHistory();
  const select = document.querySelector("[data-history-route]");
  if (select) select.addEventListener("change", drawHistory);
});
document.addEventListener("htmx:afterSwap", () => syncForm(document));
