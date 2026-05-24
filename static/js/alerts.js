const ALERT_DAY_MS = 24 * 60 * 60 * 1000;
const ALERT_FILTER_LABELS = {
  all: 'All',
  finops: 'FinOps',
  jenkins: 'Jenkins',
  prometheus: 'Prometheus',
  github: 'GitHub',
};
const ALERT_FILTER_KEYS = Object.keys(ALERT_FILTER_LABELS);

let _alertsPayload = null;
let _alertsFilter = 'all';
let _alertsStream = null;
let _alertsStreamReceived = false;
let _alertsStreamLoggedError = false;

function getAlertsUrl() {
  return document.body.dataset.alertsUrl || '';
}

function getAlertsStreamUrl() {
  return document.body.dataset.alertsStreamUrl || '';
}

function canUseAlertsLiveStream() {
  return typeof window.EventSource !== 'undefined' && Boolean(getAlertsStreamUrl());
}

function alertsLiveStreamActive() {
  return Boolean(_alertsStream);
}

function closeAlertsLiveStream() {
  if (_alertsStream) {
    _alertsStream.close();
    _alertsStream = null;
  }
}

function formatAlertDuration(ms) {
  const totalSeconds = Math.max(0, Math.round((ms || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatAlertAgeDays(ms) {
  const totalDays = Math.max(0, Number(ms || 0)) / ALERT_DAY_MS;
  if (totalDays <= 0) return '0 days';

  const roundedDays = totalDays >= 10
    ? Math.round(totalDays)
    : Math.round(totalDays * 10) / 10;
  const dayText = Number.isInteger(roundedDays)
    ? String(roundedDays)
    : roundedDays.toFixed(1).replace(/\.0$/, '');
  const dayValue = Number(dayText);
  return `${dayText} day${dayValue === 1 ? '' : 's'}`;
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
      `Base: ${alert.base_branch || '--'}`,
      `Open for: ${formatAlertAgeDays(alert.age_ms)}`
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
        No matching alerts right now.
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

function sourceSystemForAlert(alert) {
  const sourceSystem = String(alert?.source_system || '').trim().toLowerCase();
  if (sourceSystem) return sourceSystem;
  return String(alert?.source_label || '').trim().toLowerCase();
}

function buildAlertSourceCounts(alerts) {
  const counts = {
    all: Array.isArray(alerts) ? alerts.length : 0,
    finops: 0,
    jenkins: 0,
    prometheus: 0,
    github: 0,
  };

  (alerts || []).forEach(alert => {
    const sourceSystem = sourceSystemForAlert(alert);
    if (Object.prototype.hasOwnProperty.call(counts, sourceSystem)) {
      counts[sourceSystem] += 1;
    }
  });

  return counts;
}

function getAlertFilterLabel(filterKey) {
  return ALERT_FILTER_LABELS[filterKey] || ALERT_FILTER_LABELS.all;
}

function filterAlerts(alerts, filterKey) {
  if (filterKey === 'all') return Array.isArray(alerts) ? alerts : [];
  return (alerts || []).filter(alert => sourceSystemForAlert(alert) === filterKey);
}

function buildAlertsSubtitle(totalCount, counts, filteredCount) {
  if (totalCount === 0) {
    return 'No open alerts right now.';
  }

  if (_alertsFilter === 'all') {
    return `${totalCount} open alert${totalCount === 1 ? '' : 's'}: ${counts.finops} FinOps, ${counts.jenkins} Jenkins, ${counts.github} GitHub, ${counts.prometheus} Prometheus.`;
  }

  const filterLabel = getAlertFilterLabel(_alertsFilter);
  if (filteredCount === 0) {
    return `No open ${filterLabel} alerts right now. ${totalCount} alert${totalCount === 1 ? '' : 's'} still open in other sources.`;
  }

  return `Showing ${filteredCount} ${filterLabel} alert${filteredCount === 1 ? '' : 's'} out of ${totalCount} open alert${totalCount === 1 ? '' : 's'}.`;
}

function buildAlertsRuleText() {
  if (_alertsFilter === 'all') {
    return 'Open alerts from FinOps, Jenkins, GitHub, and Prometheus.';
  }
  return `Showing ${getAlertFilterLabel(_alertsFilter)} alerts only.`;
}

function buildAlertsEmptyState(totalCount) {
  if (totalCount === 0 || _alertsFilter === 'all') {
    return 'No open alerts right now.';
  }
  return `No open ${getAlertFilterLabel(_alertsFilter)} alerts right now.`;
}

function updateAlertStatusPill(totalCount, filteredCount) {
  const pill = document.getElementById('alertsStatusPill');
  if (!pill) return;

  if (totalCount === 0) {
    pill.hidden = true;
    pill.textContent = '0 open';
    pill.classList.remove('is-alert');
    return;
  }

  pill.hidden = false;
  if (_alertsFilter === 'all') {
    pill.textContent = `${totalCount} open`;
    pill.classList.toggle('is-alert', totalCount > 0);
    return;
  }

  pill.textContent = `${filteredCount} ${getAlertFilterLabel(_alertsFilter)}`;
  pill.classList.toggle('is-alert', filteredCount > 0);
}

function updateAlertFilterButtons(counts) {
  document.querySelectorAll('[data-alert-filter]').forEach(button => {
    const filterKey = String(button.dataset.alertFilter || 'all').trim().toLowerCase();
    const label = button.dataset.filterLabel || getAlertFilterLabel(filterKey);
    const count = Number(counts[filterKey] ?? 0);
    const isActive = filterKey === _alertsFilter;

    button.textContent = `${label} (${count})`;
    button.classList.toggle('is-active', isActive);
    button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

function renderAlertsFromState() {
  const payload = _alertsPayload || {};
  const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
  const counts = buildAlertSourceCounts(alerts);
  const filteredAlerts = filterAlerts(alerts, _alertsFilter);
  const totalCount = counts.all;
  const filteredCount = filteredAlerts.length;
  const canManageAlerts = document.body.dataset.canCheckAlerts === 'true';

  updateAlertFilterButtons(counts);
  setText('alertsSubtitle', buildAlertsSubtitle(totalCount, counts, filteredCount));
  setText('alertsRuleText', buildAlertsRuleText());
  updateAlertStatusPill(totalCount, filteredCount);

  toggleHidden('alertsCard', filteredCount === 0);
  toggleHidden('alertsIdleState', filteredCount > 0);
  renderAlertRows('alertsList', filteredAlerts, canManageAlerts);

  if (filteredCount === 0) {
    setText('alertsIdleState', buildAlertsEmptyState(totalCount));
  }
}

function applyAlertsPayload(data) {
  _alertsPayload = {
    ...data,
    alerts: Array.isArray(data?.alerts) ? data.alerts : [],
  };
  renderAlertsFromState();
}

function showAlertsUnavailableState() {
  if (_alertsPayload) return;

  toggleHidden('alertsCard', true);
  toggleHidden('alertsIdleState', false);
  setText('alertsSubtitle', 'Unable to load alerts right now.');
  setText('alertsIdleState', 'Unable to load alerts right now.');

  const pill = document.getElementById('alertsStatusPill');
  if (pill) {
    pill.hidden = true;
    pill.textContent = 'Unavailable';
    pill.classList.remove('is-alert');
  }
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

function applyOptimisticAlertCheck(alertId) {
  if (!_alertsPayload || !Array.isArray(_alertsPayload.alerts)) return;

  _alertsPayload = {
    ..._alertsPayload,
    alerts: _alertsPayload.alerts.filter(alert => String(alert?.id) !== String(alertId)),
  };
  renderAlertsFromState();
}

async function loadAlerts() {
  try {
    const response = await fetch(getAlertsUrl());
    if (!response.ok) {
      throw new Error('Could not load alerts.');
    }

    const data = await response.json();
    _alertsStreamReceived = true;
    applyAlertsPayload(data);
  } catch (error) {
    showAlertsUnavailableState();
  }
}

function connectAlertsLiveStream() {
  if (!canUseAlertsLiveStream() || alertsLiveStreamActive()) return false;

  _alertsStream = new EventSource(getAlertsStreamUrl());
  _alertsStream.addEventListener('open', () => {
    _alertsStreamReceived = true;
    _alertsStreamLoggedError = false;
  });
  _alertsStream.addEventListener('stream_ready', () => {
    _alertsStreamReceived = true;
    _alertsStreamLoggedError = false;
  });
  _alertsStream.addEventListener('heartbeat', () => {
    _alertsStreamReceived = true;
  });
  _alertsStream.addEventListener('alerts_payload', event => {
    _alertsStreamReceived = true;
    _alertsStreamLoggedError = false;

    try {
      applyAlertsPayload(JSON.parse(event.data));
    } catch (error) {
      console.error('Alerts SSE parse error:', error);
    }
  });
  _alertsStream.onerror = () => {
    if (!_alertsStreamLoggedError) {
      console.warn('Alerts SSE stream disconnected. The browser will retry automatically.');
      _alertsStreamLoggedError = true;
    }

    if (!_alertsStreamReceived) {
      void loadAlerts();
    }
  };

  return true;
}

function setAlertsFilter(filterKey) {
  const normalized = ALERT_FILTER_KEYS.includes(filterKey) ? filterKey : 'all';
  if (_alertsFilter === normalized && _alertsPayload) return;

  _alertsFilter = normalized;
  if (!_alertsPayload) {
    updateAlertFilterButtons({
      all: 0,
      finops: 0,
      jenkins: 0,
      prometheus: 0,
      github: 0,
    });
    setText('alertsRuleText', buildAlertsRuleText());
    return;
  }
  renderAlertsFromState();
}

document.addEventListener('click', async event => {
  const filterButton = event.target.closest('[data-alert-filter]');
  if (filterButton) {
    const filterKey = String(filterButton.dataset.alertFilter || 'all').trim().toLowerCase();
    setAlertsFilter(filterKey);
    return;
  }

  const checkButton = event.target.closest('[data-alert-check-id]');
  if (!checkButton) return;

  const alertId = checkButton.dataset.alertCheckId;
  if (!alertId) return;

  checkButton.disabled = true;
  checkButton.setAttribute('aria-busy', 'true');

  try {
    await markAlertChecked(alertId);
    applyOptimisticAlertCheck(alertId);
    await loadAlerts();
  } catch (error) {
    checkButton.disabled = false;
    checkButton.removeAttribute('aria-busy');
  }
});

document.addEventListener('DOMContentLoaded', () => {
  if (!connectAlertsLiveStream()) {
    void loadAlerts();
    return;
  }

  window.setTimeout(() => {
    if (!_alertsPayload) {
      void loadAlerts();
    }
  }, 750);
});

window.addEventListener('beforeunload', closeAlertsLiveStream);
