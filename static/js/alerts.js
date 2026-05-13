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

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function toggleHidden(id, hidden) {
  const el = document.getElementById(id);
  if (el) el.hidden = hidden;
}

function renderAlertRows(alerts) {
  const list = document.getElementById('alertsList');
  if (!list) return;

  list.innerHTML = alerts.map(alert => `
    <article class="alert-row">
      <div class="alert-row-main">
        <div class="alert-row-top">
          <span class="alert-build-tag">Build #${alert.build_number}</span>
          <span class="alert-severity">${alert.severity}</span>
        </div>
        <div class="alert-message">${alert.message}</div>
        <div class="alert-meta">
          <span>Current: ${formatAlertDuration(alert.duration_ms)}</span>
          <span>Threshold: ${formatAlertDuration(alert.threshold_ms)}</span>
          <span>Over by: ${formatAlertDuration(alert.exceeded_by_ms)}</span>
          <span>Started: ${formatAlertTime(alert.started_at)}</span>
        </div>
      </div>
    </article>
  `).join('');
}

async function loadAlerts() {
  try {
    const url = document.body.dataset.alertsUrl;
    const data = await (await fetch(url)).json();
    const summary = data.summary || {};
    const alerts = data.alerts || [];
    const pipeline = data.pipeline || {};
    const rule = data.rule || {};
    const alertCount = Number(summary.alert_count ?? alerts.length ?? 0);

    setText(
      'alertsSubtitle',
      `Watching ${pipeline.name || 'Jenkins Pipeline'} on ${pipeline.selected_branch || 'main'}`
    );
    setText(
      'alertsRuleText',
      `Running builds above the test threshold of ${formatAlertDuration(rule.threshold_ms || summary.threshold_ms || 0)}`
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
      renderAlertRows(alerts);
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

document.addEventListener('DOMContentLoaded', () => {
  loadAlerts();
  setInterval(loadAlerts, 2000);
});
