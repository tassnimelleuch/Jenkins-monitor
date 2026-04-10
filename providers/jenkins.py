#providers are external service connectors
import logging
import xml.etree.ElementTree as ET
import requests
from flask import current_app

from collections import defaultdict
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


def _get_auth():
    return (
        current_app.config['JENKINS_USERNAME'],
        current_app.config['JENKINS_TOKEN']
    )


def _get_base():
    url = current_app.config['JENKINS_URL'].rstrip('/')
    job = current_app.config['JENKINS_JOB']
    return f"{url}/job/{job}"


def _get_root():
    return current_app.config['JENKINS_URL'].rstrip('/')


def _get_artifact_paths():
    coverage = current_app.config.get('JENKINS_COVERAGE_ARTIFACT', 'coverage.xml')
    junit = current_app.config.get('JENKINS_JUNIT_ARTIFACT', 'junit-results.xml')
    return coverage, junit


def _get_text(url, timeout=8):
    try:
        resp = requests.get(
            url,
            auth=_get_auth(),
            timeout=timeout
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f'[Jenkins] Text fetch error: {e}')
        return None

def _get_json(url, timeout=8):
    try:
        resp = requests.get(
            url,
            auth=_get_auth(),
            timeout=timeout
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f'[Jenkins] JSON fetch error: {e}')
        return None


def _get_crumb_header():
    try:
        resp = requests.get(
            f'{_get_root()}/crumbIssuer/api/json',
            auth=_get_auth(),
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return {data['crumbRequestField']: data['crumb']}
    except Exception as e:
        logger.warning(f'[Jenkins] Could not fetch crumb: {e}')
    return {}


def check_connection():
    try:
        resp = requests.get(
            f'{_get_base()}/api/json?tree=nodeName',
            auth=_get_auth(),
            timeout=5
        )
        return resp.status_code == 200
    except requests.exceptions.ConnectionError:
        return False

def get_all_builds():
    try:
        resp = requests.get(
            f'{_get_base()}/api/json?tree=builds[number,status,timestamp,duration,result]',
            auth=_get_auth(),
            timeout=10
        )
        resp.raise_for_status()
        return resp.json().get('builds', [])
    except Exception as e:
        logger.error(f'[Jenkins] get_all_builds error: {e}')
        return None


def get_last_n_finished(n=10, builds=None):
    if builds is None:
        builds = get_all_builds()
    if not builds:
        return []
    finished = [b for b in builds if b.get('result') is not None]
    return finished[:n]


def get_running_builds(builds=None):
    if builds is None:
        builds = get_all_builds()
    if not builds:
        return []
    return [b for b in builds if b.get('result') is None]


def get_health_score():
    try:
        resp = requests.get(
            f'{_get_base()}/api/json?tree=healthReport[score,description]',
            auth=_get_auth(),
            timeout=10
        )
        resp.raise_for_status()
        reports = resp.json().get('healthReport', [])
        return reports[0].get('score', 0) if reports else 0
    except Exception as e:
        logger.error(f'[Jenkins] get_health_score error: {e}')
        return 0


def get_console_log(build_number):
    try:
        resp = requests.get(
            f'{_get_base()}/{build_number}/consoleText',
            auth=_get_auth(),
            timeout=30
        )
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code == 404:
            return f'[ERROR] Build #{build_number} not found.'
        else:
            return f'[ERROR] Jenkins returned {resp.status_code}'
    except requests.exceptions.ConnectionError:
        return '[ERROR] Cannot connect to Jenkins.'
    except Exception as e:
        return f'[ERROR] {str(e)}'


def trigger_build():
    try:
        resp = requests.post(
            f'{_get_base()}/build',
            auth=_get_auth(),
            headers=_get_crumb_header(),
            timeout=10
        )
        if resp.status_code in (200, 201):
            return True, 'Build queued successfully'
        else:
            return False, f'Jenkins returned {resp.status_code}'
    except requests.exceptions.ConnectionError:
        return False, 'Cannot connect to Jenkins'
    except Exception as e:
        return False, str(e)


def abort_build(build_number):
    try:
        resp = requests.post(
            f'{_get_base()}/{build_number}/stop',
            auth=_get_auth(),
            headers=_get_crumb_header(),
            timeout=10
        )
        if resp.status_code in (200, 201, 302):
            return True, f'Build #{build_number} aborted'
        else:
            return False, f'Jenkins returned {resp.status_code}'
    except requests.exceptions.ConnectionError:
        return False, 'Cannot connect to Jenkins'
    except Exception as e:
        return False, str(e)


def get_stages(build_number):
    try:
        resp = requests.get(
            f'{_get_base()}/{build_number}/wfapi/describe',
            auth=_get_auth(),
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                'name': s.get('name'),
                'status': s.get('status'),
                'duration_ms': s.get('durationMillis', 0),
                'start_time': s.get('startTimeMillis', 0),
            }
            for s in data.get('stages', [])
        ]
    except Exception as e:
        logger.error(f'[Jenkins] get_stages error: {e}')
        return []


def get_running_stages():
    running = get_running_builds()
    if not running:
        return []
    result = []
    for b in running:
        num = b.get('number')
        stages = get_stages(num)
        result.append({
            'number': num,
            'timestamp': b.get('timestamp', 0),
            'stages': stages,
        })
    return result


def _normalize_pct(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 1:
        v *= 100
    return round(v, 1)


def _extract_coverage_percent(data):
    if not isinstance(data, (dict, list)):
        return None

    def extract_from_dict(d):
        for key in ('percentage', 'ratio'):
            if key in d:
                return _normalize_pct(d.get(key))
        if 'covered' in d and 'total' in d and d.get('total'):
            return _normalize_pct(d.get('covered') / d.get('total') * 100)
        return None

    def search(obj, depth=0):
        if depth > 4:
            return None
        if isinstance(obj, dict):
            for key in ('lineCoverage', 'line_coverage', 'line', 'lines'):
                if key in obj:
                    val = obj.get(key)
                    if isinstance(val, dict):
                        pct = extract_from_dict(val)
                        if pct is not None:
                            return pct
                    else:
                        pct = _normalize_pct(val)
                        if pct is not None:
                            return pct

            name = str(obj.get('name', '')).lower()
            if name in ('line', 'lines', 'line coverage', 'linecoverage'):
                pct = extract_from_dict(obj)
                if pct is not None:
                    return pct

            if 'results' in obj:
                res = obj.get('results')
                pct = search(res, depth + 1)
                if pct is not None:
                    return pct

            if 'elements' in obj:
                pct = search(obj.get('elements'), depth + 1)
                if pct is not None:
                    return pct

            for v in obj.values():
                pct = search(v, depth + 1)
                if pct is not None:
                    return pct
        elif isinstance(obj, list):
            for item in obj:
                pct = search(item, depth + 1)
                if pct is not None:
                    return pct
        return None

    return search(data)


def get_coverage_percent(build_number):
    endpoints = (
        'coverage/api/json',
        'cobertura/api/json',
        'jacoco/api/json',
    )
    for ep in endpoints:
        data = _get_json(f'{_get_base()}/{build_number}/{ep}')
        pct = _extract_coverage_percent(data)
        if pct is not None:
            return pct
    coverage_path, _ = _get_artifact_paths()
    xml_text = _get_text(f'{_get_base()}/{build_number}/artifact/{coverage_path}')
    pct = _extract_coverage_percent_from_xml(xml_text) if xml_text else None
    if pct is not None:
        return pct
    return None


def get_test_report(build_number):
    data = _get_json(f'{_get_base()}/{build_number}/artifact/coverage.xml')
    if not data:
        _, junit_path = _get_artifact_paths()
        xml_text = _get_text(f'{_get_base()}/{build_number}/artifact/{junit_path}')
        return _extract_junit_from_xml(xml_text) if xml_text else None
    total = data.get('totalCount')
    failed = data.get('failCount', 0)
    skipped = data.get('skipCount', 0)
    if total is None:
        return None
    passed = max(total - failed - skipped, 0)
    return {
        'total': total,
        'passed': passed,
        'failed': failed,
        'skipped': skipped,
    }


def _extract_coverage_percent_from_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    # Cobertura: <coverage line-rate="0.83" branch-rate="...">
    line_rate = root.attrib.get('line-rate')
    if line_rate is not None:
        return _normalize_pct(line_rate)

    # JaCoCo report: <report><counter type="LINE" missed="" covered=""></counter>
    counters = root.findall('.//counter')
    for c in counters:
        if c.attrib.get('type') == 'LINE':
            missed = c.attrib.get('missed')
            covered = c.attrib.get('covered')
            try:
                missed = int(missed)
                covered = int(covered)
            except (TypeError, ValueError):
                continue
            total = missed + covered
            if total > 0:
                return round(covered / total * 100, 1)

    return None


def _extract_junit_from_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    def parse_suite(node):
        try:
            tests = int(node.attrib.get('tests', 0))
            failures = int(node.attrib.get('failures', 0))
            errors = int(node.attrib.get('errors', 0))
            skipped = int(node.attrib.get('skipped', 0))
        except (TypeError, ValueError):
            return 0, 0, 0, 0
        return tests, failures, errors, skipped

    total = failed = skipped = 0
    if root.tag == 'testsuite':
        tests, failures, errors, skip = parse_suite(root)
        total += tests
        failed += failures + errors
        skipped += skip
    elif root.tag == 'testsuites':
        for suite in root.findall('.//testsuite'):
            tests, failures, errors, skip = parse_suite(suite)
            total += tests
            failed += failures + errors
            skipped += skip

    if total <= 0:
        return None
    passed = max(total - failed - skipped, 0)
    return {
        'total': total,
        'passed': passed,
        'failed': failed,
        'skipped': skipped,
    }

DEPLOY_STAGE = 'Deploy to AKS'
ROLLOUT_STAGE = 'Wait for AKS Rollout'


def _stage_status_map(stages):
    return {
        (s.get('name') or '').strip(): (s.get('status') or '').strip().upper()
        for s in (stages or [])
    }


def _day_key(timestamp_ms):
    if not timestamp_ms:
        return None
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.strftime('%Y-%m-%d')


def get_pipeline_kpis():
    all_builds = get_all_builds()
    if all_builds is None:
        return {'connected': False}

    builds_data = []
    for b in all_builds[:50]:
        num = b.get('number')
        stages = get_stages(num) if num else []
        builds_data.append({
            'number': num,
            'result': b.get('result'),
            'duration': b.get('duration', 0) // 1000 if b.get('duration') else 0,
            'timestamp': b.get('timestamp', 0),
            'stages': stages,
        })

    finished = [b for b in builds_data if b['result'] is not None]

    durations = [b['duration'] for b in finished if b['duration'] > 0]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    stage_failures = {}
    stage_totals = {}
    for b in finished:
        for stage in b.get('stages', []):
            stage_name = stage.get('name', 'Unknown')
            stage_totals[stage_name] = stage_totals.get(stage_name, 0) + 1
            if stage.get('status') == 'FAILED':
                stage_failures[stage_name] = stage_failures.get(stage_name, 0) + 1

    failure_rate_by_stage = {}
    for stage_name, count in stage_totals.items():
        failures = stage_failures.get(stage_name, 0)
        failure_rate_by_stage[stage_name] = round((failures / count * 100), 1) if count > 0 else 0

    finished_recent = finished[:20]
    trend_builds = list(reversed(finished_recent))
    coverage_trend = []
    junit_trend = []
    coverage_vals = []

    for b in trend_builds:
        num = b.get('number')
        coverage = get_coverage_percent(num) if num else None
        if coverage is not None:
            coverage_vals.append(coverage)

        coverage_trend.append({
            'number': num,
            'coverage': coverage,
        })

        report = get_test_report(num) if num else None
        if report:
            junit_trend.append({
                'number': num,
                **report,
            })
        else:
            junit_trend.append({
                'number': num,
                'total': None,
                'passed': None,
                'failed': None,
                'skipped': None,
            })

    avg_test_coverage = round(sum(coverage_vals) / len(coverage_vals), 1) if coverage_vals else None

    # ── ONE deployment metric: successful deployment frequency ──
    successful_deployments_per_day = defaultdict(int)

    for b in finished:
        stage_map = _stage_status_map(b.get('stages', []))
        deploy_ok = stage_map.get(DEPLOY_STAGE) == 'SUCCESS'
        rollout_ok = stage_map.get(ROLLOUT_STAGE) == 'SUCCESS'

        if deploy_ok and rollout_ok:
            day = _day_key(b.get('timestamp'))
            if day:
                successful_deployments_per_day[day] += 1

    successful_deployment_frequency = [
        {'date': day, 'count': count}
        for day, count in sorted(successful_deployments_per_day.items())
    ]

    return {
        'connected': True,
        'builds': builds_data,
        'health_score': get_health_score(),
        'avg_duration_seconds': avg_duration,
        'failure_rate_by_stage': failure_rate_by_stage,
        'build_durations': [(b['number'], b['duration']) for b in finished[-20:]],
        'avg_test_coverage': avg_test_coverage,
        'coverage_trend': coverage_trend,
        'junit_trend': junit_trend,

        'successful_deployment_frequency': successful_deployment_frequency,
    }


def get_build_info(build_number):
    return _get_json(
        f'{_get_base()}/{build_number}/api/json?tree='
        'number,result,timestamp,duration,url,'
        'actions[lastBuiltRevision[SHA1,branch[name]],parameters[name,value]],'
        'changeSets[items[commitId,msg,author[fullName]]],'
        'culprits[fullName,absoluteUrl]'
    )


def extract_build_commit_sha(build_info):
    if not isinstance(build_info, dict):
        return None

    actions = build_info.get('actions') or []
    for action in actions:
        if not isinstance(action, dict):
            continue

        rev = action.get('lastBuiltRevision') or {}
        sha = rev.get('SHA1')
        if sha:
            return sha

        for param in action.get('parameters') or []:
            if param.get('name') == 'GIT_COMMIT' and param.get('value'):
                return param.get('value')

    change_sets = build_info.get('changeSets') or []
    for cs in change_sets:
        for item in cs.get('items') or []:
            sha = item.get('commitId')
            if sha:
                return sha

    return None


def extract_build_commits(build_info, limit=5):
    if not isinstance(build_info, dict):
        return []

    commits = []
    seen = set()
    change_sets = build_info.get('changeSets') or []
    for cs in change_sets:
        for item in cs.get('items') or []:
            sha = item.get('commitId')
            if not sha or sha in seen:
                continue
            seen.add(sha)
            commits.append({
                'sha': sha,
                'message': item.get('msg'),
                'author_name': (item.get('author') or {}).get('fullName'),
            })
            if len(commits) >= limit:
                return commits
    return commits


def extract_build_culprits(build_info, limit=3):
    if not isinstance(build_info, dict):
        return []

    culprits = []
    seen = set()
    for c in build_info.get('culprits') or []:
        name = c.get('fullName')
        if not name or name in seen:
            continue
        seen.add(name)
        culprits.append({
            'full_name': name,
            'url': c.get('absoluteUrl'),
        })
        if len(culprits) >= limit:
            break
    return culprits


def get_last_failed_build(builds=None):
    if builds is None:
        builds = get_all_builds()
    if not builds:
        return None

    for b in builds:
        if b.get('result') == 'FAILURE':
            return b
    return None
