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
        ${c.html_url ? `<a class="gh-commit-link" href="${c.html_url}" target="_blank" rel="noopener">View commit</a>` : ''}
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
    try {
      renderFailingCommit(data);
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

  container.innerHTML = `
    <div class="gh-commit gh-commit-failing">
      <div class="gh-commit-top">
        <a href="${c.html_url}" target="_blank" rel="noopener" class="gh-sha">${c.short_sha || '--'}</a>
        <span class="gh-stat-val">Build #${fc.build_number}</span>
      </div>
      <div class="gh-commit-msg">${displayMsg}</div>
      <div class="gh-meta">
        ${ghUser ? `GitHub user: @${ghUser}` : 'GitHub user: Unknown'}
      </div>
      ${fc.build_url ? `<div class="gh-meta"><a href="${fc.build_url}" target="_blank" rel="noopener">Open failed Jenkins build</a></div>` : ''}
    </div>
  `;
}

document.addEventListener('DOMContentLoaded', loadGitHub);
