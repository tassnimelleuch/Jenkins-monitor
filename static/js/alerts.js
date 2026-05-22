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

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return `${Number(value).toFixed(1)}%`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeAlertKind(value) {
  const normalized = String(value || 'generic')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-');
  return normalized || 'generic';
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
  const items = [];

  if (alert.kind === 'finops_daily_cost') {
    items.push(
      `Current: ${formatCurrency(alert.current_value, alert.currency_code)}`,
      `Average: ${formatCurrency(alert.threshold_value, alert.currency_code)}`,
      `Over by: ${formatCurrency(alert.delta_value, alert.currency_code)}`,
      `Month: ${alert.month_label || '--'}`
    );
  } else if (alert.kind === 'open_pull_request_age') {
    items.push(
      `Title: ${alert.title || '--'}`,
      `Author: ${alert.author_login || '--'}`,
      `Open for: ${formatAlertDuration(alert.age_ms)}`
    );
  } else if (alert.kind === 'build_failure_streak') {
    const buildNumbers = Array.isArray(alert.build_numbers) && alert.build_numbers.length
      ? alert.build_numbers.map(number => `#${number}`).join(', ')
      : '--';

    items.push(
      `Recent failures: ${buildNumbers}`,
      `Streak started: ${alert.first_failed_build_number ? `#${alert.first_failed_build_number}` : '--'}`
    );
  } else if (alert.kind === 'stage_duration_over_average') {
    items.push(
      `Stage: ${alert.stage_name || '--'}`,
      `Running: ${formatAlertDuration(alert.duration_ms)}`,
      `Average: ${formatAlertDuration(alert.threshold_ms)}`,
      `Over by: ${formatAlertDuration(alert.exceeded_by_ms)}`
    );
  } else if (alert.kind === 'prometheus_metric_threshold') {
    items.push(
      `Current: ${formatPercent(alert.current_value)}`,
      `Over by: ${formatPercent(alert.delta_value)}`
    );
  } else {
    items.push(
      `Current: ${formatAlertDuration(alert.duration_ms)}`,
      `Threshold: ${formatAlertDuration(alert.threshold_ms)}`,
      `Over by: ${formatAlertDuration(alert.exceeded_by_ms)}`
    );
  }

  return items;
}

function buildAlertContext(alert) {
  if (alert.kind !== 'build_failure_streak') return '';

  const firstFailedBy = alert.first_failed_author_login
    ? `@${alert.first_failed_author_login}`
    : (alert.first_failed_author_name || '--');
  const firstFailedCommit = alert.first_failed_commit_sha || '--';

  return `
    <div class="alert-context-grid">
      <div class="alert-context-card">
        <div class="alert-context-label">First failed by</div>
        <div class="alert-context-value">${escapeHtml(firstFailedBy)}</div>
      </div>
      <div class="alert-context-card">
        <div class="alert-context-label">First failed commit</div>
        <div class="alert-context-value alert-context-value-mono">${escapeHtml(firstFailedCommit)}</div>
      </div>
    </div>
  `;
}

function buildAlertAction(alert, canManageAlerts) {
  if (!canManageAlerts) return '';

  if (!alert.requires_check) return '';

  return `
    <button
      class="alert-check-btn"
      type="button"
      data-alert-check-id="${escapeHtml(alert.id)}"
      aria-label="Mark alert checked"
      title="Mark alert checked">
      <svg class="alert-check-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path d="M3.5 8.5 6.5 11.5 12.5 5.5" />
      </svg>
    </button>
  `;
}

function renderAlertRows(listId, alerts, canManageAlerts) {
  const list = document.getElementById(listId);
  if (!list) return;

  if (!Array.isArray(alerts) || alerts.length === 0) {
    list.innerHTML = `
      <div class="alerts-empty">
        No open alerts right now.
      </div>
    `;
    return;
  }

  list.innerHTML = alerts.map(alert => {
    const kindClass = sanitizeAlertKind(alert.kind);
    const metaItems = buildAlertMeta(alert)
      .map(item => `<span>${escapeHtml(item)}</span>`)
      .join('');
    const context = buildAlertContext(alert);
    const action = buildAlertAction(alert, canManageAlerts);

    return `
      <article class="alert-row alert-row-${kindClass}">
        <div class="alert-row-main">
          <div class="alert-row-top">
            <span class="alert-source-tag">${escapeHtml(alert.source_label || 'Alert')}</span>
            <span class="alert-build-tag">${escapeHtml(alert.label || 'Alert')}</span>
          </div>
          <div class="alert-message">${escapeHtml(alert.message || 'Alert triggered.')}</div>
          ${context}
          <div class="alert-meta">${metaItems}</div>
        </div>
        ${action}
      </article>
    `;
  }).join('');
}

async function postAlertAction(template, alertId, fallbackMessage) {
  if (!template || !alertId) return false;

  const url = template.replace(/\/0\/check$/, `/${alertId}/check`);
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    let message = fallbackMessage;
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

async function markAlertChecked(alertId) {
  return postAlertAction(
    document.body.dataset.alertCheckUrlTemplate || '',
    alertId,
    'Could not mark the alert as checked.'
  );
}

async function loadAlerts() {
  try {
    const url = document.body.dataset.alertsUrl;
    const canManageAlerts = document.body.dataset.canCheckAlerts === 'true';
    const response = await fetch(url);
    const data = await response.json();
    const summary = data.summary || {};
    const alerts = data.alerts || [];
    const pipeline = data.pipeline || {};
    const alertCount = Number(summary.alert_count ?? alerts.length ?? 0);
    const finopsAlertCount = Number(summary.finops_alert_count ?? 0);
    const githubAlertCount = Number(summary.github_alert_count ?? 0);
    const prometheusAlertCount = Number(summary.prometheus_alert_count ?? 0);
    const jenkinsAlertCount = Number(summary.jenkins_alert_count ?? summary.build_alert_count ?? 0);

    setText(
      'alertsSubtitle',
      `${alertCount} open alert${alertCount === 1 ? '' : 's'}: ${finopsAlertCount} FinOps, ${jenkinsAlertCount} Jenkins, ${githubAlertCount} GitHub, ${prometheusAlertCount} Prometheus.`
    );
    setText(
      'alertsRuleText',
      'Open alerts from FinOps, Jenkins, GitHub, and Prometheus.'
    );

    const pill = document.getElementById('alertsStatusPill');
    if (pill) {
      pill.hidden = alertCount === 0;
      pill.textContent = `${alertCount} open`;
      pill.classList.toggle('is-alert', alertCount > 0);
    }

    toggleHidden('alertsCard', alertCount === 0);
    toggleHidden('alertsIdleState', alertCount > 0);

    renderAlertRows('alertsList', alerts, canManageAlerts);

    if (alertCount === 0) {
      setText('alertsIdleState', 'No open alerts right now.');
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
  const checkButton = event.target.closest('[data-alert-check-id]');
  if (checkButton) {
    const alertId = checkButton.dataset.alertCheckId;
    if (!alertId) return;

    checkButton.disabled = true;
    checkButton.setAttribute('aria-busy', 'true');

    try {
      await markAlertChecked(alertId);
      await loadAlerts();
    } catch (error) {
      checkButton.disabled = false;
      checkButton.removeAttribute('aria-busy');
    }
    return;
  }
});

document.addEventListener('DOMContentLoaded', () => {
  loadAlerts();
  setInterval(loadAlerts, 10000);
});
