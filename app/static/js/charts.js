/* Dashboard charts.
 *
 * Two pages use this file: the personal landing page and the company reports
 * dashboard. Each renders a different set of keys into the JSON tag, so every
 * chart below is guarded on both its data and its canvas being present.
 *
 * Data comes from a JSON script tag rendered by the template rather than a
 * fetch, because it is already computed server-side and an extra round trip
 * would only add a loading state to manage.
 *
 * Colours are read from the live CSS variables so the charts follow the
 * theme, and everything re-styles on the themechange event rather than
 * needing a reload.
 */
(function () {
  "use strict";

  const el = document.getElementById("chart-data");
  if (!el || typeof Chart === "undefined") return;

  const DATA = JSON.parse(el.textContent);
  const charts = [];

  // Chosen to stay legible on both a white and a near-black background.
  const SERIES = {
    blue: "#4c8dff",
    teal: "#2ec4b6",
    amber: "#f5a524",
    red: "#e5484d",
    purple: "#8b7cf6",
    grey: "#8b93a1",
  };

  function css(name, fallback) {
    const v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }

  function palette() {
    return {
      text: css("--bs-body-color", "#212529"),
      muted: css("--bs-secondary-color", "#6c757d"),
      grid: css("--bs-border-color", "rgba(0,0,0,.1)"),
    };
  }

  function baseOptions(p) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { labels: { color: p.text, boxWidth: 12, boxHeight: 12 } },
        tooltip: { padding: 10, boxPadding: 4 },
      },
      scales: {
        x: { ticks: { color: p.muted }, grid: { color: p.grid } },
        y: { ticks: { color: p.muted }, grid: { color: p.grid }, beginAtZero: true },
      },
    };
  }

  function make(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const chart = new Chart(canvas, config);
    charts.push(chart);
    return chart;
  }

  function build() {
    const p = palette();
    Chart.defaults.color = p.muted;
    Chart.defaults.font.family =
      getComputedStyle(document.body).fontFamily || "system-ui";

    // --- My hours, last 14 working days -------------------------------------
    // Landing page only. Amber marks a day that was clocked in but never
    // clocked out, so the zero reads as a recording gap rather than a day off.
    if (DATA.recent_days) {
      const days = DATA.recent_days;
      make("chart-my-hours", {
        type: "bar",
        data: {
          labels: days.map((d) => d.label),
          datasets: [
            {
              label: "Hours",
              data: days.map((d) => d.hours),
              backgroundColor: days.map((d) =>
                d.incomplete ? SERIES.amber : SERIES.blue
              ),
              borderRadius: 3,
            },
          ],
        },
        options: Object.assign(baseOptions(p), {
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (i) =>
                  days[i.dataIndex].incomplete
                    ? " no clock-out recorded"
                    : ` ${i.parsed.y} hours`,
              },
            },
          },
        }),
      });
    }

    if (!DATA.months) return;

    // --- Attendance rate and hours over time --------------------------------
    const months = DATA.months;
    make("chart-attendance", {
      type: "line",
      data: {
        labels: months.map((m) => m.period),
        datasets: [
          {
            label: "Attendance rate (%)",
            data: months.map((m) => m.attendance_rate),
            borderColor: SERIES.blue,
            backgroundColor: SERIES.blue + "22",
            fill: true,
            tension: 0.3,
            yAxisID: "y",
          },
          {
            label: "Hours",
            data: months.map((m) => m.hours),
            borderColor: SERIES.teal,
            backgroundColor: "transparent",
            borderDash: [5, 4],
            tension: 0.3,
            yAxisID: "y1",
          },
        ],
      },
      options: Object.assign(baseOptions(p), {
        scales: {
          x: { ticks: { color: p.muted }, grid: { color: p.grid } },
          y: {
            position: "left",
            // Not zero-based: every month sits in the 90s, so a 0-100 axis
            // would render the variation as a flat line.
            min: 80,
            max: 100,
            ticks: { color: p.muted, callback: (v) => v + "%" },
            grid: { color: p.grid },
          },
          y1: {
            position: "right",
            beginAtZero: true,
            ticks: { color: p.muted },
            grid: { drawOnChartArea: false },
          },
        },
      }),
    });

    // --- Hours by department ------------------------------------------------
    const depts = DATA.departments;
    make("chart-departments", {
      type: "bar",
      data: {
        labels: depts.map((d) => d.department),
        datasets: [
          {
            label: "Hours (30 days)",
            data: depts.map((d) => d.hours),
            backgroundColor: SERIES.blue,
            borderRadius: 3,
          },
        ],
      },
      options: Object.assign(baseOptions(p), {
        indexAxis: "y",
        plugins: { legend: { display: false } },
      }),
    });

    // --- Project utilisation ------------------------------------------------
    const projects = DATA.projects;
    make("chart-projects", {
      type: "bar",
      data: {
        labels: projects.map((x) => x.code),
        datasets: [
          {
            label: "Hours (90 days)",
            data: projects.map((x) => x.hours),
            backgroundColor: projects.map((x) =>
              x.active ? SERIES.teal : SERIES.grey
            ),
            borderRadius: 3,
          },
        ],
      },
      options: Object.assign(baseOptions(p), {
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              // The code alone is cryptic; show the client on hover.
              afterTitle: (items) => projects[items[0].dataIndex].name,
              afterLabel: (item) =>
                projects[item.dataIndex].client +
                (projects[item.dataIndex].active ? "" : " (closed)"),
            },
          },
        },
      }),
    });

    // --- Leave mix ----------------------------------------------------------
    const leave = DATA.leave;
    make("chart-leave", {
      type: "doughnut",
      data: {
        labels: leave.map((l) => l.type),
        datasets: [
          {
            data: leave.map((l) => l.days),
            backgroundColor: [
              SERIES.blue,
              SERIES.amber,
              SERIES.purple,
              SERIES.teal,
              SERIES.red,
            ],
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: { position: "right", labels: { color: p.text, boxWidth: 12 } },
          tooltip: {
            callbacks: { label: (i) => ` ${i.label}: ${i.parsed} days` },
          },
        },
      },
    });
  }

  function restyle() {
    const p = palette();
    Chart.defaults.color = p.muted;

    charts.forEach(function (chart) {
      if (chart.options.plugins && chart.options.plugins.legend) {
        chart.options.plugins.legend.labels =
          chart.options.plugins.legend.labels || {};
        chart.options.plugins.legend.labels.color = p.text;
      }
      Object.values(chart.options.scales || {}).forEach(function (scale) {
        if (scale.ticks) scale.ticks.color = p.muted;
        if (scale.grid && scale.grid.drawOnChartArea !== false) {
          scale.grid.color = p.grid;
        }
      });
      chart.update("none");
    });
  }

  document.addEventListener("DOMContentLoaded", build);
  window.addEventListener("themechange", restyle);
})();
