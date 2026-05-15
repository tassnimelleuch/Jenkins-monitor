function fmtNum(val) {
  if (val === null || val === undefined) return '--';
  return Number(val).toLocaleString();
}

function fmtDate(val) {
  if (!val) return '--';
  const d = new Date(val);
  if (isNaN(d.getTime())) return val;
  return d.toLocaleString();
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function renderAnalyticsNotice(data) {
  const el = document.getElementById('ghAnalyticsNotice');
  if (!el) return;
  const message = data?.analytics_notice;
  if (!message) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  el.hidden = false;
  el.textContent = message;
}

let _ghAnalyticsPayload = null;
let _ghAnalyticsGrouping = '24h';

function analyticsLimitedMessage(data) {
  if (data?.analytics_mode !== 'branch_heads') return null;
  return data.analytics_message || 'Analytics are limited to the latest commit on each branch.';
}

function formatMonthKey(key, format = 'short') {
  if (!key || typeof key !== 'string') return key || '--';
  const [year, month] = key.split('-');
  const parsed = new Date(Number(year), Number(month) - 1, 1);
  if (Number.isNaN(parsed.getTime())) return key;
  return parsed.toLocaleDateString('en-US', {
    month: format === 'long' ? 'long' : 'short',
    year: format === 'long' ? 'numeric' : '2-digit'
  });
}

function analyticsUnitLabel() {
  if (_ghAnalyticsGrouping === '24h') return 'day';
  return _ghAnalyticsGrouping === 'week' ? 'week' : 'month';
}

function analyticsWindowCount(data) {
  const windowData = data?.analytics_window || {};
  if (_ghAnalyticsGrouping === '24h') return 1;
  return _ghAnalyticsGrouping === 'week' ? (windowData.weeks || 0) : (windowData.months || 0);
}

function updateGitHubAnalyticsButtons() {
  document.querySelectorAll('.gh-analytics-btn').forEach((button) => {
    button.classList.toggle('active', button.dataset.ghGroup === _ghAnalyticsGrouping);
  });
}

function normalizeLegacyMonthChurn(data) {
  if (!Array.isArray(data?.code_churn)) return [];
  return data.code_churn.map((item) => ({
    period_key: item.month,
    label: formatMonthKey(item.month, 'short'),
    detail_label: formatMonthKey(item.month, 'long'),
    start_date: `${item.month}-01`,
    additions: item.additions || 0,
    deletions: item.deletions || 0,
    commits: 0,
    changed_files: 0,
    files_added: 0,
    files_modified: 0,
    files_removed: 0,
    files_renamed: 0
  }));
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fileLineChanges(file) {
  if (!file) return 0;
  if (Number.isFinite(Number(file.line_changes))) return Number(file.line_changes);
  return Number(file.additions || 0) + Number(file.deletions || 0);
}

function fileTouches(file) {
  if (!file) return 0;
  return Number(file.touches ?? file.changes ?? 0);
}

function splitFilePath(filename) {
  const parts = String(filename || '').split('/');
  const name = parts.pop() || filename || '--';
  return {
    name,
    directory: parts.join('/') || 'repo root'
  };
}

function fileStatusSummary(file) {
  const statuses = [];
  if (file?.modified) statuses.push(`${fmtNum(file.modified)} modified`);
  if (file?.added) statuses.push(`${fmtNum(file.added)} added`);
  if (file?.removed) statuses.push(`${fmtNum(file.removed)} removed`);
  if (file?.renamed) statuses.push(`${fmtNum(file.renamed)} renamed`);
  return statuses.join(' | ') || 'Recent activity';
}

function getCodeChurnDataset(data) {
  const dataset = data?.code_churn_24h;
  if (dataset && typeof dataset === 'object') return dataset;
  return {
    scope_label: 'Lines added and deleted in the last 24 hours',
    commit_count: 0,
    changed_files: 0,
    additions: 0,
    deletions: 0,
    total_lines_changed: 0,
    net_change: 0
  };
}

function getFileChangeDataset(data) {
  const grouped = data?.file_changes_by_period || {};
  const dataset = grouped['24h'];
  if (dataset && Array.isArray(dataset.items)) return dataset;
  return {
    items: Array.isArray(data?.file_changes) ? data.file_changes : [],
    period_count: 1,
    scope_label: 'Top 5 most changed files in the last 24 hours',
    ranking_label: 'Ranked by total lines changed',
    commit_count: 0,
    total_files: Array.isArray(data?.file_changes) ? data.file_changes.length : 0
  };
}

function setGitHubAnalyticsGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month' && grouping !== '24h') return;
  _ghAnalyticsGrouping = grouping;
  updateGitHubAnalyticsButtons();
  if (_ghAnalyticsPayload) {
    renderMostChanged(_ghAnalyticsPayload);
    renderCodeChurn(_ghAnalyticsPayload);
  }
}

// Tag modal functions
function openTagModal(sha, shortSha, branchName) {
  const modal = document.getElementById('ghTagModal');
  if (!modal) {
    console.error('Tag modal not found');
    return;
  }
  modal.dataset.branchName = branchName || '';
  document.getElementById('tagCommitSha').value = sha;
  document.getElementById('tagCommitDisplay').textContent = shortSha;
  document.getElementById('tagNameInput').value = '';
  document.getElementById('tagMessageInput').value = '';
  document.getElementById('tagStatus').textContent = '';
  document.getElementById('tagStatus').className = 'gh-tag-status';
  modal.style.display = 'flex';
}

function closeTagModal() {
  const modal = document.getElementById('ghTagModal');
  if (modal) {
    modal.dataset.branchName = '';
    modal.style.display = 'none';
  }
}

async function submitTag() {
  const modal = document.getElementById('ghTagModal');
  const sha = document.getElementById('tagCommitSha').value;
  const branchName = modal?.dataset.branchName || '';
  const tagName = document.getElementById('tagNameInput').value.trim();
  const message = document.getElementById('tagMessageInput').value.trim();
  const statusEl = document.getElementById('tagStatus');
  
  if (!tagName) {
    statusEl.textContent = 'Please enter a tag name';
    statusEl.className = 'gh-tag-status gh-tag-error';
    return;
  }

  if (!branchName) {
    statusEl.textContent = 'Unable to determine which branch this commit belongs to.';
    statusEl.className = 'gh-tag-status gh-tag-error';
    return;
  }
  
  // Validate tag name format (GitHub requirements)
  if (!/^[a-zA-Z0-9._-]+$/.test(tagName)) {
    statusEl.textContent = 'Invalid tag name. Use only letters, numbers, dots, dashes, and underscores.';
    statusEl.className = 'gh-tag-status gh-tag-error';
    return;
  }
  
  statusEl.textContent = 'Creating tag...';
  statusEl.className = 'gh-tag-status gh-tag-loading';
  
  try {
    const response = await fetch('/api/github/tag', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        sha: sha,
        branch_name: branchName,
        tag_name: tagName,
        message: message
      })
    });
    
    const data = await response.json();
    
    if (response.ok) {
      statusEl.textContent = `✓ Tag "${tagName}" created successfully!`;
      statusEl.className = 'gh-tag-status gh-tag-success';
      document.getElementById('tagNameInput').value = '';
      document.getElementById('tagMessageInput').value = '';
      
      // Close modal after 2 seconds
      setTimeout(() => {
        closeTagModal();
      }, 2000);
    } else {
      statusEl.textContent = data.error || 'Failed to create tag';
      statusEl.className = 'gh-tag-status gh-tag-error';
    }
  } catch (error) {
    statusEl.textContent = 'Error: ' + error.message;
    statusEl.className = 'gh-tag-status gh-tag-error';
  }
}

// Close modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
  const modal = document.getElementById('ghTagModal');
  if (modal) {
    window.addEventListener('click', function(event) {
      if (event.target === modal) {
        closeTagModal();
      }
    });
  }
});


function renderCommits(container, commits) {
  if (!container) return;
  if (!commits || commits.length === 0) {
    container.innerHTML = '<div class="gh-empty">No commits found.</div>';
    return;
  }
  container.innerHTML = '';
  commits.forEach(c => {
    const branchPill = c.branch_name
      ? `<span class="gh-branch-pill">${c.branch_name}</span>`
      : '';
    const div = document.createElement('div');
    div.className = 'gh-commit';
    div.innerHTML = `
      <div class="gh-commit-sha">${c.short_sha || '--'}</div>
      <div class="gh-commit-body">
        <div class="gh-commit-msg">${(c.message || '').split('\\n')[0]}</div>
        <div class="gh-commit-meta">${c.author_name || 'Unknown'} · ${fmtDate(c.date)} ${branchPill}</div>
        <div class="gh-commit-actions">
          ${c.html_url ? `<a class="gh-commit-link" href="${c.html_url}" target="_blank" rel="noopener">View commit</a>` : ''}
          ${c.tagging_allowed ? `<button class="gh-tag-btn" onclick="openTagModal('${c.sha}', '${c.short_sha}', decodeURIComponent('${encodeURIComponent(c.branch_name || '')}'))">Tag</button>` : ''}
        </div>
      </div>
    `;
    container.appendChild(div);
  });
}

async function loadGitHub() {
  const url = document.body.dataset.githubUrl;
  if (!url) return;

  try {
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`GitHub API returned ${res.status}`);
    }
    const data = await res.json();

    if (!data.connected) {
      setText('ghRepoName', data.message || 'GitHub unavailable');
      renderCommits(document.getElementById('ghCommits'), []);
      return;
    }

    const repo = data.repo_info || {};
    const full = repo.full_name || `${data.owner}/${data.repo}`;

    const repoDataMissing = !repo || (
      repo.stars == null &&
      repo.forks == null &&
      repo.open_issues == null &&
      !repo.updated_at
    );

    setText('ghRepoName', full);
    setText(
      'ghRepoDesc',
      repoDataMissing
      ? 'Repository data unavailable.'
      : (repo.description || '—')
    );
    setText('ghStars', fmtNum(repo.stars));
    setText('ghForks', fmtNum(repo.forks));
    setText('ghIssues', fmtNum(repo.open_issues));
    setText('ghBranch', repo.default_branch || '—');
    setText('ghLang', repo.language || '—');
    setText('ghUpdated', fmtDate(repo.updated_at));

    const link = document.getElementById('ghRepoLink');
    if (link && repo.html_url) link.href = repo.html_url;
    setText('ghCommitScope', data.commit_scope_label || 'Most recent commit on each branch');

    renderCommits(document.getElementById('ghCommits'), data.commits || []);
    renderPullRequests(document.getElementById('ghOpenPRs'), data.pull_requests_open || [], 'open');
    renderPullRequests(document.getElementById('ghMergedPRs'), data.pull_requests_merged || [], 'merged');
    try {
      _ghAnalyticsPayload = data;
      updateGitHubAnalyticsButtons();
      renderAnalyticsNotice(data);
      renderFailingCommit(data);
      renderFixCommit(data);
      renderTimeToFix(data);
      renderMostChanged(data);
      renderCodeChurn(data);
    } catch (e) {
      const container = document.getElementById('ghFailingCommit');
      if (container) {
        container.innerHTML = '<div class="gh-empty">Failed to render failed commit.</div>';
      }
    }

    if (Array.isArray(data.commits) && data.commits[0] && data.commits[0].sha) {
      localStorage.setItem('gh-last-seen', data.commits[0].sha);
    }
  } catch (e) {
    setText('ghRepoName', 'Failed to load GitHub data');
    renderCommits(document.getElementById('ghCommits'), []);
  }
}
function renderFailingCommit(data) {
  const container = document.getElementById('ghFailingCommit');
  if (!container) return;

  const fc = data.failing_commit;
  if (!fc || !fc.commit) {
    container.innerHTML = '<div class="gh-empty">No failed build commit found.</div>';
    return;
  }

  const c = fc.commit;

  const ghUser =
    c.author_login ||
    c.committer_login ||
    (c.author_name ? c.author_name.replace(/\s+/g, '') : null);

  const displayMsg = (c.message || 'No commit message').split('\n')[0];
  
  const avatarUrl = c.author_avatar || c.committer_avatar;
  const profileUrl = c.author_profile_url || c.committer_profile_url;
  const userName = c.author_name || c.committer_name || ghUser || 'Unknown';

  let userCardHTML = '';
  if (profileUrl) {
    userCardHTML = `
      <a href="${profileUrl}" target="_blank" rel="noopener" class="gh-user-card">
        ${avatarUrl ? `<img src="${avatarUrl}" alt="${userName}" class="gh-user-avatar">` : ''}
        <div class="gh-user-info">
          <div class="gh-user-name">${userName}</div>
          ${ghUser ? `<div class="gh-user-login">@${ghUser}</div>` : ''}
        </div>
      </a>
    `;
  } else {
    userCardHTML = `
      <div class="gh-user-card-plain">
        ${avatarUrl ? `<img src="${avatarUrl}" alt="${userName}" class="gh-user-avatar">` : ''}
        <div class="gh-user-info">
          <div class="gh-user-name">${userName}</div>
          ${ghUser ? `<div class="gh-user-login">@${ghUser}</div>` : ''}
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="gh-commit gh-commit-failing">
      <div class="gh-commit-header">
        <div class="gh-commit-title-row">
          <div>
            <a href="${c.html_url}" target="_blank" rel="noopener" class="gh-commit-sha-badge">${c.short_sha || '--'}</a>
            <span class="gh-build-badge">Build #${fc.build_number}</span>
          </div>
        </div>
        <div class="gh-commit-msg">${displayMsg}</div>
      </div>
      
      <div class="gh-culprit-section">
        <div class="gh-culprit-label">Failed by</div>
        ${userCardHTML}
      </div>
      
      <div class="gh-commit-footer">
        ${fmtDate(c.date) !== '--' ? `<div class="gh-meta">Committed ${fmtDate(c.date)}</div>` : ''}
        ${fc.build_url ? `<a href="${fc.build_url}" target="_blank" rel="noopener" class="gh-build-link">View Jenkins build →</a>` : ''}
      </div>
    </div>
  `;
}

function renderFixCommit(data) {
  const container = document.getElementById('ghFixCommit');
  if (!container) return;

  const fc = data.failing_commit;
  if (!fc || !fc.fix_commit) {
    container.innerHTML = '<div class="gh-empty">No fix commit found yet.</div>';
    return;
  }

  const c = fc.fix_commit;

  const ghUser =
    c.author_login ||
    c.committer_login ||
    (c.author_name ? c.author_name.replace(/\s+/g, '') : null);

  const displayMsg = (c.message || 'No commit message').split('\n')[0];
  
  const avatarUrl = c.author_avatar || c.committer_avatar;
  const profileUrl = c.author_profile_url || c.committer_profile_url;
  const userName = c.author_name || c.committer_name || ghUser || 'Unknown';
  const buildBadge = fc.fix_build_number ? `<span class="gh-build-badge gh-build-badge-success">Build #${fc.fix_build_number}</span>` : '';
  const label = fc.fix_same_sha ? 'Recovered in' : 'Fixed by';
  const detailNote = fc.fix_same_sha
    ? '<div class="gh-meta">The same commit later passed on a successful Jenkins build.</div>'
    : '';

  let userCardHTML = '';
  if (profileUrl) {
    userCardHTML = `
      <a href="${profileUrl}" target="_blank" rel="noopener" class="gh-user-card gh-user-card-success">
        ${avatarUrl ? `<img src="${avatarUrl}" alt="${userName}" class="gh-user-avatar">` : ''}
        <div class="gh-user-info">
          <div class="gh-user-name">${userName}</div>
          ${ghUser ? `<div class="gh-user-login">@${ghUser}</div>` : ''}
        </div>
      </a>
    `;
  } else {
    userCardHTML = `
      <div class="gh-user-card-plain gh-user-card-success">
        ${avatarUrl ? `<img src="${avatarUrl}" alt="${userName}" class="gh-user-avatar">` : ''}
        <div class="gh-user-info">
          <div class="gh-user-name">${userName}</div>
          ${ghUser ? `<div class="gh-user-login">@${ghUser}</div>` : ''}
        </div>
      </div>
    `;
  }

  container.innerHTML = `
    <div class="gh-commit gh-commit-fixed">
      <div class="gh-commit-header">
        <div class="gh-commit-title-row">
          <div>
            <a href="${c.html_url}" target="_blank" rel="noopener" class="gh-commit-sha-badge gh-commit-sha-badge-success">${c.short_sha || '--'}</a>
            ${buildBadge}
          </div>
        </div>
        <div class="gh-commit-msg gh-commit-msg-success">${displayMsg}</div>
        ${detailNote}
      </div>
      
      <div class="gh-culprit-section gh-culprit-section-success">
        <div class="gh-culprit-label gh-culprit-label-success">${label}</div>
        ${userCardHTML}
      </div>
      
      <div class="gh-commit-footer">
        ${fmtDate(c.date) !== '--' ? `<div class="gh-meta">Committed ${fmtDate(c.date)}</div>` : ''}
        <div class="gh-commit-actions">
          ${fc.fix_build_url ? `<a href="${fc.fix_build_url}" target="_blank" rel="noopener" class="gh-build-link gh-build-link-success">View Jenkins build →</a>` : ''}
          ${c.html_url ? `<a href="${c.html_url}" target="_blank" rel="noopener" class="gh-build-link gh-build-link-success">View commit →</a>` : ''}
        </div>
      </div>
    </div>
  `;
}

// CALCULATE AND DISPLAY TIME TO FIX
function renderTimeToFix(data) {
  const container = document.getElementById('ghTimeToFix');
  if (!container) return;

  const fc = data.failing_commit;
  if (!fc || !fc.commit || !fc.fix_commit) {
    container.innerHTML = '<div class="gh-empty">No fix commit found yet.</div>';
    return;
  }

  const failDate = fc.build_timestamp ? new Date(Number(fc.build_timestamp)) : new Date(fc.commit.date);
  const fixDate = fc.fix_build_timestamp ? new Date(Number(fc.fix_build_timestamp)) : new Date(fc.fix_commit.date);

  if (isNaN(failDate.getTime()) || isNaN(fixDate.getTime())) {
    container.innerHTML = '<div class="gh-empty">Unable to calculate time to fix (missing dates).</div>';
    return;
  }

  const diffMs = fixDate.getTime() - failDate.getTime();
  
  // Check if fix came before failure (shouldn't happen)
  if (diffMs < 0) {
    container.innerHTML = '<div class="gh-empty">Fix appears to be before failure.</div>';
    return;
  }

  // Format the time difference
  const diffSeconds = Math.floor(diffMs / 1000);
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  let timeStr = '';
  if (diffDays > 0) {
    timeStr = `${diffDays}d ${diffHours % 24}h ${diffMinutes % 60}m`;
  } else if (diffHours > 0) {
    timeStr = `${diffHours}h ${diffMinutes % 60}m`;
  } else if (diffMinutes > 0) {
    timeStr = `${diffMinutes}m ${diffSeconds % 60}s`;
  } else {
    timeStr = `${diffSeconds}s`;
  }

  container.innerHTML = `
    <div class="ttf-container">
      <div class="ttf-main">
        <div class="ttf-time">${timeStr}</div>
        <div class="ttf-label">From failed build to successful build</div>
      </div>
      
    </div>
  `;
}

// MOST CHANGED FILES
function renderMostChanged(data) {
  const container = document.getElementById('ghMostChanged');
  const subtitle = document.getElementById('ghMostChangedSub');
  const summary = document.getElementById('ghMostChangedSummary');
  if (!container) return;

  const limitedMessage = analyticsLimitedMessage(data);
  if (limitedMessage) {
    if (subtitle) subtitle.textContent = 'Reduced to branch heads';
    if (summary) summary.innerHTML = '';
    container.innerHTML = `<div class="gh-empty">${limitedMessage}</div>`;
    return;
  }

  const dataset = getFileChangeDataset(data);
  const files = (dataset.items || []).slice(0, 5);
  if (subtitle) subtitle.textContent = dataset.scope_label || 'Top 5 most changed files in the last 24 hours';

  if (!files || files.length === 0) {
    if (summary) summary.innerHTML = '';
    const emptyMessage = Number(dataset.commit_count || 0) === 0
      ? 'No files were changed on main in the last 24 hours.'
      : 'The last 24 hours returned commits, but no per-file details were available.';
    container.innerHTML = `<div class="gh-empty">${emptyMessage}</div>`;
    return;
  }

  const totalChangedFiles = Number(dataset.total_files || files.length);
  const totalTouches = Number(dataset.total_touches ?? files.reduce((sum, file) => sum + fileTouches(file), 0));
  const totalLineChanges = Number(dataset.total_line_changes ?? files.reduce((sum, file) => sum + fileLineChanges(file), 0));
  const totalLinesAdded = Number(dataset.total_additions ?? files.reduce((sum, file) => sum + (file.additions || 0), 0));
  const totalLinesDeleted = Number(dataset.total_deletions ?? files.reduce((sum, file) => sum + (file.deletions || 0), 0));
  const totalCommits = Number(dataset.commit_count || 0);
  const detailedCommits = Number(dataset.detail_commit_count ?? totalCommits);
  if (summary) {
    summary.innerHTML = `
      <span class="gh-summary-pill"><strong>${fmtNum(totalChangedFiles)}</strong> changed files</span>
      <span class="gh-summary-pill"><strong>${fmtNum(totalCommits)}</strong> commits in 24h</span>
      ${detailedCommits > 0 && detailedCommits !== totalCommits ? `<span class="gh-summary-pill"><strong>${fmtNum(detailedCommits)}</strong> commits with file details</span>` : ''}
      <span class="gh-summary-pill"><strong>${fmtNum(totalLineChanges)}</strong> lines changed</span>
      <span class="gh-summary-pill"><strong>${fmtNum(totalTouches)}</strong> touches</span>
      <span class="gh-summary-pill"><strong>+${fmtNum(totalLinesAdded)}</strong> / <strong>-${fmtNum(totalLinesDeleted)}</strong></span>
      ${dataset.ranking_label ? `<span class="gh-summary-pill"><strong>${escapeHtml(dataset.ranking_label)}</strong></span>` : ''}
    `;
  }

  let html = '<div class="gh-file-strip">';
  files.forEach((file, idx) => {
    const path = splitFilePath(file.filename);
    const lineChanges = fileLineChanges(file);
    const touches = fileTouches(file);
    const tooltip = `${file.filename}\n${fmtNum(lineChanges)} lines changed\n${fmtNum(touches)} touches\n+${fmtNum(file.additions || 0)} / -${fmtNum(file.deletions || 0)}`;
    html += `
      <article class="gh-file-card" title="${escapeHtml(tooltip)}">
        <div class="gh-file-card-rank">#${idx + 1}</div>
        <div class="gh-file-card-value">${fmtNum(lineChanges)}</div>
        <div class="gh-file-card-label">lines changed</div>
        <div class="gh-file-card-name">${escapeHtml(path.name)}</div>
        <div class="gh-file-card-dir">${escapeHtml(path.directory)}</div>
        <div class="gh-file-card-metrics">
          <span>+${fmtNum(file.additions || 0)}</span>
          <span>-${fmtNum(file.deletions || 0)}</span>
        </div>
        <div class="gh-file-card-footer">
          <span>${fmtNum(touches)} ${touches === 1 ? 'touch' : 'touches'}</span>
          <span>${escapeHtml(fileStatusSummary(file))}</span>
        </div>
      </article>
    `;
  });
  html += '</div>';

  container.innerHTML = html;
}

// CODE CHURN CHART (Lines added/deleted in the last 24 hours)
function renderCodeChurn(data) {
  const container = document.getElementById('ghCodeChurn');
  const subtitle = document.getElementById('ghCodeChurnSub');
  const summary = document.getElementById('ghCodeChurnSummary');
  if (!container) return;

  const limitedMessage = analyticsLimitedMessage(data);
  if (limitedMessage) {
    if (subtitle) subtitle.textContent = 'Reduced to branch heads';
    if (summary) summary.innerHTML = '';
    container.innerHTML = `<div class="gh-empty">${limitedMessage}</div>`;
    return;
  }

  const churnData = getCodeChurnDataset(data);
  if (subtitle) {
    subtitle.textContent = churnData.scope_label || 'Lines added and deleted in the last 24 hours';
  }
  const additions = Number(churnData.additions || 0);
  const deletions = Number(churnData.deletions || 0);
  const commitCount = Number(churnData.commit_count || 0);
  const detailedCommitCount = Number(churnData.detail_commit_count ?? commitCount);
  if (!churnData || (additions === 0 && deletions === 0 && commitCount === 0)) {
    if (summary) summary.innerHTML = '';
    container.innerHTML = '<div class="gh-empty">No code churn recorded on main in the last 24 hours.</div>';
    return;
  }
  if (commitCount > 0 && detailedCommitCount === 0) {
    if (summary) summary.innerHTML = '';
    container.innerHTML = '<div class="gh-empty">GitHub returned recent commits, but file details were unavailable to calculate 24-hour churn.</div>';
    return;
  }

  const changedFiles = Number(churnData.changed_files || 0);
  const totalLinesChanged = Number(churnData.total_lines_changed ?? (additions + deletions));
  const netChange = Number(churnData.net_change ?? (additions - deletions));

  if (summary) {
    summary.innerHTML = `
      <span class="gh-summary-pill"><strong>${fmtNum(commitCount)}</strong> commits in 24h</span>
      ${detailedCommitCount > 0 && detailedCommitCount !== commitCount ? `<span class="gh-summary-pill"><strong>${fmtNum(detailedCommitCount)}</strong> commits with file details</span>` : ''}
      <span class="gh-summary-pill"><strong>${fmtNum(changedFiles)}</strong> changed files</span>
      <span class="gh-summary-pill"><strong>${fmtNum(totalLinesChanged)}</strong> lines changed</span>
      <span class="gh-summary-pill"><strong>${netChange >= 0 ? '+' : ''}${fmtNum(netChange)}</strong> net</span>
    `;
  }

  const maxLines = Math.max(additions, deletions, 1);
  const additionPct = Math.max(8, Math.round((additions / maxLines) * 100));
  const deletionPct = Math.max(8, Math.round((deletions / maxLines) * 100));

  container.innerHTML = `
    <div class="gh-churn-bars">
      <div class="gh-churn-row">
        <div class="gh-churn-row-head">
          <div class="gh-churn-label">Lines Added</div>
          <div class="gh-churn-value gh-churn-value-added">+${fmtNum(additions)}</div>
        </div>
        <div class="gh-churn-track">
          <div class="gh-churn-fill gh-churn-fill-added" style="width:${additionPct}%"></div>
        </div>
      </div>
      <div class="gh-churn-row">
        <div class="gh-churn-row-head">
          <div class="gh-churn-label">Lines Deleted</div>
          <div class="gh-churn-value gh-churn-value-deleted">-${fmtNum(deletions)}</div>
        </div>
        <div class="gh-churn-track">
          <div class="gh-churn-fill gh-churn-fill-deleted" style="width:${deletionPct}%"></div>
        </div>
      </div>
      <div class="gh-churn-foot">
        <span>Total 24h churn: ${fmtNum(totalLinesChanged)} lines</span>
      </div>
    </div>
  `;
}

// RENDER PULL REQUESTS
function renderPullRequests(container, prs, type) {
  if (!container) return;
  if (!prs || prs.length === 0) {
    const emptyMsg = type === 'open' ? 'No open pull requests.' : 'No merged pull requests yet.';
    container.innerHTML = `<div class="gh-empty">${emptyMsg}</div>`;
    return;
  }
  
  container.innerHTML = '';
  prs.slice(0, 10).forEach(pr => {
    const div = document.createElement('div');
    div.className = 'gh-pr';
    if (pr.state === 'merged' || type === 'merged') {
      div.classList.add('gh-pr-merged');
    } else if (pr.state === 'closed') {
      div.classList.add('gh-pr-closed');
    } else {
      div.classList.add('gh-pr-open');
    }
    
    const statusLabel = pr.state === 'merged' || type === 'merged' ? '✓ Merged' : 
                       pr.state === 'open' ? '◯ Open' : '✕ Closed';
    const statusClass = pr.state === 'merged' || type === 'merged' ? 'gh-pr-status-merged' :
                       pr.state === 'open' ? 'gh-pr-status-open' : 'gh-pr-status-closed';
    
    const avatar = pr.author_avatar ? `<img src="${pr.author_avatar}" alt="${pr.author_login}" class="gh-pr-avatar">` : '';
    const author = pr.author_login || pr.author_name || 'Unknown';
    const authorLink = pr.author_profile_url ? 
      `<a href="${pr.author_profile_url}" target="_blank" rel="noopener" class="gh-pr-author">${author}</a>` :
      `<span class="gh-pr-author">${author}</span>`;
    
    const stats = `<span class="gh-pr-stat">+${pr.additions}</span><span class="gh-pr-stat">-${pr.deletions}</span>`;
    const dateStr = pr.merged_at ? fmtDate(pr.merged_at) : fmtDate(pr.updated_at);
    
    div.innerHTML = `
      <div class="gh-pr-header">
        <span class="gh-pr-number">#${pr.number}</span>
        <a href="${pr.url}" target="_blank" rel="noopener" class="gh-pr-title">${pr.title}</a>
        <span class="${statusClass}">${statusLabel}</span>
      </div>
      <div class="gh-pr-meta">
        ${avatar}
        ${authorLink}
        <span class="gh-pr-date">${dateStr}</span>
        <span class="gh-pr-files">${pr.changed_files} files</span>
        ${stats}
      </div>
    `;
    container.appendChild(div);
  });
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.gh-analytics-btn').forEach((button) => {
      button.addEventListener('click', () => setGitHubAnalyticsGrouping(button.dataset.ghGroup));
    });
    updateGitHubAnalyticsButtons();
    loadGitHub();
});
