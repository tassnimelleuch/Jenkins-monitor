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
    const data = await res.json();

    if (!data.connected) {
      document.getElementById('ghRepoName').textContent = data.message || 'GitHub unavailable';
      renderCommits(document.getElementById('ghCommits'), []);
      return;
    }

    const repo = data.repo_info || {};
    const full = repo.full_name || `${data.owner}/${data.repo}`;

    document.getElementById('ghRepoName').textContent = full;
    document.getElementById('ghRepoDesc').textContent = repo.description || '—';
    document.getElementById('ghStars').textContent = fmtNum(repo.stars);
    document.getElementById('ghForks').textContent = fmtNum(repo.forks);
    document.getElementById('ghIssues').textContent = fmtNum(repo.open_issues);
    document.getElementById('ghBranch').textContent = repo.default_branch || '—';
    document.getElementById('ghLang').textContent = repo.language || '—';
    document.getElementById('ghUpdated').textContent = fmtDate(repo.updated_at);

    const link = document.getElementById('ghRepoLink');
    if (link && repo.html_url) link.href = repo.html_url;

    renderCommits(document.getElementById('ghCommits'), data.commits || []);

    if (Array.isArray(data.commits) && data.commits[0] && data.commits[0].sha) {
      localStorage.setItem('gh-last-seen', data.commits[0].sha);
    }
  } catch (e) {
    document.getElementById('ghRepoName').textContent = 'Failed to load GitHub data';
    renderCommits(document.getElementById('ghCommits'), []);
  }
}

document.addEventListener('DOMContentLoaded', loadGitHub);
