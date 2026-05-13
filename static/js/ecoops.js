const ECOOPS_URL = document.body.dataset.ecoopsUrl || '';
const ECOOPS_REFRESH_MS = 30_000;

let ecoopsChart = null;

function ecoopsSetText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function formatWatts(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return `${Number(value).toFixed(1)} W`;
}

function formatGrams(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const numeric = Number(value);
  if (numeric >= 1000) return `${(numeric / 1000).toFixed(2)} kgCO2eq`;
  return `${numeric.toFixed(2)} gCO2eq`;
}

function formatRate(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return `${Number(value).toFixed(3)} gCO2eq/h`;
}

function showEcoopsError(message) {
  const el = document.getElementById('ecoopsError');
  if (!el) return;

  if (!message) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }

  el.textContent = message;
  el.style.display = 'block';
}

function updateStatusBadge(connected) {
  const badge = document.getElementById('ecoopsStatusBadge');
  if (!badge) return;

  badge.classList.remove('is-live', 'is-error');
  if (connected) {
    badge.textContent = 'Live';
    badge.classList.add('is-live');
  } else {
    badge.textContent = 'Unavailable';
    badge.classList.add('is-error');
  }
}

function renderEcoopsChart(series) {
  const canvas = document.getElementById('ecoopsCo2Chart');
  if (!canvas || !window.Chart) return;

  const labels = (series.timestamps || []).map(formatTimeLabel);
  const datasets = applyLineDefaults(
    [
      {
        label: 'AKS',
        data: series.aks_co2_hour_g || [],
        borderColor: '#00dba0',
        backgroundColor: 'rgba(0, 219, 160, 0.14)',
        fill: false
      },
      {
        label: 'VM',
        data: series.vm_co2_hour_g || [],
        borderColor: '#3ab8f8',
        backgroundColor: 'rgba(58, 184, 248, 0.14)',
        fill: false
      },
      {
        label: 'Combined',
        data: series.combined_co2_hour_g || [],
        borderColor: '#f5c542',
        backgroundColor: 'rgba(245, 197, 66, 0.16)',
        fill: true
      }
    ],
    { tension: 0.28 }
  );

  if (ecoopsChart) ecoopsChart.destroy();
  ecoopsChart = buildLineChart(canvas.getContext('2d'), labels, datasets, {
    unit: ' g/h',
    min: 0,
    maxTicksLimit: 8
  });
}

function updateSummary(summary) {
  const aks = summary?.aks || {};
  const vm = summary?.vm || {};
  const combined = summary?.combined || {};

  ecoopsSetText('aksCo2Hour', formatRate(aks.co2_hour_g));
  ecoopsSetText('vmCo2Hour', formatRate(vm.co2_hour_g));
  ecoopsSetText('combinedLastHour', formatGrams(combined.co2_last_hour_g));

  ecoopsSetText(
    'aksPowerMeta',
    `Wall power ${formatWatts(aks.wall_power_w)} | ${aks.vcpus ?? '-'} vCPUs`
  );
  ecoopsSetText(
    'vmPowerMeta',
    `Wall power ${formatWatts(vm.wall_power_w)} | ${vm.vcpus ?? '-'} vCPUs`
  );
  ecoopsSetText(
    'combinedPowerMeta',
    `Current combined power ${formatWatts(combined.wall_power_w)}`
  );

  ecoopsSetText('aksCo2Day', formatGrams(aks.co2_day_g));
  ecoopsSetText('vmCo2Day', formatGrams(vm.co2_day_g));
  ecoopsSetText('combinedCo2Month', formatGrams(combined.co2_month_g));

  ecoopsSetText(
    'aksUsageMeta',
    `CPU ${Number(aks.cpu_pct ?? 0).toFixed(1)}% | RAM ${Number(aks.ram_used_gb ?? 0).toFixed(2)} GB`
  );
  ecoopsSetText(
    'vmUsageMeta',
    `CPU ${Number(vm.cpu_pct ?? 0).toFixed(1)}% | RAM ${Number(vm.ram_used_gb ?? 0).toFixed(2)} GB`
  );
}

async function loadEcoopsDashboard() {
  if (!ECOOPS_URL) return;

  try {
    const res = await fetch(ECOOPS_URL, { headers: { Accept: 'application/json' } });
    const payload = await res.json().catch(() => ({}));

    if (!res.ok || !payload.connected) {
      updateStatusBadge(false);
      showEcoopsError(payload.error || 'EcoOps data is unavailable.');
      return;
    }

    updateStatusBadge(true);
    showEcoopsError('');
    updateSummary(payload.summary);
    renderEcoopsChart(payload.series || {});

    if (payload.refreshed_at) {
      ecoopsSetText(
        'ecoopsLastUpdated',
        `Last update: ${formatUserDateTime(payload.refreshed_at, { includeSeconds: false })}`
      );
    }
  } catch (error) {
    updateStatusBadge(false);
    showEcoopsError(`EcoOps request failed: ${error.message}`);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadEcoopsDashboard();
  if (ECOOPS_URL) {
    setInterval(loadEcoopsDashboard, ECOOPS_REFRESH_MS);
  }
});
