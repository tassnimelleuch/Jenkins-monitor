function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val == null || val === '' ? '--' : val;
}

function formatBuildMeta(build) {
  if (!build || build.number == null) return '--';

  const meta = [];
  if (build.result == null) {
    meta.push('Running');
  } else if (build.result) {
    meta.push(build.result);
  }

  if (build.timestamp) {
    meta.push(formatUserDateTime(build.timestamp, {
      includeSeconds: false,
      fallback: '--',
    }));
  }

  return meta.join(' | ') || '--';
}

function buildStatusInfo(branch) {
  const latestBuild = branch?.latest_build || {};
  const latestCompletedBuild = branch?.latest_completed_build || {};

  if (
    branch?.is_building
    || (latestBuild.number != null && latestBuild.result == null)
  ) {
    return { label: 'Running', className: 'running' };
  }

  const result = latestCompletedBuild.result || latestBuild.result;
  if (result === 'SUCCESS') return { label: 'Success', className: 'success' };
  if (result === 'FAILURE') return { label: 'Failure', className: 'failure' };
  if (result === 'ABORTED') return { label: 'Aborted', className: 'aborted' };
  return { label: 'Unknown', className: 'unknown' };
}

function renderBuildCell(build) {
  const cell = document.createElement('div');
  cell.className = 'pd-branch-build';

  if (!build || build.number == null) {
    const empty = document.createElement('span');
    empty.className = 'pd-muted';
    empty.textContent = '--';
    cell.appendChild(empty);
    return cell;
  }

  if (build.url) {
    const link = document.createElement('a');
    link.className = 'pd-branch-build-link';
    link.href = build.url;
    link.target = '_blank';
    link.rel = 'noreferrer';
    link.textContent = `#${build.number}`;
    cell.appendChild(link);
  } else {
    const text = document.createElement('span');
    text.className = 'pd-branch-build-link';
    text.textContent = `#${build.number}`;
    cell.appendChild(text);
  }

  const meta = document.createElement('span');
  meta.className = 'pd-branch-build-meta';
  meta.textContent = formatBuildMeta(build);
  cell.appendChild(meta);

  return cell;
}

function renderBranches(list) {
  const box = document.getElementById('pdBranches');
  if (!box) return;
  box.innerHTML = '';

  if (!list || list.length === 0) {
    const row = document.createElement('div');
    row.className = 'pd-muted';
    row.textContent = 'No branches found.';
    box.appendChild(row);
    return;
  }

  list.forEach(branch => {
    const row = document.createElement('div');
    row.className = 'pd-branch-row';

    const branchCell = document.createElement('div');
    branchCell.className = 'pd-branch-name';

    const branchName = document.createElement('span');
    branchName.className = 'pd-branch-name-text';
    branchName.textContent = branch.name || '--';
    branchCell.appendChild(branchName);

    if (branch.is_kpi_source) {
      const badge = document.createElement('span');
      badge.className = 'pd-branch-badge';
      badge.textContent = 'KPI';
      branchCell.appendChild(badge);
    }

    row.appendChild(branchCell);
    row.appendChild(renderBuildCell(branch.latest_build));
    row.appendChild(renderBuildCell(branch.latest_completed_build));

    const status = buildStatusInfo(branch);
    const statusCell = document.createElement('span');
    statusCell.className = `pd-branch-status ${status.className}`;
    statusCell.textContent = status.label;
    row.appendChild(statusCell);

    box.appendChild(row);
  });
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
    const branchNames = pipeline.branch_names || [];

    setText('pdTitle', job.display_name || job.name || 'Pipeline Details');
    setText('pdSub', 'Configuration and branch coverage for this pipeline.');
    setText('pdBranchNote', pipeline.kpi_note || 'Pipeline KPIs are collected from the main branch only.');

    renderBranches(data.branches || []);

    setText('pdPipeType', pipeline.type || '--');
    setText('pdKpiBranch', pipeline.kpi_branch || '--');
    setText('pdAllBranches', branchNames.length ? branchNames.join(', ') : '--');
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
