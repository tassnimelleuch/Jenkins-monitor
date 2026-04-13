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

// ── AKS metrics ─────────────────────────────────────────────────────────────
const CLUSTER_METRICS_URL = document.body.dataset.clusterMetricsUrl || '/jenkins/api/cluster-metrics';
let aksCpuNsChart = null;
let aksRamNsChart = null;
let aksNetNsChart = null;
let aksDiskNsChart = null;

function renderNamespaceSeriesChart(canvasId, seriesMap, chartRef, palette, opts = {}) {
  const ctx = document.getElementById(canvasId)?.getContext('2d');
  if (!ctx) return chartRef;

  const entries = Object.entries(seriesMap || {}).filter(([, v]) => Array.isArray(v) && v.length);
  if (!entries.length) {
    const wrap = document.getElementById(canvasId)?.parentElement;
    if (wrap && !wrap.querySelector('.chart-empty')) {
      const empty = document.createElement('div');
      empty.className = 'chart-empty';
      empty.textContent = 'No namespace metrics available';
      wrap.appendChild(empty);
    }
    return chartRef;
  }
  const wrap = document.getElementById(canvasId)?.parentElement;
  const existing = wrap?.querySelector('.chart-empty');
  if (existing) existing.remove();

  const [, firstSeries] = entries[0];
  const labels = firstSeries.map(([ts]) => formatTimeLabel(ts));

  const datasets = entries.slice(0, 8).map(([ns, points], i) => {
    const color = palette[i % palette.length];
    return {
      label: ns,
      data: points.map(([, v]) => parseFloat(v.toFixed(2))),
      borderColor: color,
      backgroundColor: color + '22',
      fill: false
    };
  });

  if (chartRef) chartRef.destroy();
  const unit = opts.unit || '%';
  const max = opts.max ?? null;
  const styledDatasets = applyLineDefaults(datasets, { tension: 0.25 });
  return buildLineChart(ctx, labels, styledDatasets, {
    unit,
    min: 0,
    max,
    maxTicksLimit: 10
  });
}

async function loadClusterMetrics() {
  try {
    const res = await fetch(CLUSTER_METRICS_URL);
    const d = await res.json();
    if (!d.connected) return;

    if (window.Chart) {
      const palette = [
        '#5cb85c', '#3ab8f8', '#ff9f43', '#ff4560',
        '#7c6fff', '#00dba0', '#f5c542', '#a855f7'
      ];
      if (d.namespace_cpu_history) {
        aksCpuNsChart = renderNamespaceSeriesChart(
          'aksCpuNsChart',
          d.namespace_cpu_history,
          aksCpuNsChart,
          palette,
          { unit: '%', max: 100 }
        );
        const badge = document.getElementById('nsCpuBadge');
        const avgCpu = avgFromSeriesMap(d.namespace_cpu_history, 1);
        if (badge) badge.textContent = avgCpu ? `Avg ${avgCpu}%` : 'Avg —%';
      }
      if (d.namespace_ram_history) {
        aksRamNsChart = renderNamespaceSeriesChart(
          'aksRamNsChart',
          d.namespace_ram_history,
          aksRamNsChart,
          palette,
          { unit: 'GB', max: null }
        );
        const badge = document.getElementById('nsRamBadge');
        const avgRam = avgFromSeriesMap(d.namespace_ram_history, 2);
        if (badge) badge.textContent = avgRam ? `Avg ${avgRam} GB` : 'Avg — GB';
      }
      if (d.namespace_net_history) {
        aksNetNsChart = renderNamespaceSeriesChart(
          'aksNetNsChart',
          d.namespace_net_history,
          aksNetNsChart,
          palette,
          { unit: ' MB/s', max: null }
        );
        const badge = document.getElementById('nsNetBadge');
        const avgNet = avgFromSeriesMap(d.namespace_net_history, 2);
        if (badge) badge.textContent = avgNet ? `Avg ${avgNet} MB/s` : 'Avg — MB/s';
      }
      if (d.namespace_disk_history) {
        aksDiskNsChart = renderNamespaceSeriesChart(
          'aksDiskNsChart',
          d.namespace_disk_history,
          aksDiskNsChart,
          palette,
          { unit: 'GB', max: null }
        );
        const badge = document.getElementById('nsDiskBadge');
        const avgDisk = avgFromSeriesMap(d.namespace_disk_history, 2);
        if (badge) badge.textContent = avgDisk ? `Avg ${avgDisk} GB` : 'Avg — GB';
      }
    }
  } catch (e) {
    console.warn('Cluster metrics fetch failed', e);
  }
}

function renderDeploymentFrequencyChart(freq) {
  const successful = freq?.successful ?? 0;
  const total = freq?.total ?? 0;
  const other = Math.max(total - successful, 0);

  const badge = document.getElementById('deployFreqBadge');
  if (badge) badge.textContent = `${successful} / ${total}`;

  renderDoughnutChart(
    'deployFreq',
    'deployFreqChart',
    ['Successful Deployments', 'Other Builds'],
    [successful, other],
    [getCssVar('--green'), getCssVar('--border')]
  );
}

function formatTimestamp(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '--';
  return d.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function formatSizeMB(sizeMb) {
  if (sizeMb == null || Number.isNaN(Number(sizeMb))) return '--';
  return `${Number(sizeMb).toFixed(1)} MB`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '--';
}

function renderLatestImageArtifact(latestImage) {
  const badge = document.getElementById('latestImageBadge');
  const resultEl = document.getElementById('latestImageResult');

  if (!latestImage || Object.keys(latestImage).length === 0) {
    setText('latestImageBuild', '--');
    setText('latestImageName', '--');
    setText('latestImageTag', '--');
    setText('latestImageSize', '--');
    setText('latestImageResult', '--');
    setText('latestImageTimestamp', '--');
    if (badge) badge.textContent = 'Unavailable';
    if (resultEl) {
      resultEl.classList.remove('success-text', 'fail-text', 'neutral-text');
    }
    return;
  }

  const buildNumber = latestImage.build_number ?? '--';
  const imageName = latestImage.image_name || '--';
  const tag = latestImage.tag || '--';
  const sizeMb = latestImage.size_mb;
  const result = latestImage.result || '--';
  const timestamp = latestImage.timestamp || null;

  setText('latestImageBuild', `#${buildNumber}`);
  setText('latestImageName', imageName);
  setText('latestImageTag', tag);
  setText('latestImageSize', formatSizeMB(sizeMb));
  setText('latestImageResult', result);
  setText('latestImageTimestamp', formatTimestamp(timestamp));

  if (badge) {
    badge.textContent = tag !== '--' ? tag : 'Latest Build';
  }

  if (resultEl) {
    resultEl.classList.remove('success-text', 'fail-text', 'neutral-text');
    if (result === 'SUCCESS') resultEl.classList.add('success-text');
    else if (result === 'FAILURE') resultEl.classList.add('fail-text');
    else resultEl.classList.add('neutral-text');
  }
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
    renderDeploymentFrequencyChart(data.deployment_frequency || {});
    renderLatestImageArtifact(data.latest_image || {});

  } catch (e) {
    console.error('Deployment KPIs error:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadDeploymentKpis();
  loadClusterMetrics();
  setInterval(loadClusterMetrics, 30000);
});
