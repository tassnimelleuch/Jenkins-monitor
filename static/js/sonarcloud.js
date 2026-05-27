const SONAR_URL = document.body.dataset.sonarUrl || '';
const SONAR_LIVE_STREAM_URL = document.body.dataset.liveStreamUrl || '';
const SONAR_FALLBACK_POLL_MS = 30000;

const ISSUE_TYPE_LABELS = {
  BUG: 'Bugs',
  VULNERABILITY: 'Vulnerabilities',
  CODE_SMELL: 'Code Smells',
  SECURITY_HOTSPOT: 'Security Hotspots'
};

let _sonarLiveStream = null;
let _sonarFallbackPollHandle = null;
let _sonarFallbackStarted = false;
let _sonarLiveStreamReceived = false;
let _sonarLiveStreamLoggedError = false;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtInt(val) {
  if (val === null || val === undefined) return '--';
  if (typeof val === 'object') {
    const total = Object.values(val || {}).reduce((sum, v) => {
      const n = Number(v);
      return sum + (Number.isFinite(n) ? n : 0);
    }, 0);
    return Number(total).toLocaleString();
  }
  return Number(val).toLocaleString();
}

function fmtPct(val) {
  if (val === null || val === undefined) return '--';
  return Number(val).toFixed(2) + '%';
}

function setGatePill(pill, status) {
  if (!pill) return;
  const s = (status || '').toUpperCase();
  pill.classList.remove('ok', 'error', 'warn');
  if (s === 'OK') {
    pill.classList.add('ok');
    pill.textContent = 'Quality Gate: OK';
  } else if (s === 'ERROR') {
    pill.classList.add('error');
    pill.textContent = 'Quality Gate: Error';
  } else if (s === 'WARN') {
    pill.classList.add('warn');
    pill.textContent = 'Quality Gate: Warn';
  } else {
    pill.textContent = 'Quality Gate: --';
  }
}

function setTextById(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function resetSonarMetrics() {
  setTextById('sonarBugs', '--');
  setTextById('sonarVulnerabilities', '--');
  setTextById('sonarSmells', '--');
  setTextById('sonarHotspots', '--');
  setTextById('sonarDupes', '--');
  setTextById('sonarNcloc', '--');
  setTextById('sonarGateStatus', '--');
  setTextById('sonarGateMeta', 'Conditions: 0 · Failing: 0');
}

function setConditionsState(message) {
  const listEl = document.getElementById('sonarConditions');
  if (!listEl) return;
  const empty = document.createElement('div');
  empty.className = 'sonar-empty';
  empty.textContent = message;
  listEl.replaceChildren(empty);
}

function formatIssueTypeLabel(issueType) {
  return ISSUE_TYPE_LABELS[issueType] || String(issueType || 'Issues').replace(/_/g, ' ');
}

function updateIssueCardLinks(data) {
  const issueLinks = (data && data.links && data.links.issue_types) || {};
  document.querySelectorAll('.sonar-kpi[data-issue-type]').forEach(card => {
    const issueType = card.dataset.issueType;
    const url = issueLinks[issueType] || '';
    if (url) {
      card.dataset.externalUrl = url;
      card.setAttribute('aria-disabled', 'false');
      card.setAttribute('aria-label', `View ${formatIssueTypeLabel(issueType)} details`);
    } else {
      delete card.dataset.externalUrl;
      card.setAttribute('aria-disabled', 'true');
      card.setAttribute('aria-label', `${formatIssueTypeLabel(issueType)} unavailable`);
    }
  });
}

function renderConditions(listEl, conditions) {
  if (!listEl) return;
  if (!conditions || conditions.length === 0) {
    setConditionsState('No conditions reported.');
    return;
  }

  listEl.innerHTML = '';
  conditions.forEach(c => {
    const status = (c.status || '').toUpperCase();
    const statusClass = status === 'OK' ? 'ok' : (status === 'ERROR' ? 'error' : 'warn');
    const value = c.value ?? '--';
    const threshold = c.threshold ?? '--';
    const div = document.createElement('div');
    div.className = 'sonar-cond-item';
    div.innerHTML = `
      <div class="sonar-cond-left">
        <span class="sonar-cond-status ${statusClass}">${escapeHtml(status || '--')}</span>
        <span class="sonar-cond-key">${escapeHtml(c.metric || 'metric')}</span>
      </div>
      <div class="sonar-cond-right">value: <strong>${escapeHtml(value)}</strong> · threshold: ${escapeHtml(threshold)}</div>
    `;
    listEl.appendChild(div);
  });
}

function applySonarCloudPayload(data) {
  const projectKeyEl = document.getElementById('sonarProjectKey');
  const gatePill = document.getElementById('sonarGatePill');
  window.__sonarData = data || {};
  updateIssueCardLinks(data);

  if (!data || !data.connected) {
    if (projectKeyEl) projectKeyEl.textContent = 'Project: —';
    resetSonarMetrics();
    setGatePill(gatePill, null);
    setConditionsState((data && data.message) || 'Conditions unavailable.');
    highlightFailingKpis(null);
    return;
  }

  if (projectKeyEl) projectKeyEl.textContent = 'Project: ' + (data.project_key || '--');

  const metrics = data.metrics || {};
  const gate = data.quality_gate || {};
  setTextById('sonarBugs', fmtInt(metrics.bugs));
  setTextById('sonarVulnerabilities', fmtInt(metrics.vulnerabilities));
  setTextById('sonarSmells', fmtInt(metrics.code_smells));
  setTextById('sonarHotspots', fmtInt(metrics.security_hotspots));
  setTextById('sonarDupes', fmtPct(metrics.duplicated_lines_density));
  setTextById('sonarNcloc', fmtInt(metrics.ncloc));

  setTextById('sonarGateStatus', gate.status || '--');
  setTextById(
    'sonarGateMeta',
    'Conditions: ' + (gate.conditions ? gate.conditions.length : 0) + ' · Failing: ' + (gate.failed ?? 0)
  );

  setGatePill(gatePill, gate.status);
  renderConditions(document.getElementById('sonarConditions'), gate.conditions);
  highlightFailingKpis(gate);
}

async function loadSonarCloud() {
  if (!SONAR_URL) return;

  setConditionsState('Loading conditions...');

  try {
    const res = await fetch(SONAR_URL);
    const data = await res.json();
    applySonarCloudPayload(data);
  } catch (e) {
    resetSonarMetrics();
    setGatePill(document.getElementById('sonarGatePill'), null);
    setConditionsState('Conditions unavailable.');
  }
}

function highlightFailingKpis(gate) {
  document.querySelectorAll('.sonar-kpi-fail').forEach(el => el.classList.remove('sonar-kpi-fail'));
  const conditions = (gate && gate.conditions) || [];
  const failing = conditions.filter(c => (c.status || '').toUpperCase() === 'ERROR');
  if (failing.length === 0) return;

  const metrics = new Set(failing.map(c => c.metric));
  let matched = 0;
  document.querySelectorAll('.sonar-kpi[data-metric]').forEach(card => {
    const key = card.dataset.metric;
    if (metrics.has(key)) {
      card.classList.add('sonar-kpi-fail');
      matched += 1;
    }
  });

  if (matched === 0) {
    const gateCard = document.getElementById('sonarGateCard');
    if (gateCard) gateCard.classList.add('sonar-kpi-fail');
  }
}

function openDrawer(title, subtitle, bodyHtml) {
  const drawer = document.getElementById('sonarDrawer');
  const titleEl = document.getElementById('sonarDrawerTitle');
  const subEl = document.getElementById('sonarDrawerSub');
  const bodyEl = document.getElementById('sonarDrawerBody');
  if (!drawer || !titleEl || !subEl || !bodyEl) return;
  titleEl.textContent = title || 'Details';
  subEl.textContent = subtitle || '—';
  bodyEl.innerHTML = bodyHtml || '<div class="sonar-empty">No details available.</div>';
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
}

function closeDrawer() {
  const drawer = document.getElementById('sonarDrawer');
  if (!drawer) return;
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}

function renderIssues(issues) {
  if (!issues || issues.length === 0) {
    return '<div class="sonar-empty">No open issues found.</div>';
  }

  return issues.map(issue => {
    const rawSev = (issue.severity || 'INFO').toUpperCase();
    const sevClass = rawSev.toLowerCase();
    let sevLabel = rawSev;
    if (rawSev === 'BLOCKER' || rawSev === 'CRITICAL') sevLabel = 'High';
    else if (rawSev === 'MAJOR') sevLabel = 'Medium';
    else if (rawSev === 'MINOR' || rawSev === 'INFO') sevLabel = 'Low';

    const issueUrl = issue.url || '';
    const line = issue.line ? `:${issue.line}` : '';
    const tagName = issueUrl ? 'a' : 'div';
    const hrefAttr = issueUrl
      ? ` href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener noreferrer"`
      : '';
    const openLabel = issueUrl
      ? '<span class="sonar-issue-open">Open in SonarCloud</span>'
      : '';

    return `
      <${tagName} class="sonar-issue${issueUrl ? ' sonar-issue-link' : ''}"${hrefAttr}>
        <div class="sonar-issue-head">
          <span class="sonar-issue-sev ${sevClass}">${escapeHtml(sevLabel)}</span>
          <span class="sonar-issue-rule">${escapeHtml(issue.rule || 'rule')}</span>
        </div>
        <div class="sonar-issue-msg">${escapeHtml(issue.message || '—')}</div>
        <div class="sonar-issue-meta">${escapeHtml((issue.component || 'component') + line)} · status: ${escapeHtml(issue.status || '--')}</div>
        ${openLabel}
      </${tagName}>
    `;
  }).join('');
}

function renderFailingConditions(conditions) {
  const failing = (conditions || []).filter(c => (c.status || '').toUpperCase() === 'ERROR');
  if (failing.length === 0) return '<div class="sonar-empty">No failing conditions.</div>';
  return failing.map(c => `
    <div class="sonar-issue">
      <div class="sonar-issue-head">
        <span class="sonar-issue-sev critical">FAIL</span>
        <span class="sonar-issue-rule">${escapeHtml(c.metric || 'metric')}</span>
      </div>
      <div class="sonar-issue-msg">Actual: ${escapeHtml(c.value ?? '--')} · Threshold: ${escapeHtml(c.threshold ?? '--')}</div>
      <div class="sonar-issue-meta">Status: ${escapeHtml(c.status || '--')}</div>
    </div>
  `).join('');
}

function renderMetricDetails(metric) {
  const data = window.__sonarData || {};
  const metrics = data.metrics || {};
  const gate = data.quality_gate || {};
  const conditions = Array.isArray(gate.conditions) ? gate.conditions : [];

  if (metric === 'duplicated_lines_density') {
    const value = metrics.duplicated_lines_density;
    const condition = conditions.find(item => item.metric === 'duplicated_lines_density');
    const rows = [
      `
        <div class="sonar-issue">
          <div class="sonar-issue-head">
            <span class="sonar-issue-rule">Current duplication</span>
          </div>
          <div class="sonar-issue-msg">${escapeHtml(fmtPct(value))}</div>
          <div class="sonar-issue-meta">Duplicated lines density reported by SonarCloud.</div>
        </div>
      `
    ];

    if (condition) {
      rows.push(`
        <div class="sonar-issue">
          <div class="sonar-issue-head">
            <span class="sonar-issue-sev ${String(condition.status || '').toLowerCase() === 'error' ? 'critical' : 'major'}">${escapeHtml(condition.status || '--')}</span>
            <span class="sonar-issue-rule">Quality Gate Condition</span>
          </div>
          <div class="sonar-issue-msg">Actual: ${escapeHtml(condition.value ?? '--')} · Threshold: ${escapeHtml(condition.threshold ?? '--')}</div>
          <div class="sonar-issue-meta">This metric is tracked at quality-gate level, not as an issue list.</div>
        </div>
      `);
    } else {
      rows.push(`
        <div class="sonar-issue">
          <div class="sonar-issue-head">
            <span class="sonar-issue-rule">No issue list</span>
          </div>
          <div class="sonar-issue-msg">Duplications are shown as a metric, not as Bugs/Vulnerabilities/Code Smells.</div>
          <div class="sonar-issue-meta">If you have duplication, you will see the percentage here and any related quality-gate condition if SonarCloud returns one.</div>
        </div>
      `);
    }

    return rows.join('');
  }

  return '<div class="sonar-empty">No detail panel for this KPI.</div>';
}

async function handleKpiClick(event, card) {
  if (card.getAttribute('aria-disabled') === 'true') {
    return;
  }

  const issueType = card.dataset.issueType;
  const metric = card.dataset.metric;
  const action = card.dataset.action;

  if (issueType) {
    openDrawer(`${formatIssueTypeLabel(issueType)}`, 'Loading...', '<div class="sonar-empty">Loading issues...</div>');
    try {
      const res = await fetch(`/api/sonarcloud/issues?type=${encodeURIComponent(issueType)}&page=1&page_size=50`);
      const data = await res.json();
      if (!data.connected) {
        openDrawer('Issues', data.message || 'Unavailable', '<div class="sonar-empty">No data.</div>');
        return;
      }
      const subtitle = `Total: ${data.paging?.total ?? data.issues?.length ?? 0}`;
      openDrawer(formatIssueTypeLabel(issueType), subtitle, renderIssues(data.issues));
    } catch (e) {
      openDrawer('Issues', 'Failed to load', '<div class="sonar-empty">Request failed.</div>');
    }
    return;
  }

  if (action === 'conditions') {
    const gate = (window.__sonarData && window.__sonarData.quality_gate) || {};
    openDrawer('Quality Gate Fails', 'Failing conditions', renderFailingConditions(gate.conditions));
    return;
  }

  if (action === 'metric') {
    openDrawer('Metric', metric || '—', renderMetricDetails(metric));
  }
}

function canUseSonarLiveStream() {
  return typeof window.EventSource !== 'undefined' && Boolean(SONAR_LIVE_STREAM_URL);
}

function sonarLiveStreamActive() {
  return Boolean(_sonarLiveStream);
}

function closeSonarLiveStream() {
  if (_sonarLiveStream) {
    _sonarLiveStream.close();
    _sonarLiveStream = null;
  }
}

function startSonarPollingFallback() {
  if (_sonarFallbackStarted) return;
  _sonarFallbackStarted = true;
  if (_sonarFallbackPollHandle) clearInterval(_sonarFallbackPollHandle);
  _sonarFallbackPollHandle = window.setInterval(loadSonarCloud, SONAR_FALLBACK_POLL_MS);
}

function connectSonarLiveStream() {
  if (!canUseSonarLiveStream() || sonarLiveStreamActive()) return false;

  _sonarLiveStream = new EventSource(SONAR_LIVE_STREAM_URL);
  _sonarLiveStream.addEventListener('stream_ready', () => {
    _sonarLiveStreamReceived = true;
    _sonarLiveStreamLoggedError = false;
  });
  _sonarLiveStream.addEventListener('heartbeat', () => {
    _sonarLiveStreamReceived = true;
  });
  _sonarLiveStream.addEventListener('sonarcloud_payload', event => {
    _sonarLiveStreamReceived = true;
    _sonarLiveStreamLoggedError = false;
    try {
      applySonarCloudPayload(JSON.parse(event.data));
    } catch (error) {
      console.error('SonarCloud SSE parse error:', error);
    }
  });
  _sonarLiveStream.onerror = () => {
    if (!_sonarLiveStreamLoggedError) {
      console.warn('SonarCloud SSE stream disconnected. The browser will retry automatically.');
      _sonarLiveStreamLoggedError = true;
    }
    if (!_sonarLiveStreamReceived) {
      startSonarPollingFallback();
    }
  };

  return true;
}

document.addEventListener('click', (e) => {
  const card = e.target.closest('.sonar-kpi');
  if (card) {
    handleKpiClick(e, card);
  }
  if (e.target.id === 'sonarDrawerBackdrop' || e.target.id === 'sonarDrawerClose') {
    closeDrawer();
  }
});

document.addEventListener('keydown', (e) => {
  const card = e.target.closest('.sonar-kpi');
  if (!card) return;
  if (e.key !== 'Enter' && e.key !== ' ') return;
  e.preventDefault();
  handleKpiClick(e, card);
});

document.addEventListener('DOMContentLoaded', () => {
  const hasLiveStream = connectSonarLiveStream();
  loadSonarCloud();
  if (!hasLiveStream) {
    startSonarPollingFallback();
  }
});

window.addEventListener('beforeunload', closeSonarLiveStream);
