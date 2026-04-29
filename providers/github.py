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
