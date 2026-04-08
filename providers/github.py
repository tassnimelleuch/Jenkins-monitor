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


def get_commits(owner, repo, per_page=8):
    url = f"{_get_base_url()}/repos/{owner}/{repo}/commits"
    params = {'per_page': per_page}
    return _get_json(url, params=params)
