let chartInstance = null;

function formatMonthLabel(year, month) {
  if (!year || !month) return 'previous month';
  const date = new Date(Number(year), Number(month) - 1, 1);
  if (Number.isNaN(date.getTime())) return 'previous month';
  return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

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

function formatSignedCurrency(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const numeric = Number(value);
  const sign = numeric > 0 ? '+' : numeric < 0 ? '-' : '';
  return `${sign}${formatCurrency(Math.abs(numeric))}`;
}

function formatSignedPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  const numeric = Number(value);
  const sign = numeric > 0 ? '+' : numeric < 0 ? '' : '';
  return `${sign}${numeric.toFixed(2)}%`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
}

function applyCostTrendClass(el, amount) {
  if (!el) return;
  el.classList.remove('is-increase', 'is-decrease', 'is-neutral');

  if (amount > 0) {
    el.classList.add('is-increase');
  } else if (amount < 0) {
    el.classList.add('is-decrease');
  } else {
    el.classList.add('is-neutral');
  }
}

function setDeltaText(id, change, previousMonthLabel, options = {}) {
  const el = document.getElementById(id);
  if (!el) return;

  const formatter = options.formatter || 'currency';
  const prefix = options.prefix || '';
  const suffix = options.suffix || '';
  const neutralText = options.neutralText || `No comparison available for ${previousMonthLabel}`;
  const amount = Number(change?.amount || 0);

  el.classList.remove('is-increase', 'is-decrease', 'is-neutral');

  if (!change || change.amount == null) {
    el.textContent = neutralText;
    el.classList.add('is-neutral');
    return;
  }

  const deltaValue = formatter === 'percent'
    ? formatSignedPercent(change.pct)
    : formatSignedCurrency(change.amount);

  el.textContent = `${prefix}${deltaValue} vs ${previousMonthLabel}${suffix}`;
  applyCostTrendClass(el, amount);
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

  const baseUrl = document.body.dataset.finopsUrl || '/api/finops/daily-cost';
  const params = new URLSearchParams({ year, month });
  const resp = await fetch(`${baseUrl}?${params.toString()}`);
  const data = await resp.json();

  if (!resp.ok || data.error) {
    showError(data.error || 'Could not load cost data from Azure.');
    return;
  }
  if (data.meta && (!data.meta.date_col || !data.meta.cost_col)) {
    showError('Cost API returned unexpected columns. Check debug output.');
  } else {
    showError('');
  }

  setText('totalCost', formatCurrency(data.summary.total_cost));
  setText('avgDaily', formatCurrency(data.summary.average_daily_cost));
  setText(
    'highestDay',
    data.summary.highest_day
      ? `${data.summary.highest_day} (${formatCurrency(data.summary.highest_day_cost)})`
      : '-'
  );

  const previousMonthLabel = formatMonthLabel(
    String(data.summary.previous_month_label || '').slice(0, 4),
    String(data.summary.previous_month_label || '').slice(5, 7)
  );
  const selectedMonthLabel = formatMonthLabel(data.year, data.month);
  const totalDelta = data.summary.delta?.total_cost;
  const totalSeries = Array.isArray(data.series?.total)
    ? data.series.total
    : [];

  if (totalSeries.length !== data.labels.length) {
    showError('Daily totals are unavailable. Try refreshing the FinOps cache.');
    return;
  }

  setDeltaText('totalCostDelta', totalDelta, previousMonthLabel);
  setDeltaText('avgDailyDelta', data.summary.delta?.average_daily_cost, previousMonthLabel);
  setDeltaText('highestDayDelta', data.summary.delta?.highest_day_cost, previousMonthLabel, { prefix: 'Peak ' });

  setText(
    'monthChange',
    totalDelta?.pct == null ? '-' : formatSignedPercent(totalDelta.pct)
  );
  applyCostTrendClass(document.getElementById('monthChange'), Number(totalDelta?.amount || 0));
  setDeltaText('monthChangeMeta', totalDelta, previousMonthLabel, { suffix: ' total' });

  setText('finopsChartTitle', 'Daily Total Cost');
  setText('finopsChartSub', `${selectedMonthLabel} compared with ${previousMonthLabel}`);

  const ctx = document.getElementById('dailyCostChart').getContext('2d');
  const rootStyle = getComputedStyle(document.documentElement);
  const totalColor = rootStyle.getPropertyValue('--blue') || '#3ab8f8';

  if (chartInstance) {
    chartInstance.destroy();
  }

  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [
        {
          label: 'Total',
          data: totalSeries,
          backgroundColor: parseCssColor(totalColor, 0.6),
          borderColor: parseCssColor(totalColor, 0.9),
          borderWidth: 1,
          borderRadius: 8,
          maxBarThickness: 36
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
              return `${context.dataset.label}: ${formatCurrency(context.parsed.y)}`;
            }
          }
        }
      },
      scales: {
        x: { stacked: false },
        y: {
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
});
