function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function toSeries(map, limit = 8, preferredOrder = []) {
  const order = new Map(preferredOrder.map((k, i) => [k, i]));
  const entries = Object.entries(map || {}).sort((a, b) => {
    const ao = order.has(a[0]) ? order.get(a[0]) : 999;
    const bo = order.has(b[0]) ? order.get(b[0]) : 999;
    if (ao !== bo) return ao - bo;
    if (b[1] !== a[1]) return b[1] - a[1];
    return a[0].localeCompare(b[0]);
  });
  return {
    labels: entries.slice(0, limit).map(([k]) => k),
    values: entries.slice(0, limit).map(([, v]) => v)
  };
}

function renderBarChart(key, canvasId, labels, values, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (!window._deployCharts) window._deployCharts = {};
  if (window._deployCharts[key]) {
    window._deployCharts[key].destroy();
    window._deployCharts[key] = null;
  }
  if (!labels.length) return;

  window._deployCharts[key] = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: color,
        borderRadius: 8,
        barThickness: 12
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: getCssVar('--text2'), font: { size: 10 } }
        },
        y: {
          grid: { color: getCssVar('--border') },
          ticks: { color: getCssVar('--text2'), font: { size: 10 }, stepSize: 1 }
        }
      }
    }
  });
}

function renderDoughnutChart(key, canvasId, labels, values, colors) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (!window._deployCharts) window._deployCharts = {};
  if (window._deployCharts[key]) {
    window._deployCharts[key].destroy();
    window._deployCharts[key] = null;
  }
  if (!labels.length) return;

  window._deployCharts[key] = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: getCssVar('--text2'), boxWidth: 10, boxHeight: 10 }
        }
      }
    }
  });
}

function renderDiskChart(key, canvasId, labels, values, colors) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (!window._deployCharts) window._deployCharts = {};
  if (window._deployCharts[key]) {
    window._deployCharts[key].destroy();
    window._deployCharts[key] = null;
  }
  if (!labels.length) return;

  window._deployCharts[key] = new Chart(canvas, {
    type: 'pie',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: getCssVar('--text2'), boxWidth: 10, boxHeight: 10 }
        }
      }
    }
  });
}

async function loadDeploymentKpis() {
  const url = document.body.dataset.deploymentKpisUrl;
  if (!url) return;

  try {
    const res = await fetch(url);
    const payload = await res.json().catch(() => ({}));
    const data = payload.data || {};

    const podsTotal = data.pods_total ?? '--';
    const rsTotal = data.replica_sets_total ?? '--';
    const pvcsTotal = data.pvcs_total ?? '--';

    const nsEl = document.getElementById('namespacesTotal');
    const podsEl = document.getElementById('podsTotal');
    const rsEl = document.getElementById('rsTotal');
    const pvcsEl = document.getElementById('pvcsTotal');
    if (nsEl) nsEl.textContent = Object.keys(data.pods_by_namespace || {}).length;
    if (podsEl) podsEl.textContent = podsTotal;
    if (rsEl) rsEl.textContent = rsTotal;
    if (pvcsEl) pvcsEl.textContent = pvcsTotal;

    const preferredNs = ['kube-system', 'default'];
    const podsNs = toSeries(data.pods_by_namespace, 8, preferredNs);
    const rsNs = toSeries(data.replica_sets_by_namespace, 8, preferredNs);
    const pvcsNs = toSeries(data.pvcs_by_namespace, 8, preferredNs);
    const podsPhase = toSeries(data.pods_by_phase, 8);

    const podsBadge = document.getElementById('podsNsBadge');
    const rsBadge = document.getElementById('rsNsBadge');
    const pvcsBadge = document.getElementById('pvcsNsBadge');
    const phaseBadge = document.getElementById('podsPhaseBadge');
    if (podsBadge) podsBadge.textContent = 'Total ' + (data.pods_total ?? 0);
    if (rsBadge) rsBadge.textContent = 'Total ' + (data.replica_sets_total ?? 0);
    if (pvcsBadge) pvcsBadge.textContent = 'Total ' + (data.pvcs_total ?? 0);
    if (phaseBadge) phaseBadge.textContent = 'Total ' + (data.pods_total ?? 0);

    renderBarChart('podsNs', 'podsNsChart', podsNs.labels, podsNs.values, getCssVar('--accent'));
    renderBarChart('rsNs', 'rsNsChart', rsNs.labels, rsNs.values, getCssVar('--blue'));
    const pvcColors = [
      getCssVar('--orange'),
      getCssVar('--yellow'),
      getCssVar('--red'),
      getCssVar('--blue'),
      getCssVar('--accent')
    ];
    renderDoughnutChart('pvcsNs', 'pvcsNsChart', pvcsNs.labels, pvcsNs.values, pvcColors);

    const phaseColors = [
      getCssVar('--green'),
      getCssVar('--yellow'),
      getCssVar('--red'),
      getCssVar('--blue'),
      getCssVar('--accent')
    ];
    renderDiskChart('podsPhase', 'podsPhaseChart', podsPhase.labels, podsPhase.values, phaseColors);

  } catch (e) {
    console.error('Deployment KPIs error:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadDeploymentKpis();
});
