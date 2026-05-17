from urllib.parse import quote

from flask import current_app

from pipeline_identity import (
    configured_branch_name,
    configured_pipeline_job_path,
    job_path_segments,
    pipeline_name,
)
from services.jenkins_service import get_pipeline_kpis


def _build_branch_job_url(branch_name):
    root = (current_app.config.get('JENKINS_URL') or '').rstrip('/')
    pipeline_job = configured_pipeline_job_path(current_app.config, default_branch='main')
    segments = job_path_segments(pipeline_job)
    clean_branch_name = (branch_name or '').strip()

    if not root or not segments or not clean_branch_name:
        return None

    path = ''.join(
        f"/job/{quote(segment, safe='')}"
        for segment in [*segments, clean_branch_name]
    )
    return f'{root}{path}/'


def _build_branch_build_url(job_url, build):
    build_number = (build or {}).get('number')
    clean_job_url = (job_url or '').rstrip('/')
    if not clean_job_url or build_number is None:
        return None
    return f'{clean_job_url}/{build_number}/'


def _serialize_branch_details(branch_name, branch_payload, kpi_branch_name):
    branch_payload = branch_payload or {}
    links = branch_payload.get('links') or {}
    last_build = branch_payload.get('last_build') or {}
    last_completed_build = branch_payload.get('last_completed_build') or {}
    job_url = links.get('job_url') or _build_branch_job_url(branch_name)

    return {
        'name': branch_name,
        'job_url': job_url,
        'is_kpi_source': branch_name == kpi_branch_name,
        'health_score': (branch_payload.get('summary') or {}).get('health_score'),
        'is_building': bool((branch_payload.get('status') or {}).get('building')),
        'latest_build': {
            **last_build,
            'url': _build_branch_build_url(job_url, last_build),
        } if last_build else None,
        'latest_completed_build': {
            **last_completed_build,
            'url': _build_branch_build_url(job_url, last_completed_build),
        } if last_completed_build else None,
    }


def get_pipeline_details_summary():
    payload = get_pipeline_kpis() or {}
    if not payload.get('connected'):
        return {
            'connected': False,
            'message': (
                'Could not fetch pipeline details from Jenkins. '
                'Verify JENKINS_URL, JENKINS_JOB, and credentials.'
            ),
        }

    pipeline = payload.get('pipeline') or {}
    branches_payload = payload.get('branches') or {}
    if not branches_payload:
        return {
            'connected': False,
            'message': 'No pipeline branches are available right now.',
        }

    branch_names = list(branches_payload)
    configured_main_branch = configured_branch_name(current_app.config, default='main')
    kpi_branch_name = (
        pipeline.get('selected_branch')
        or ('main' if 'main' in branches_payload else configured_main_branch)
        or next(iter(branches_payload), 'main')
    )
    multibranch = (
        pipeline.get('type') == 'multibranch'
        or len(branches_payload) > 1
    )

    branches = [
        _serialize_branch_details(branch_name, branch_payload, kpi_branch_name)
        for branch_name, branch_payload in branches_payload.items()
    ]

    return {
        'connected': True,
        'job': {
            'name': pipeline.get('name') or pipeline_name(current_app.config.get('JENKINS_JOB')),
            'display_name': pipeline.get('name') or pipeline_name(current_app.config.get('JENKINS_JOB')),
        },
        'pipeline': {
            'type': pipeline.get('type') or ('multibranch' if multibranch else 'single-branch'),
            'multibranch': multibranch,
            'kpi_branch': kpi_branch_name,
            'branch_count': len(branch_names),
            'branch_names': branch_names,
            'kpi_note': f'Pipeline KPIs are collected from the {kpi_branch_name} branch only.',
            'job_class': None,
            'definition_class': None,
            'script_path': None,
        },
        'build_discarder': {
            'num_to_keep': None,
        },
        'triggers': [],
        'parameters': [],
        'branches': branches,
    }
