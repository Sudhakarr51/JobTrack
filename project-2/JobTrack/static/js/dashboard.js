// Small helper script: delete confirmations (all pages) and the status chart (dashboard).
(function () {
  "use strict";

  // Ask "Are you sure?" before any form that has a data-confirm message is sent.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  var canvas = document.getElementById("statusChart");
  var dataElement = document.getElementById("chart-data");
  if (!canvas || !dataElement) { return; }  // not on the dashboard

  // If Chart.js could not be downloaded, show a friendly message instead.
  if (typeof Chart === "undefined") {
    canvas.classList.add("d-none");
    document.getElementById("chartError").classList.remove("d-none");
    return;
  }

  var data = JSON.parse(dataElement.textContent);  // numbers calculated by Flask from the database
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: data.labels,
      datasets: [{
        data: data.values,
        backgroundColor: ["#94a3b8", "#2563eb", "#0891b2", "#d97706", "#16a34a", "#dc2626", "#334155"]
      }]
    },
    options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
  });
})();
