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

let _ghAnalyticsPayload = null;
let _ghAnalyticsGrouping = 'month';

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
  return _ghAnalyticsGrouping === 'week' ? 'week' : 'month';
}

function analyticsWindowCount(data) {
  const windowData = data?.analytics_window || {};
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

function getCodeChurnDataset(data) {
  const grouped = data?.code_churn_by_period || {};
  const periods = grouped[_ghAnalyticsGrouping];
  if (Array.isArray(periods) && periods.length) return periods;
  if (_ghAnalyticsGrouping === 'month') return normalizeLegacyMonthChurn(data);
  return [];
}

function getFileChangeDataset(data) {
  const grouped = data?.file_changes_by_period || {};
  const dataset = grouped[_ghAnalyticsGrouping];
  if (dataset && Array.isArray(dataset.items)) return dataset;
  return {
    items: Array.isArray(data?.file_changes) ? data.file_changes : [],
    period_count: analyticsWindowCount(data),
    scope_label: `Top 10 files touched across recent ${analyticsUnitLabel()}s`
  };
}

function setGitHubAnalyticsGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month') return;
  _ghAnalyticsGrouping = grouping;
  updateGitHubAnalyticsButtons();
  if (_ghAnalyticsPayload) {
    renderMostChanged(_ghAnalyticsPayload);
    renderCodeChurn(_ghAnalyticsPayload);
  }
}

// Tag modal functions
function openTagModal(sha, shortSha) {
  const modal = document.getElementById('ghTagModal');
  if (!modal) {
    console.error('Tag modal not found');
    return;
  }
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
    modal.style.display = 'none';
  }
}

async function submitTag() {
  const sha = document.getElementById('tagCommitSha').value;
  const tagName = document.getElementById('tagNameInput').value.trim();
  const message = document.getElementById('tagMessageInput').value.trim();
  const statusEl = document.getElementById('tagStatus');
  
  if (!tagName) {
    statusEl.textContent = 'Please enter a tag name';
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
    const div = document.createElement('div');
    div.className = 'gh-commit';
    div.innerHTML = `
      <div class="gh-commit-sha">${c.short_sha || '--'}</div>
      <div class="gh-commit-body">
        <div class="gh-commit-msg">${(c.message || '').split('\\n')[0]}</div>
        <div class="gh-commit-meta">${c.author_name || 'Unknown'} · ${fmtDate(c.date)}</div>
        <div class="gh-commit-actions">
          ${c.html_url ? `<a class="gh-commit-link" href="${c.html_url}" target="_blank" rel="noopener">View commit</a>` : ''}
          <button class="gh-tag-btn" onclick="openTagModal('${c.sha}', '${c.short_sha}')">Tag</button>
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

    renderCommits(document.getElementById('ghCommits'), data.commits || []);
    renderPullRequests(document.getElementById('ghOpenPRs'), data.pull_requests_open || [], 'open');
    renderPullRequests(document.getElementById('ghMergedPRs'), data.pull_requests_merged || [], 'merged');
    try {
      _ghAnalyticsPayload = data;
      updateGitHubAnalyticsButtons();
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
  console.log('Fix commit data:', c); // Debug log

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
            <span class="gh-build-badge gh-build-badge-success">Fixed</span>
          </div>
        </div>
        <div class="gh-commit-msg gh-commit-msg-success">${displayMsg}</div>
      </div>
      
      <div class="gh-culprit-section gh-culprit-section-success">
        <div class="gh-culprit-label gh-culprit-label-success">Fixed by</div>
        ${userCardHTML}
      </div>
      
      <div class="gh-commit-footer">
        ${fmtDate(c.date) !== '--' ? `<div class="gh-meta">Committed ${fmtDate(c.date)}</div>` : ''}
        ${c.html_url ? `<a href="${c.html_url}" target="_blank" rel="noopener" class="gh-build-link gh-build-link-success">View commit →</a>` : ''}
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

  const failDate = new Date(fc.commit.date);
  const fixDate = new Date(fc.fix_commit.date);

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

  // Determine severity color
  let severity = 'good';
  let severityLabel = 'Excellent';
  if (diffHours >= 24) {
    severity = 'critical';
    severityLabel = 'Critical';
  } else if (diffHours >= 8) {
    severity = 'warning';
    severityLabel = 'Fair';
  } else if (diffHours >= 1) {
    severity = 'caution';
    severityLabel = 'Good';
  }

  container.innerHTML = `
    <div class="ttf-container">
      <div class="ttf-main">
        <div class="ttf-time">${timeStr}</div>
        <div class="ttf-label">From failure to fix</div>
      </div>
      
    </div>
  `;
}

// MOST CHANGED FILES
function renderMostChanged(data) {
  const container = document.getElementById('ghMostChanged');
  if (!container) return;

  const files = data.file_changes;
  if (!files || files.length === 0) {
    container.innerHTML = '<div class="gh-empty">No file changes data available.</div>';
    return;
  }

  // Find max changes for scaling
  const maxChanges = Math.max(...files.map(f => f.changes));

  // Create bar chart HTML
  let html = '<div class="file-bars">';
  files.forEach((file, idx) => {
    const pct = Math.round((file.changes / maxChanges) * 100);
    const bgColor = idx % 2 === 0 ? '#3a7be8' : '#ff8c42';
    const filename = file.filename.length > 45 ? file.filename.substring(0, 42) + '...' : file.filename;
    
    html += `
      <div class="file-bar-row" title="${file.filename}">
        <div class="file-bar-name">${filename}</div>
        <div class="file-bar-container">
          <div class="file-bar" style="width: ${pct}%; background-color: ${bgColor};">
            <span class="file-bar-value">${file.changes}</span>
          </div>
        </div>
      </div>
    `;
  });
  html += '</div>';

  container.innerHTML = html;
}

// CODE CHURN CHART (Lines added/deleted per month)
function renderCodeChurn(data) {
  const container = document.getElementById('ghCodeChurn');
  if (!container) return;

  const churnData = data.code_churn;
  if (!churnData || churnData.length === 0) {
    container.innerHTML = '<div class="gh-empty">No code churn data available.</div>';
    return;
  }

  const lastMonths = churnData.slice(-6);

  const totalAdded = lastMonths.reduce((sum, d) => sum + (d.additions || 0), 0);
  const totalDeleted = lastMonths.reduce((sum, d) => sum + (d.deletions || 0), 0);

  // Chart dimensions
  const width = 520;
  const height = 190;
  const padding = 28;
  const chartWidth = width - 2 * padding;
  const chartHeight = height - 2 * padding - 12;

  // Find max value for scaling
  let maxValue = 0;
  lastMonths.forEach(d => {
    maxValue = Math.max(maxValue, d.additions + d.deletions);
  });
  maxValue = Math.ceil(maxValue / 100) * 100; // Round up to nearest 100
  if (maxValue === 0) maxValue = 100;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.classList.add('churn-svg');

  // Draw bars
  const barWidth = (chartWidth / lastMonths.length) * 0.32;
  const spaceBetweenBars = (chartWidth / lastMonths.length) * 0.12;

  lastMonths.forEach((d, idx) => {
    const x = padding + (idx * (chartWidth / lastMonths.length)) + spaceBetweenBars;
    const addHeight = (d.additions / maxValue) * chartHeight;
    const delHeight = (d.deletions / maxValue) * chartHeight;

    // Additions bar (green) - on the left
    if (d.additions > 0) {
      const addBar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      addBar.setAttribute('x', x - barWidth / 2 - 3);
      addBar.setAttribute('y', padding + chartHeight - addHeight);
      addBar.setAttribute('width', barWidth);
      addBar.setAttribute('height', addHeight);
      addBar.setAttribute('fill', '#22c55e');
      addBar.setAttribute('opacity', '0.85');
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${d.month} - Added: ${fmtNum(d.additions)}`;
      addBar.appendChild(title);
      svg.appendChild(addBar);
    }

    // Deletions bar (red) - on the right
    if (d.deletions > 0) {
      const delBar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      delBar.setAttribute('x', x + barWidth / 2 + 3);
      delBar.setAttribute('y', padding + chartHeight - delHeight);
      delBar.setAttribute('width', barWidth);
      delBar.setAttribute('height', delHeight);
      delBar.setAttribute('fill', '#ef4444');
      delBar.setAttribute('opacity', '0.85');
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${d.month} - Deleted: ${fmtNum(d.deletions)}`;
      delBar.appendChild(title);
      svg.appendChild(delBar);
    }

    // X-axis label (month)
    const monthLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    monthLabel.setAttribute('x', x + spaceBetweenBars / 2);
    monthLabel.setAttribute('y', padding + chartHeight + 16);
    monthLabel.setAttribute('font-size', '10.5');
    monthLabel.setAttribute('fill', 'var(--text2)');
    monthLabel.setAttribute('text-anchor', 'middle');
    // Format month: "2024-03" -> "Mar"
    const [year, month] = d.month.split('-');
    const monthDate = new Date(year, parseInt(month) - 1);
    const monthStr = monthDate.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
    monthLabel.textContent = monthStr;
    svg.appendChild(monthLabel);
  });

  // Axes
  const xAxis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  xAxis.setAttribute('x1', padding);
  xAxis.setAttribute('y1', padding + chartHeight);
  xAxis.setAttribute('x2', width - padding);
  xAxis.setAttribute('y2', padding + chartHeight);
  xAxis.setAttribute('stroke', 'var(--border)');
  xAxis.setAttribute('stroke-width', '1');
  svg.appendChild(xAxis);

  // Legend
  const legendGroup = document.createElement('div');
  legendGroup.className = 'churn-legend';
  legendGroup.innerHTML = `
    <div class="churn-legend-item">
      <div class="churn-legend-dot" style="background: #22c55e;"></div>
      <span>Added ${fmtNum(totalAdded)}</span>
    </div>
    <div class="churn-legend-item">
      <div class="churn-legend-dot" style="background: #ef4444;"></div>
      <span>Deleted ${fmtNum(totalDeleted)}</span>
    </div>
  `;

  container.innerHTML = '';
  container.appendChild(svg);
  container.appendChild(legendGroup);
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
    loadGitHub();
});
