function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val == null || val === '' ? '--' : val;
}

function renderTriggers(list) {
  const box = document.getElementById('pdTriggers');
  if (!box) return;
  box.innerHTML = '';
  if (!list || list.length === 0) {
    const d = document.createElement('div');
    d.className = 'pd-muted';
    d.textContent = 'No triggers configured.';
    box.appendChild(d);
    return;
  }
  list.forEach(t => {
    const row = document.createElement('div');
    row.className = 'pd-chip';
    const type = t.type || 'Trigger';
    const spec = t.spec || 'on event';
    row.innerHTML = `<span>${type}</span><span>${spec}</span>`;
    box.appendChild(row);
  });
}

function renderParams(list) {
  const box = document.getElementById('pdParams');
  if (!box) return;
  box.innerHTML = '';
  if (!list || list.length === 0) {
    const row = document.createElement('div');
    row.className = 'pd-muted';
    row.textContent = 'No parameters defined.';
    box.appendChild(row);
    return;
  }
  list.forEach(p => {
    const row = document.createElement('div');
    row.className = 'pd-table-row';
    row.innerHTML = `<span>${p.name || '--'}</span><span>${p.type || '--'}</span><span>${p.default ?? '--'}</span>`;
    box.appendChild(row);
  });
}

function fmtBool(val) {
  if (val === true) return 'Yes';
  if (val === false) return 'No';
  return '--';
}

function fmtNum(val) {
  if (val == null || val === '') return '--';
  const n = Number(val);
  if (Number.isNaN(n)) return String(val);
  return n.toLocaleString();
}

async function loadPipelineDetails() {
  const url = document.body.dataset.pipelineDetailsUrl;
  if (!url) return;
  const banner = document.getElementById('pdDisconnected');

  try {
    const res = await fetch(url);
    const data = await res.json();

    if (!res.ok || !data.connected) {
      if (banner) {
        banner.style.display = 'flex';
        banner.innerHTML = `
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><line x1="12" y1="7" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          ${data && data.message
            ? data.message
            : 'Pipeline details are unavailable. Check Jenkins connection and permissions.'}
        `;
      }
      return;
    }
    if (banner) banner.style.display = 'none';

    const job = data.job || {};
    const pipeline = data.pipeline || {};
    const discarder = data.build_discarder || {};
    setText('pdTitle', job.display_name || job.name || 'Pipeline Details');
    setText('pdSub', 'Minimal configuration details (not shown elsewhere).');

    renderTriggers(data.triggers || []);
    renderParams(data.parameters || []);

    setText('pdPipeType', pipeline.type || '--');
    setText('pdMultibranch', fmtBool(pipeline.multibranch));
    setText('pdJobClass', pipeline.job_class || '--');
    setText('pdDefClass', pipeline.definition_class || '--');
    setText('pdScriptPath', pipeline.script_path || '--');
    if (discarder.num_to_keep == null || discarder.num_to_keep === '') {
      setText('pdMaxBuilds', '--');
    } else {
      setText('pdMaxBuilds', fmtNum(discarder.num_to_keep));
    }
  } catch (e) {
    if (banner) {
      banner.style.display = 'flex';
      banner.innerHTML = `
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><line x1="12" y1="7" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        Pipeline details are unavailable. Check Jenkins connection and permissions.
      `;
    }
    console.error('Pipeline details error:', e);
  }
}

document.addEventListener('DOMContentLoaded', loadPipelineDetails);
