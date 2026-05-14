import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)


def _get_base_url():
    return current_app.config.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')


def _get_token():
    return current_app.config.get('GITHUB_TOKEN')


def _get_headers():
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'Jenkins-Monitor',
    }
    token = _get_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def _get_json(url, params=None, timeout=8):
    try:
        resp = requests.get(
            url,
            params=params,
            headers=_get_headers(),
            timeout=timeout
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f'[GitHub] JSON fetch error: {e}')
        return None


def _get_paginated_json(url, base_params=None, per_page=20, timeout=8, page_limit=None, item_label='items'):
    params = dict(base_params or {})
    params['per_page'] = per_page

    all_items = []
    page = 1

    while True:
        page_params = {**params, 'page': page}
        try:
            resp = requests.get(
                url,
                params=page_params,
                headers=_get_headers(),
                timeout=timeout
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            items = resp.json()

            if not items or not isinstance(items, list):
                break

            all_items.extend(items)
            logger.info(f'[GitHub] Page {page}: fetched {len(items)} {item_label}')

            if len(items) < per_page or (page_limit and page >= page_limit):
                break
            page += 1
        except Exception as e:
            logger.warning(f'[GitHub] {item_label.capitalize()} fetch error on page {page}: {e}')
            break

    return all_items if all_items else None


def get_repo(owner, repo):
    url = f"{_get_base_url()}/repos/{owner}/{repo}"
    return _get_json(url)


def get_commits(owner, repo, per_page=8, since=None, until=None):
    """Fetch commits with optional pagination support."""
    return _get_commits(owner, repo, per_page=per_page, since=since, until=until)


def _get_commits(owner, repo, per_page=8, since=None, until=None, sha=None, page_limit=None):
    url = f"{_get_base_url()}/repos/{owner}/{repo}/commits"
    base_params = {}
    if since:
        base_params['since'] = since
    if until:
        base_params['until'] = until
    if sha:
        base_params['sha'] = sha

    logger.info(f"[GitHub] Fetching commits with params: {base_params}")

    all_commits = _get_paginated_json(
        url,
        base_params=base_params,
        per_page=per_page,
        timeout=8,
        page_limit=page_limit,
        item_label='commits'
    )
    logger.info(f"[GitHub] Total commits fetched: {len(all_commits or [])}")
    return all_commits


def get_latest_commit_for_branch(owner, repo, branch_name):
    commits = _get_commits(
        owner,
        repo,
        per_page=1,
        sha=branch_name,
        page_limit=1,
    )
    return commits[0] if commits else None


def get_commits_for_branch(owner, repo, branch_name, per_page=20, page_limit=1, since=None, until=None):
    return _get_commits(
        owner,
        repo,
        per_page=per_page,
        sha=branch_name,
        page_limit=page_limit,
        since=since,
        until=until,
    )


def get_commit(owner, repo, sha):
    url = f"{_get_base_url()}/repos/{owner}/{repo}/commits/{sha}"
    return _get_json(url)


def enrich_commits_with_files(owner, repo, commits, max_commits=20):
    """Enrich commits with file change details by fetching individual commits.
    
    Args:
        owner: GitHub repository owner
        repo: GitHub repository name
        commits: List of commits to enrich (usually from _get_commits)
        max_commits: Maximum number of commits to enrich (API rate limiting)
    
    Returns:
        List of commits enriched with file details from their individual API calls
    """
    if not commits or not isinstance(commits, list):
        return commits
    
    # Limit commits to enrich (to avoid too many API calls)
    commits_to_enrich = commits[:max_commits]
    
    try:
        from services.parallel_executor import parallel_execute
        
        # Create tasks to fetch full commit details
        tasks = {
            f"commit_{i}_{c.get('sha', '')[:7]}": (
                lambda sha=c.get('sha'): get_commit(owner, repo, sha)
            )
            for i, c in enumerate(commits_to_enrich)
            if c.get('sha')
        }
        
        if not tasks:
            return commits
        
        logger.info(f"[GitHub] Enriching {len(tasks)} commits with file details")
        enriched_data = parallel_execute(tasks, max_workers=4, timeout=10)
        
        # Merge enriched data back into commits
        enriched_commits = []
        for commit in commits:
            sha = commit.get('sha')
            # Find the enriched data for this commit
            enriched = None
            for key, value in enriched_data.items():
                if sha and isinstance(value, dict) and value.get('sha') == sha:
                    enriched = value
                    break
            
            if enriched:
                # Merge file and stats data from enriched commit
                merged_commit = dict(commit)
                merged_commit['files'] = enriched.get('files', [])
                merged_commit['stats'] = enriched.get('stats', {})
                enriched_commits.append(merged_commit)
            else:
                enriched_commits.append(commit)
        
        return enriched_commits
    
    except Exception as e:
        logger.warning(f"[GitHub] Failed to enrich commits with files: {e}")
        return commits


def get_branches(owner, repo, per_page=100):
    url = f"{_get_base_url()}/repos/{owner}/{repo}/branches"
    logger.info('[GitHub] Fetching branches')
    branches = _get_paginated_json(
        url,
        per_page=per_page,
        timeout=8,
        item_label='branches'
    )
    logger.info(f"[GitHub] Total branches fetched: {len(branches or [])}")
    return branches


def get_pull_requests(owner, repo, state='all', per_page=20):
    """Fetch pull requests (open, closed, or all)."""
    url = f"{_get_base_url()}/repos/{owner}/{repo}/pulls"
    base_params = {'state': state, 'sort': 'updated', 'direction': 'desc'}
    
    logger.info(f"[GitHub] Fetching pull requests with state={state}")

    all_prs = _get_paginated_json(
        url,
        base_params=base_params,
        per_page=per_page,
        timeout=8,
        item_label='pull requests'
    )
    logger.info(f"[GitHub] Total pull requests fetched: {len(all_prs or [])}")
    return all_prs


def create_tag(owner, repo, tag_name, sha, message=None):
    """Create a git tag on a commit in GitHub.
    
    Args:
        owner: Repository owner
        repo: Repository name
        tag_name: Name of the tag to create
        sha: Commit SHA to tag
        message: Optional message for the tag (if None, creates lightweight tag)
    
    Returns:
        dict with tag info on success, dict with error on failure
    """
    if not tag_name or not sha:
        logger.error('[GitHub] Tag creation failed: missing tag_name or sha')
        return {'error': 'Tag name and commit SHA are required'}
    
    token = _get_token()
    if not token:
        logger.error('[GitHub] Tag creation failed: no GitHub token configured')
        return {'error': 'GitHub token not configured'}
    
    base_url = _get_base_url()
    headers = _get_headers()
    
    try:
        if message:
            # Step 1: Create tag object
            tag_url = f"{base_url}/repos/{owner}/{repo}/git/tags"
            tag_payload = {
                'tag': tag_name,
                'message': message,
                'object': sha,
                'type': 'commit'
            }
            
            tag_resp = requests.post(
                tag_url,
                json=tag_payload,
                headers=headers,
                timeout=8
            )
            
            if not tag_resp.ok:
                error_msg = tag_resp.json().get('message', 'Unknown error')
                logger.error(f'[GitHub] Failed to create tag object: {error_msg}')
                return {'error': f'Failed to create tag: {error_msg}'}
            
            tag_obj = tag_resp.json()
            tag_sha = tag_obj.get('sha')
            
            # Step 2: Create reference
            ref_url = f"{base_url}/repos/{owner}/{repo}/git/refs"
            ref_payload = {
                'ref': f'refs/tags/{tag_name}',
                'sha': tag_sha
            }
        else:
            # Create lightweight tag (directly reference the commit)
            ref_url = f"{base_url}/repos/{owner}/{repo}/git/refs"
            ref_payload = {
                'ref': f'refs/tags/{tag_name}',
                'sha': sha
            }
        
        ref_resp = requests.post(
            ref_url,
            json=ref_payload,
            headers=headers,
            timeout=8
        )
        
        if not ref_resp.ok:
            error_msg = ref_resp.json().get('message', 'Unknown error')
            logger.error(f'[GitHub] Failed to create tag reference: {error_msg}')
            return {'error': f'Failed to create tag reference: {error_msg}'}
        
        result = ref_resp.json()
        logger.info(f'[GitHub] Successfully created tag "{tag_name}" on commit {sha[:7]}')
        return {
            'success': True,
            'tag_name': tag_name,
            'ref': result.get('ref'),
            'message': f'Tag "{tag_name}" created successfully'
        }
        
    except Exception as e:
        logger.error(f'[GitHub] Tag creation exception: {e}')
        return {'error': f'Error creating tag: {str(e)}'}
