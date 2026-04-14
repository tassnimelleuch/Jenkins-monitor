import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)


def _get_base_url():
    return current_app.config.get('SONARCLOUD_BASE_URL', 'https://sonarcloud.io/api').rstrip('/')


def _get_project_key():
    return current_app.config.get('SONARCLOUD_PROJECT_KEY')


def _get_token():
    return current_app.config.get('SONARCLOUD_TOKEN')


def _get_headers():
    token = _get_token()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}


def _get_json(url, params=None, timeout=8):
    try:
        resp = requests.get(
            url,
            params=params,
            headers=_get_headers(),
            timeout=timeout,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f'[SonarCloud] JSON fetch error: {e}')
        return None


def get_measures(metric_keys, project_key=None):
    key = project_key or _get_project_key()
    if not key:
        return None

    url = f"{_get_base_url()}/measures/component"
    params = {
        'component': key,
        'metricKeys': ','.join(metric_keys),
    }
    return _get_json(url, params=params)


def get_quality_gate_status(project_key=None):
    key = project_key or _get_project_key()
    if not key:
        return None

    url = f"{_get_base_url()}/qualitygates/project_status"
    params = {'projectKey': key}
    return _get_json(url, params=params)


def search_issues(
    project_key=None,
    issue_type=None,
    severity=None,
    page=1,
    page_size=20,
):
    key = project_key or _get_project_key()
    if not key:
        return None

    url = f"{_get_base_url()}/issues/search"
    params = {
        'projects': key,
        'p': page,
        'ps': page_size,
        'resolved': 'false',
    }

    if issue_type:
        params['types'] = issue_type
    if severity:
        params['severities'] = severity

    return _get_json(url, params=params)