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


def get_repo(owner, repo):
    url = f"{_get_base_url()}/repos/{owner}/{repo}"
    return _get_json(url)


def get_commits(owner, repo, per_page=8, since=None, until=None):
    """Fetch all commits with pagination support."""
    url = f"{_get_base_url()}/repos/{owner}/{repo}/commits"
    base_params = {'per_page': per_page}
    if since:
        base_params['since'] = since
    if until:
        base_params['until'] = until
    
    logger.info(f"[GitHub] Fetching commits with params: {base_params}")
    
    all_commits = []
    page = 1
    
    while True:
        params = {**base_params, 'page': page}
        try:
            resp = requests.get(
                url,
                params=params,
                headers=_get_headers(),
                timeout=8
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            commits = resp.json()
            
            if not commits or not isinstance(commits, list):
                break
            
            all_commits.extend(commits)
            logger.info(f"[GitHub] Page {page}: fetched {len(commits)} commits")
            
            # Check if there's a next page
            if len(commits) < per_page:
                break
            page += 1
        except Exception as e:
            logger.warning(f'[GitHub] Commit fetch error on page {page}: {e}')
            break
    
    logger.info(f"[GitHub] Total commits fetched: {len(all_commits)}")
    return all_commits if all_commits else None


def get_commit(owner, repo, sha):
    url = f"{_get_base_url()}/repos/{owner}/{repo}/commits/{sha}"
    return _get_json(url)


def get_pull_requests(owner, repo, state='all', per_page=20):
    """Fetch pull requests (open, closed, or all)."""
    url = f"{_get_base_url()}/repos/{owner}/{repo}/pulls"
    base_params = {'per_page': per_page, 'state': state, 'sort': 'updated', 'direction': 'desc'}
    
    logger.info(f"[GitHub] Fetching pull requests with state={state}")
    
    all_prs = []
    page = 1
    
    while True:
        params = {**base_params, 'page': page}
        try:
            resp = requests.get(
                url,
                params=params,
                headers=_get_headers(),
                timeout=8
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            prs = resp.json()
            
            if not prs or not isinstance(prs, list):
                break
            
            all_prs.extend(prs)
            logger.info(f"[GitHub] Page {page}: fetched {len(prs)} pull requests")
            
            if len(prs) < per_page:
                break
            page += 1
        except Exception as e:
            logger.warning(f'[GitHub] Pull request fetch error on page {page}: {e}')
            break
    
    logger.info(f"[GitHub] Total pull requests fetched: {len(all_prs)}")
    return all_prs if all_prs else None


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
