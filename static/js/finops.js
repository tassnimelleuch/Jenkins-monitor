let chartInstance = null;

function populateMonthSelector() {
  const select = document.getElementById('monthSelector');
  if (!select) return;
  const now = new Date();

  for (let i = 0; i < 12; i += 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const year = d.getFullYear();
    const month = d.getMonth() + 1;

    const option = document.createElement('option');
    option.value = `${year}-${String(month).padStart(2, '0')}`;
    option.textContent = `${year}-${String(month).padStart(2, '0')}`;
    if (i === 0) option.selected = true;
    select.appendChild(option);
  }
}

function showError(msg) {
  const el = document.getElementById('finopsError');
  if (!el) return;
  if (!msg) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.textContent = msg;
  el.style.display = 'block';
}

function formatCurrency(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
}

function sumSeries(values) {
  if (!Array.isArray(values)) return 0;
  return values.reduce((acc, val) => acc + (Number(val) || 0), 0);
}

function parseCssColor(value, alpha) {
  if (!value) return `rgba(0,0,0,${alpha})`;
  const v = value.trim();
  if (v.startsWith('rgb')) {
    return v.replace(/rgba?\(([^)]+)\)/, (m, inner) => {
      const parts = inner.split(',').map((p) => p.trim());
      return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
    });
  }
  if (v.startsWith('#')) {
    const hex = v.replace('#', '');
    const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return v;
}

async function loadFinopsChart() {
  const ym = document.getElementById('monthSelector').value.split('-');
  const year = ym[0];
  const month = ym[1];
  const mode = document.getElementById('modeSelector').value;
  const only = document.getElementById('onlySelector').value;

  const baseUrl = document.body.dataset.finopsUrl || '/api/finops/daily-cost';
  const resp = await fetch(`${baseUrl}?year=${year}&month=${month}&mode=${mode}&only=${only}`);
  const data = await resp.json();

  if (!resp.ok || data.error) {
    showError(data.error || 'Could not load cost data from Azure.');
    return;
  }
  if (data.meta && data.meta.row_count === 0) {
    showError('No cost rows returned for this month. Try a previous month or verify Cost Management access.');
  } else if (data.meta && (!data.meta.date_col || !data.meta.type_col || !data.meta.cost_col)) {
    showError('Cost API returned unexpected columns. Check debug output.');
  } else {
    showError('');
  }

  const aksTotal = sumSeries(data.series.aks);
  const vmTotal = sumSeries(data.series.vm);

  setText('totalCost', formatCurrency(data.summary.total_cost));
  setText('aksTotal', formatCurrency(aksTotal));
  setText('vmTotal', formatCurrency(vmTotal));
  setText('avgDaily', formatCurrency(data.summary.average_daily_cost));
  setText(
    'highestDay',
    data.summary.highest_day
      ? `${data.summary.highest_day} (${formatCurrency(data.summary.highest_day_cost)})`
      : '-'
  );
  setText(
    'weekChange',
    data.summary.previous_week_change_pct === null
      ? '-'
      : `${data.summary.previous_week_change_pct}%`
  );

  const ctx = document.getElementById('dailyCostChart').getContext('2d');
  const rootStyle = getComputedStyle(document.documentElement);
  const aksColor = rootStyle.getPropertyValue('--green') || '#00dba0';
  const vmColor = rootStyle.getPropertyValue('--blue') || '#3ab8f8';

  if (chartInstance) {
    chartInstance.destroy();
  }

  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [
        {
          label: 'AKS',
          data: data.series.aks,
          stack: 'cost',
          backgroundColor: parseCssColor(aksColor, 0.6),
          borderColor: parseCssColor(aksColor, 0.9),
          borderWidth: 1
        },
        {
          label: 'VM',
          data: data.series.vm,
          stack: 'cost',
          backgroundColor: parseCssColor(vmColor, 0.6),
          borderColor: parseCssColor(vmColor, 0.9),
          borderWidth: 1
        }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            label: function(context) {
              const label = context.dataset.label || '';
              return `${label}: ${formatCurrency(context.parsed.y)}`;
            },
            footer: function(items) {
              const total = items.reduce((sum, item) => sum + item.parsed.y, 0);
              return `Total: ${formatCurrency(total)}`;
            }
          }
        }
      },
      scales: {
        x: { stacked: true },
        y: {
          stacked: true,
          beginAtZero: true,
          title: { display: true, text: 'Daily cost' },
          ticks: {
            callback: function(value) {
              return formatCurrency(value);
            }
          }
        }
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  populateMonthSelector();
  loadFinopsChart();

  document.getElementById('monthSelector').addEventListener('change', loadFinopsChart);
  document.getElementById('modeSelector').addEventListener('change', loadFinopsChart);
  document.getElementById('onlySelector').addEventListener('change', loadFinopsChart);
});
