from providers.sonarcloud import get_measures, get_quality_gate_status
from flask import current_app


METRIC_KEYS = [
    'bugs',
    'vulnerabilities',
    'code_smells',
    'coverage',
    'duplicated_lines_density',
    'security_hotspots',
    'ncloc',
]


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
            'bugs': _to_int(metrics.get('bugs')),
            'vulnerabilities': _to_int(metrics.get('vulnerabilities')),
            'code_smells': _to_int(metrics.get('code_smells')),
            'coverage': _to_float(metrics.get('coverage')),
            'duplicated_lines_density': _to_float(metrics.get('duplicated_lines_density')),
            'security_hotspots': _to_int(metrics.get('security_hotspots')),
            'ncloc': _to_int(metrics.get('ncloc')),
        },
    }
