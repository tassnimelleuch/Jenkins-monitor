import logging

import requests
from flask import current_app
from pipeline_identity import normalize_branch_name


logger = logging.getLogger(__name__)
_DOCKERHUB_JWT_CACHE = {}


def _get_base_url():
    return current_app.config.get('DOCKERHUB_API_URL', 'https://hub.docker.com/v2').rstrip('/')


def _get_headers():
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'Jenkins-Monitor',
    }
    token = _get_bearer_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def _get_image_name():
    image_name = (current_app.config.get('DOCKERHUB_IMAGE') or '').strip()
    if image_name.startswith('docker.io/'):
        image_name = image_name[len('docker.io/'):]
    return image_name or None

def _safe_branch_name(branch_name):
    branch = normalize_branch_name(branch_name)
    if not branch:
        return None
    return branch.replace('/', '-').lower()


def _split_image_name(image_name=None):
    name = image_name or _get_image_name()
    if not name or '/' not in name:
        return None, None
    namespace, repository = name.split('/', 1)
    return namespace, repository


def _get_auth_identifier():
    username = (current_app.config.get('DOCKERHUB_USERNAME') or '').strip()
    if username:
        return username
    namespace, _ = _split_image_name()
    return namespace


def _looks_like_jwt(token):
    return bool(token) and token.count('.') == 2


def _get_bearer_token():
    raw_token = (current_app.config.get('DOCKERHUB_TOKEN') or '').strip()
    if not raw_token:
        return None
    if _looks_like_jwt(raw_token):
        return raw_token

    identifier = _get_auth_identifier()
    if not identifier:
        return None

    cache_key = (identifier, raw_token)
    cached = _DOCKERHUB_JWT_CACHE.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.post(
            f'{_get_base_url()}/auth/token',
            json={
                'identifier': identifier,
                'secret': raw_token,
            },
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'Jenkins-Monitor',
            },
            timeout=8,
        )
        if resp.status_code != 200:
            logger.warning('[Docker Hub] Token exchange failed: %s', resp.status_code)
            return None
        data = resp.json()
        access_token = (data or {}).get('access_token')
        if not access_token:
            logger.warning('[Docker Hub] Token exchange returned no access_token.')
            return None
        _DOCKERHUB_JWT_CACHE[cache_key] = access_token
        return access_token
    except Exception as e:
        logger.warning(f'[Docker Hub] Token exchange error: {e}')
        return None


def _get_json(url, params=None, timeout=8):
    headers = _get_headers()
    try:
        resp = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout
        )
        if resp.status_code in (401, 403) and 'Authorization' in headers:
            # Public repositories can still be queried anonymously if auth fails.
            resp = requests.get(
                url,
                params=params,
                headers={
                    'Accept': 'application/json',
                    'User-Agent': 'Jenkins-Monitor',
                },
                timeout=timeout
            )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f'[Docker Hub] JSON fetch error: {e}')
        return None


def _extract_size_mb(tag_data):
    size_bytes = tag_data.get('full_size')
    if not size_bytes:
        images = tag_data.get('images') or []
        if images:
            size_bytes = images[0].get('size')
    if not size_bytes:
        return None
    return round(size_bytes / (1024 * 1024), 1)


def _get_build_tag_suffix(build_number):
    template = current_app.config.get('DOCKERHUB_BUILD_TAG_SUFFIX', 'build-{build_number}')
    try:
        return template.format(build_number=build_number)
    except Exception:
        return f'build-{build_number}'


def _tag_matches_build(tag_name, build_number):
    suffix = _get_build_tag_suffix(build_number)
    return bool(tag_name) and tag_name.endswith(suffix)


def _tag_matches_branch_prefix(tag_name, branch_name):
    safe_branch = _safe_branch_name(branch_name)
    if not safe_branch or not tag_name:
        return False
    normalized_tag = tag_name.strip().lower()
    return normalized_tag == safe_branch or normalized_tag.startswith(f'{safe_branch}-')


def list_repository_tags(image_name=None, page=1, page_size=100):
    namespace, repository = _split_image_name(image_name)
    if not namespace or not repository:
        return None

    url = f'{_get_base_url()}/repositories/{namespace}/{repository}/tags'
    return _get_json(
        url,
        params={
            'page': page,
            'page_size': page_size,
            'ordering': 'last_updated',
        }
    )


def list_recent_repository_tags(image_name=None, max_pages=5, page_size=100):
    all_tags = []
    for page in range(1, max_pages + 1):
        data = list_repository_tags(image_name=image_name, page=page, page_size=page_size)
        results = (data or {}).get('results') or []
        all_tags.extend(results)
        if not results or not (data or {}).get('next'):
            break
    return all_tags


def get_repository_tag(tag=None, image_name=None):
    namespace, repository = _split_image_name(image_name)
    if not namespace or not repository:
        return None

    tag_name = tag or (current_app.config.get('DOCKERHUB_TAG') or '').strip()
    if tag_name:
        url = f'{_get_base_url()}/repositories/{namespace}/{repository}/tags/{tag_name}/'
        return _get_json(url)

    url = f'{_get_base_url()}/repositories/{namespace}/{repository}/tags'
    data = _get_json(url, params={'page_size': 1, 'ordering': 'last_updated'})
    results = (data or {}).get('results') or []
    return results[0] if results else None


def find_repository_tag_for_build(
    build_number,
    image_name=None,
    branch_name=None,
    max_pages=5,
    page_size=100,
    tag_results=None,
):
    def _find_matching_tag(results):
        fallback_match = None
        for tag_data in results:
            tag_name = (tag_data.get('name') or '').strip()
            if not _tag_matches_build(tag_name, build_number):
                continue
            if branch_name and _tag_matches_branch_prefix(tag_name, branch_name):
                return tag_data
            if fallback_match is None:
                fallback_match = tag_data
        return fallback_match

    if tag_results is not None:
        return _find_matching_tag(tag_results)

    for page in range(1, max_pages + 1):
        data = list_repository_tags(image_name=image_name, page=page, page_size=page_size)
        results = (data or {}).get('results') or []
        match = _find_matching_tag(results)
        if match:
            return match
        if not results or not (data or {}).get('next'):
            break
    return None


def build_image_metadata(tag_data, build=None):
    if not tag_data:
        return None

    build_number = build.get('number') if build else None
    build_result = build.get('result') if build else 'Available'
    build_timestamp = build.get('timestamp') if build else None

    return {
        'source': 'Docker Hub',
        'build_number': build_number,
        'image_name': _get_image_name(),
        'tag': tag_data.get('name') or (current_app.config.get('DOCKERHUB_TAG') or None),
        'size_mb': _extract_size_mb(tag_data),
        'result': build_result,
        'status': 'Matched' if build_number else 'Available',
        'timestamp': build_timestamp or tag_data.get('tag_last_pushed') or tag_data.get('last_updated'),
    }


def get_latest_image_metadata(tag=None):
    image_name = _get_image_name()
    if not image_name:
        return None

    tag_data = get_repository_tag(tag=tag, image_name=image_name)
    return build_image_metadata(tag_data)
