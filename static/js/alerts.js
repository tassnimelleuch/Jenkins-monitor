function formatAlertDuration(ms) {
  const totalSeconds = Math.max(0, Math.round((ms || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatAlertTime(ts) {
  if (!ts) return '--';
  return new Date(ts).toLocaleString();
}

function formatCurrency(value, currencyCode = 'USD') {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: currencyCode || 'USD',
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function toggleHidden(id, hidden) {
  const el = document.getElementById(id);
  if (el) el.hidden = hidden;
}

function buildAlertMeta(alert) {
  if (alert.kind === 'finops_daily_cost') {
    return [
      `Day: ${alert.usage_date || '--'}`,
      `Current: ${formatCurrency(alert.current_value, alert.currency_code)}`,
      `Average: ${formatCurrency(alert.threshold_value, alert.currency_code)}`,
      `Over by: ${formatCurrency(alert.delta_value, alert.currency_code)}`,
      `Month: ${alert.month_label || '--'}`,
      `Detected: ${formatAlertTime(alert.last_detected_at)}`,
    ];
  }

  return [
    `Current: ${formatAlertDuration(alert.duration_ms)}`,
    `Threshold: ${formatAlertDuration(alert.threshold_ms)}`,
    `Over by: ${formatAlertDuration(alert.exceeded_by_ms)}`,
    `Started: ${formatAlertTime(alert.started_at)}`,
  ];
}

function renderAlertRows(alerts, canCheckAlerts) {
  const list = document.getElementById('alertsList');
  if (!list) return;

  list.innerHTML = alerts.map(alert => {
    const metaItems = buildAlertMeta(alert)
      .map(item => `<span>${item}</span>`)
      .join('');

    const checkAction = alert.requires_check && canCheckAlerts
      ? `
        <button
          class="alert-check-btn"
          type="button"
          data-alert-check-id="${alert.id}">
          Checked
        </button>
      `
      : '';

    return `
      <article class="alert-row alert-row-${alert.kind}">
        <div class="alert-row-main">
          <div class="alert-row-top">
            <span class="alert-source-tag">${alert.source_label || 'Alert'}</span>
            <span class="alert-build-tag">${alert.label || 'Alert'}</span>
            <span class="alert-severity">${alert.severity || 'warning'}</span>
          </div>
          <div class="alert-message">${alert.message || 'Alert triggered.'}</div>
          <div class="alert-meta">${metaItems}</div>
        </div>
        ${checkAction}
      </article>
    `;
  }).join('');
}

async function markAlertChecked(alertId) {
  const template = document.body.dataset.alertCheckUrlTemplate || '';
  if (!template || !alertId) return false;

  const url = template.replace(/\/0\/check$/, `/${alertId}/check`);
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    let message = 'Could not mark the alert as checked.';
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (error) {
      // Keep the fallback message.
    }
    throw new Error(message);
  }

  return true;
}

async function loadAlerts() {
  try {
    const url = document.body.dataset.alertsUrl;
    const canCheckAlerts = document.body.dataset.canCheckAlerts === 'true';
    const response = await fetch(url);
    const data = await response.json();
    const summary = data.summary || {};
    const alerts = data.alerts || [];
    const pipeline = data.pipeline || {};
    const alertCount = Number(summary.alert_count ?? alerts.length ?? 0);
    const finopsAlertCount = Number(summary.finops_alert_count ?? 0);
    const buildAlertCount = Number(summary.build_alert_count ?? 0);
    const thresholdMs = summary.threshold_ms || 0;

    setText(
      'alertsSubtitle',
      `Monitoring ${finopsAlertCount} pending total-cost FinOps alert${finopsAlertCount === 1 ? '' : 's'} and ${buildAlertCount} Jenkins alert${buildAlertCount === 1 ? '' : 's'}.`
    );
    setText(
      'alertsRuleText',
      `Total-cost FinOps alerts stay until an admin checks them. Jenkins alerts track running builds over ${formatAlertDuration(thresholdMs)} on ${pipeline.selected_branch || 'main'}.`
    );
    setText('alertsUpdatedAt', `Updated ${formatAlertTime(data.generated_at)}`);

    const pill = document.getElementById('alertsStatusPill');
    if (pill) {
      pill.hidden = alertCount === 0;
      pill.textContent = `${alertCount} active`;
      pill.classList.toggle('is-alert', alertCount > 0);
    }

    toggleHidden('alertsCard', alertCount === 0);
    toggleHidden('alertsIdleState', alertCount > 0);

    if (alertCount > 0) {
      renderAlertRows(alerts, canCheckAlerts);
    }
  } catch (e) {
    toggleHidden('alertsCard', true);
    toggleHidden('alertsIdleState', false);
    setText('alertsIdleState', 'Unable to load alerts right now.');
    const pill = document.getElementById('alertsStatusPill');
    if (pill) {
      pill.hidden = true;
      pill.textContent = 'Unavailable';
      pill.classList.remove('is-alert');
    }
  }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-alert-check-id]');
  if (!button) return;

  const alertId = button.dataset.alertCheckId;
  if (!alertId) return;

  button.disabled = true;
  button.textContent = 'Checking...';

  try {
    await markAlertChecked(alertId);
    await loadAlerts();
  } catch (error) {
    button.disabled = false;
    button.textContent = 'Checked';
  }
});

document.addEventListener('DOMContentLoaded', () => {
  loadAlerts();
  setInterval(loadAlerts, 10000);
});
