function fmtInt(val) {
  if (val === null || val === undefined) return '--';
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

  const banner = document.getElementById('sonarBanner');
  const projectKeyEl = document.getElementById('sonarProjectKey');
  const gatePill = document.getElementById('sonarGatePill');

  try {
    const res = await fetch(url);
    const data = await res.json();

    if (!data.connected) {
      if (banner) {
        banner.textContent = data.message || 'SonarCloud is unavailable.';
        banner.style.display = 'inline-flex';
      }
      setGatePill(gatePill, null);
      return;
    }

    if (banner) banner.style.display = 'none';
    if (projectKeyEl) projectKeyEl.textContent = 'Project: ' + (data.project_key || '--');

    const metrics = data.metrics || {};
    const gate = data.quality_gate || {};

    document.getElementById('sonarBugs').textContent = fmtInt(metrics.bugs);
    document.getElementById('sonarVulnerabilities').textContent = fmtInt(metrics.vulnerabilities);
    document.getElementById('sonarSmells').textContent = fmtInt(metrics.code_smells);
    document.getElementById('sonarHotspots').textContent = fmtInt(metrics.security_hotspots);
    document.getElementById('sonarCoverage').textContent = fmtPct(metrics.coverage);
    document.getElementById('sonarDupes').textContent = fmtPct(metrics.duplicated_lines_density);
    document.getElementById('sonarNcloc').textContent = fmtInt(metrics.ncloc);

    const gateStatus = gate.status || '--';
    document.getElementById('sonarGateStatus').textContent = gateStatus;
    document.getElementById('sonarGateMeta').textContent =
      'Conditions: ' + (gate.conditions ? gate.conditions.length : 0) + ' · Failing: ' + (gate.failed ?? 0);

    setGatePill(gatePill, gate.status);
    renderConditions(document.getElementById('sonarConditions'), gate.conditions);
  } catch (e) {
    if (banner) {
      banner.textContent = 'Failed to load SonarCloud data.';
      banner.style.display = 'inline-flex';
    }
    setGatePill(gatePill, null);
  }
}

document.addEventListener('DOMContentLoaded', loadSonarCloud);
