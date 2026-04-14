
from providers.sonarcloud import (
    get_measures,
    get_quality_gate_status,
    search_issues,
)
from flask import current_app


METRIC_KEYS = [
    'vulnerabilities',
    'code_smells',
    'duplicated_lines_density',
    'security_hotspots',
    'ncloc',
]


BUG_SEVERITY_MAP = {
    'low': ['MINOR', 'INFO'],
    'medium': ['MAJOR'],
    'high': ['CRITICAL', 'BLOCKER'],
}


def _to_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val):
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return None


def _measures_map(raw):
    if not raw or 'component' not in raw:
        return {}
    measures = raw.get('component', {}).get('measures', []) or []
    return {m.get('metric'): m.get('value') for m in measures}


def _bug_count_for_severities(project_key, severities):
    total = 0
    for sev in severities:
        data = search_issues(
            project_key=project_key,
            issue_type='BUG',
            severity=sev,
            page=1,
            page_size=1,  # only need paging metadata / total
        )
        if not data:
            continue
        total += data.get('paging', {}).get('total', 0)
    return total


def get_sonarcloud_summary():
    project_key = current_app.config.get('SONARCLOUD_PROJECT_KEY')
    if not project_key:
        return {
            'connected': False,
            'message': 'SonarCloud is not configured. Set SONARCLOUD_PROJECT_KEY.',
        }

    measures_raw = get_measures(METRIC_KEYS, project_key=project_key)
    gate_raw = get_quality_gate_status(project_key=project_key)

    if measures_raw is None and gate_raw is None:
        return {
            'connected': False,
            'message': 'Unable to fetch SonarCloud data.',
        }

    metrics = _measures_map(measures_raw)

    gate = gate_raw.get('projectStatus', {}) if gate_raw else {}
    conditions = gate.get('conditions', []) or []
    failing = [c for c in conditions if c.get('status') == 'ERROR']

    bugs_by_severity = {
        level: _bug_count_for_severities(project_key, severities)
        for level, severities in BUG_SEVERITY_MAP.items()
    }

    return {
        'connected': True,
        'project_key': project_key,
        'quality_gate': {
            'status': gate.get('status'),
            'failed': len(failing),
            'conditions': [
                {
                    'metric': c.get('metricKey'),
                    'status': c.get('status'),
                    'value': c.get('actualValue'),
                    'threshold': c.get('errorThreshold'),
                }
                for c in conditions
            ],
        },
        'metrics': {
            'bugs': bugs_by_severity,
            'vulnerabilities': _to_int(metrics.get('vulnerabilities')),
            'code_smells': _to_int(metrics.get('code_smells')),
            'duplicated_lines_density': _to_float(metrics.get('duplicated_lines_density')),
            'security_hotspots': _to_int(metrics.get('security_hotspots')),
            'ncloc': _to_int(metrics.get('ncloc')),
        },
    }


def get_bug_details(level=None, page=1, page_size=20):
    project_key = current_app.config.get('SONARCLOUD_PROJECT_KEY')
    if not project_key:
        return {
            'connected': False,
            'message': 'SonarCloud is not configured.',
            'issues': [],
        }

    severities = BUG_SEVERITY_MAP.get(level, [])
    if not severities:
        return {
            'connected': True,
            'issues': [],
            'paging': {'pageIndex': page, 'pageSize': page_size, 'total': 0},
        }

    collected = []
    total = 0

    for sev in severities:
        data = search_issues(
            project_key=project_key,
            issue_type='BUG',
            severity=sev,
            page=1,
            page_size=100,
        )
        if not data:
            continue

        total += data.get('paging', {}).get('total', 0)

        for issue in data.get('issues', []):
            collected.append({
                'key': issue.get('key'),
                'rule': issue.get('rule'),
                'severity': issue.get('severity'),
                'message': issue.get('message'),
                'component': issue.get('component'),
                'line': issue.get('line'),
                'status': issue.get('status'),
                'author': issue.get('author'),
                'creation_date': issue.get('creationDate'),
                'update_date': issue.get('updateDate'),
                'tags': issue.get('tags', []),
                'clean_code_attribute': issue.get('cleanCodeAttribute'),
                'impacts': issue.get('impacts', []),
            })

    severity_order = {
        'BLOCKER': 0,
        'CRITICAL': 1,
        'MAJOR': 2,
        'MINOR': 3,
        'INFO': 4,
    }
    collected.sort(key=lambda x: severity_order.get(x['severity'], 999))

    start = (page - 1) * page_size
    end = start + page_size

    return {
        'connected': True,
        'level': level,
        'paging': {
            'pageIndex': page,
            'pageSize': page_size,
            'total': len(collected),
        },
        'issues': collected[start:end],
    }


def get_issue_details(issue_type=None, page=1, page_size=20, severity=None):
    project_key = current_app.config.get('SONARCLOUD_PROJECT_KEY')
    if not project_key:
        return {
            'connected': False,
            'message': 'SonarCloud is not configured.',
            'issues': [],
        }

    data = search_issues(
        project_key=project_key,
        issue_type=issue_type,
        severity=severity,
        page=page,
        page_size=page_size,
    )

    if not data:
        return {
            'connected': False,
            'message': 'Unable to fetch SonarCloud issues.',
            'issues': [],
        }

    issues = []
    for issue in data.get('issues', []):
        issues.append({
            'key': issue.get('key'),
            'rule': issue.get('rule'),
            'severity': issue.get('severity'),
            'message': issue.get('message'),
            'component': issue.get('component'),
            'line': issue.get('line'),
            'status': issue.get('status'),
            'author': issue.get('author'),
            'creation_date': issue.get('creationDate'),
            'update_date': issue.get('updateDate'),
            'tags': issue.get('tags', []),
            'clean_code_attribute': issue.get('cleanCodeAttribute'),
            'impacts': issue.get('impacts', []),
        })

    severity_order = {
        'BLOCKER': 0,
        'CRITICAL': 1,
        'MAJOR': 2,
        'MINOR': 3,
        'INFO': 4,
    }
    issues.sort(key=lambda x: severity_order.get(x['severity'], 999))

    return {
        'connected': True,
        'issue_type': issue_type,
        'severity': severity,
        'paging': data.get('paging', {'pageIndex': page, 'pageSize': page_size, 'total': len(issues)}),
        'issues': issues,
    }
