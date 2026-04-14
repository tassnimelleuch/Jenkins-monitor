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

function setConditionsState(message) {
  const listEl = document.getElementById('sonarConditions');
  if (listEl) listEl.innerHTML = `<div class="sonar-empty">${message}</div>`;
}

function renderConditions(listEl, conditions) {
  if (!listEl) return;
  if (!conditions || conditions.length === 0) {
    listEl.innerHTML = '<div class="sonar-empty">No conditions reported.</div>';
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
        <span class="sonar-cond-status ${statusClass}">${status || '--'}</span>
        <span class="sonar-cond-key">${c.metric || 'metric'}</span>
      </div>
      <div class="sonar-cond-right">value: <strong>${value}</strong> · threshold: ${threshold}</div>
    `;
    listEl.appendChild(div);
  });
}

async function loadSonarCloud() {
  const url = document.body.dataset.sonarUrl;
  if (!url) return;

  const projectKeyEl = document.getElementById('sonarProjectKey');
  const gatePill = document.getElementById('sonarGatePill');
  setConditionsState('Loading conditions...');

  try {
    const res = await fetch(url);
    const data = await res.json();
    window.__sonarData = data;

    if (!data.connected) {
      setGatePill(gatePill, null);
      setConditionsState('No conditions reported.');
      return;
    }
    if (projectKeyEl) projectKeyEl.textContent = 'Project: ' + (data.project_key || '--');

    const metrics = data.metrics || {};
    const gate = data.quality_gate || {};

    const bugsEl = document.getElementById('sonarBugs');
    if (bugsEl) bugsEl.textContent = fmtInt(metrics.bugs);

    const vulnEl = document.getElementById('sonarVulnerabilities');
    if (vulnEl) vulnEl.textContent = fmtInt(metrics.vulnerabilities);

    const smellsEl = document.getElementById('sonarSmells');
    if (smellsEl) smellsEl.textContent = fmtInt(metrics.code_smells);

    const hotspotsEl = document.getElementById('sonarHotspots');
    if (hotspotsEl) hotspotsEl.textContent = fmtInt(metrics.security_hotspots);

    const dupesEl = document.getElementById('sonarDupes');
    if (dupesEl) dupesEl.textContent = fmtPct(metrics.duplicated_lines_density);

    const nclocEl = document.getElementById('sonarNcloc');
    if (nclocEl) nclocEl.textContent = fmtInt(metrics.ncloc);

    const gateStatus = gate.status || '--';
    setTextById('sonarGateStatus', gateStatus);
    setTextById(
      'sonarGateMeta',
      'Conditions: ' + (gate.conditions ? gate.conditions.length : 0) + ' · Failing: ' + (gate.failed ?? 0)
    );

    setGatePill(gatePill, gate.status);
    renderConditions(document.getElementById('sonarConditions'), gate.conditions);
    highlightFailingKpis(gate);
  } catch (e) {
    setGatePill(gatePill, null);
    setConditionsState('Conditions unavailable.');
  }
}

document.addEventListener('DOMContentLoaded', loadSonarCloud);

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
    const line = issue.line ? `:${issue.line}` : '';
    return `
      <div class="sonar-issue">
        <div class="sonar-issue-head">
          <span class="sonar-issue-sev ${sevClass}">${sevLabel}</span>
          <span class="sonar-issue-rule">${issue.rule || 'rule'}</span>
        </div>
        <div class="sonar-issue-msg">${issue.message || '—'}</div>
        <div class="sonar-issue-meta">${issue.component || 'component'}${line} · status: ${issue.status || '--'}</div>
      </div>
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
        <span class="sonar-issue-rule">${c.metric || 'metric'}</span>
      </div>
      <div class="sonar-issue-msg">Actual: ${c.value ?? '--'} · Threshold: ${c.threshold ?? '--'}</div>
      <div class="sonar-issue-meta">Status: ${c.status || '--'}</div>
    </div>
  `).join('');
}

async function handleKpiClick(card) {
  const issueType = card.dataset.issueType;
  const metric = card.dataset.metric;
  const action = card.dataset.action;

  if (issueType) {
    openDrawer(`${issueType.replace('_', ' ')} Issues`, 'Loading...', '<div class="sonar-empty">Loading issues...</div>');
    try {
      const res = await fetch(`/api/sonarcloud/issues?type=${encodeURIComponent(issueType)}&page=1&page_size=50`);
      const data = await res.json();
      if (!data.connected) {
        openDrawer('Issues', data.message || 'Unavailable', '<div class="sonar-empty">No data.</div>');
        return;
      }
      const subtitle = `Total: ${data.paging?.total ?? data.issues?.length ?? 0}`;
      openDrawer(`${issueType.replace('_', ' ')} Issues`, subtitle, renderIssues(data.issues));
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
    openDrawer('Metric', metric || '—', '<div class="sonar-empty">No issue list for this KPI.</div>');
  }
}

document.addEventListener('click', (e) => {
  const card = e.target.closest('.sonar-kpi');
  if (card) {
    handleKpiClick(card);
  }
  if (e.target.id === 'sonarDrawerBackdrop' || e.target.id === 'sonarDrawerClose') {
    closeDrawer();
  }
});
