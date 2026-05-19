from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from dashboard_kpi_documents_models import (
    DashboardKpiDocument,
    DashboardKpiDocumentChunk,
)
from extensions import db
from pipeline_identity import (
    configured_branch_name,
    configured_pipeline_job_path,
    normalize_job_path,
    pipeline_name,
)
from services.rag_base_service import (
    get_chunking_config as build_chunking_config,
    normalize_text as _normalize_text,
    split_text_into_chunks,
    tokenize as _tokenize,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _coerce_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _render_content(*, intro, represents=None, calculation=None, notes=None):
    sections = [str(intro or '').strip()]
    for heading, items in (
        ('What it represents', represents or []),
        ('How the dashboard calculates or renders it', calculation or []),
        ('Notes', notes or []),
    ):
        clean_items = [str(item).strip() for item in items if str(item or '').strip()]
        if not clean_items:
            continue
        sections.append('')
        sections.append(f'{heading}:')
        sections.extend(f'- {item}' for item in clean_items)
    return '\n'.join(sections).strip()


def _get_chunking_config():
    return build_chunking_config(
        chunk_size_key='DASHBOARD_KPI_CHUNK_SIZE',
        chunk_overlap_key='DASHBOARD_KPI_CHUNK_OVERLAP',
        default_chunk_size=1100,
        default_chunk_overlap=140,
        min_chunk_size=250,
    )


def _resolve_context():
    branch_name = configured_branch_name(current_app.config, default='main') or 'main'
    job_path = normalize_job_path(
        configured_pipeline_job_path(current_app.config, default_branch=branch_name)
    )
    pipeline_label = pipeline_name(current_app.config.get('JENKINS_JOB'), branch_name=branch_name)

    return {
        'pipeline_job_path': job_path,
        'pipeline_name': pipeline_label,
        'branch_name': branch_name,
    }


def _document(
    *,
    context,
    document_key,
    dashboard_page,
    title,
    content,
    tags,
    aliases,
    time_window,
    aggregation,
    source_endpoints=None,
    source_files=None,
):
    return {
        'document_key': document_key,
        'dashboard_page': dashboard_page,
        'title': title,
        'content': content,
        'summary': {
            'kind': 'dashboard_kpi_explanation',
            'value_mode': 'definition_only',
            'tags': list(tags or []),
            'aliases': list(aliases or []),
            'time_window': time_window,
            'aggregation': aggregation,
            'source_endpoints': list(source_endpoints or []),
            'source_files': list(source_files or []),
        },
    }


def _build_volume_document(context):
    content = _render_content(
        intro=(
            'This document explains the build count cards displayed on the Overview and '
            'Pipeline KPIs pages for the selected Jenkins branch.'
        ),
        represents=[
            'These cards summarize build volume and final-result categories for the selected pipeline branch.',
            'The Total Builds card represents the population of finished builds included by the dashboard.',
            'The Successful, Failed, and Aborted cards split that same finished-build population by final result.',
        ],
        calculation=[
            'Only finished builds are counted in these cards.',
            "Total Builds = count of builds with a final result.",
            "Successful = count of builds whose result is 'SUCCESS'.",
            "Failed = count of builds whose result is 'FAILURE'.",
            "Aborted = count of builds whose result is 'ABORTED'.",
            'Running builds are excluded from these cards and are represented separately through running-build widgets.',
        ],
        notes=[
            'This is a definition-only document. It explains the KPI meaning and formula, not the current numeric values on a specific dashboard snapshot.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.build_volume_summary',
        dashboard_page='shared',
        title='Build Volume Summary',
        content=content,
        tags=[
            'total builds',
            'successful builds',
            'failed builds',
            'aborted builds',
            'build count',
            'overview stats',
        ],
        aliases=[
            'total builds',
            'successful',
            'failed',
            'aborted',
            'build counts',
            'stat cards',
        ],
        time_window='all stored finished builds for the selected branch',
        aggregation='result-based counts over finished PipelineMainBuild rows',
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'templates/partials/kpi_cards.html',
        ],
    )


def _build_success_rate_document(context):
    content = _render_content(
        intro='This document explains the Success Rate circular KPI shown on both dashboard pages.',
        represents=[
            'Success Rate is the percentage of finished builds that ended successfully.',
            'It answers whether recent stored build outcomes are mostly successful, regardless of build duration.',
        ],
        calculation=[
            "successful = count of builds with result == 'SUCCESS'.",
            "finished_count = successful + failed + aborted.",
            'success_rate = successful / finished_count * 100.',
            'Running builds are excluded from the formula because they have not reached a final result yet.',
            'The UI maps the percentage into badges as Excellent at 80% or above, Fair from 50% to 79%, and Poor below 50%.',
        ],
        notes=[
            'This metric is computed by the dashboard backend from build results, unlike Health Score which comes directly from Jenkins.',
            'This document is definition-only and does not provide the current success-rate value.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.success_rate',
        dashboard_page='shared',
        title='Success Rate',
        content=content,
        tags=['success rate', 'build stability', 'successful percentage'],
        aliases=['success rate', 'success ratio', 'build success percentage'],
        time_window='all stored finished builds for the selected branch',
        aggregation='successful finished builds divided by all finished builds',
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/pipeline_kpis.js',
        ],
    )


def _build_health_score_document(context):
    content = _render_content(
        intro='This document explains the Jenkins Health Score shown on the Overview and Pipeline KPIs pages.',
        represents=[
            'Health Score is the numeric 0-100 Jenkins health report value for the selected branch or job.',
            'It is meant to provide a quick stability signal rather than a duration or throughput measurement.',
        ],
        calculation=[
            "The dashboard does not compute Health Score locally. It reads Jenkins' healthReport[0].score value and stores it as PipelineBranch.health_score.",
            'Jenkins usually derives this score from recent build health, and the detailed weighting stays Jenkins-side rather than dashboard-side.',
            'The UI renders the numeric score and compresses it into badge states: 80 to 100 = Excellent, 50 to 79 = Fair, below 50 = Poor.',
        ],
        notes=[
            'If you need the exact reason for a particular Health Score change, Jenkins is the source of truth because the app only mirrors the returned score.',
            'This is a generic definition document and does not include the current Health Score value.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.health_score',
        dashboard_page='shared',
        title='Jenkins Health Score',
        content=content,
        tags=['health score', 'jenkins health', 'pipeline health'],
        aliases=['health score', 'jenkins health score', 'pipeline health indicator'],
        time_window='Jenkins-provided current job health indicator for the selected branch',
        aggregation='Jenkins-provided score; not recalculated by the app',
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'collectors/jenkins_collector.py',
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/pipeline_kpis.js',
        ],
    )


def _build_active_builds_document(context):
    content = _render_content(
        intro='This document explains the Active Builds card on the Overview page.',
        represents=[
            'The card lists builds that are currently running and have not produced a final Jenkins result yet.',
            'Each row is a live operational status entry, not a historical KPI snapshot.',
        ],
        calculation=[
            'A build is considered active when its result is null.',
            'The elapsed timer is computed in the browser from the current time minus the build timestamp.',
            'The progress bar is a pacing hint based on elapsed_seconds / average_finished_build_duration, capped at 95%.',
            'That progress bar is not Jenkins-native completion percentage and should not be read as exact stage progress.',
        ],
        notes=[
            'When live polling is available, the Overview page merges stored snapshot data with the running-build endpoint so this card stays current between full refreshes.',
            'This document explains the signification of the card only and does not expose the current running-build count.',
        ],
    )
    return _document(
        context=context,
        document_key='overview.active_builds',
        dashboard_page='overview',
        title='Overview Active Builds',
        content=content,
        tags=['active builds', 'running builds', 'live build status'],
        aliases=['active builds', 'running builds', 'current builds'],
        time_window='current live running builds',
        aggregation='list of builds where result is null, with browser-side elapsed time',
        source_endpoints=['/api/pipeline/kpis', '/api/running_builds', '/api/running_stages'],
        source_files=[
            'services/jenkins_service.py',
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_overview_latest_duration_document(context):
    content = _render_content(
        intro='This document explains the "Latest Builds Duration" chart on the Overview page.',
        represents=[
            'The chart compares finished build durations from the last 24 hours.',
            'Each bar is one finished build and the summary row counts pass, fail, and aborted outcomes for that same 24-hour build set.',
        ],
        calculation=[
            'Only finished builds whose timestamps fall within the last 24 hours are included.',
            'Build duration comes from duration_ms when available.',
            'The average badge is the mean duration of all included finished builds in that 24-hour window.',
            'Bar heights are normalized against the longest build in the same window, so the column height is a relative comparison and not a direct time percentage.',
        ],
        notes=[
            'This widget is intentionally build-level and 24-hour scoped, while the Pipeline KPIs duration chart is grouped into weekly or monthly averages.',
            'This document is definition-only and does not include the latest build duration currently shown in the UI.',
        ],
    )
    return _document(
        context=context,
        document_key='overview.latest_builds_duration_24h',
        dashboard_page='overview',
        title='Overview Latest Builds Duration',
        content=content,
        tags=['latest build duration', 'overview duration', '24h build duration'],
        aliases=[
            'latest build duration',
            'latest builds duration',
            'overview duration chart',
            '24 hour build duration',
        ],
        time_window='finished builds from the last 24 hours',
        aggregation='per-build durations with a 24-hour average and relative bar scaling',
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/overview.js',
            'templates/partials/kpi_cards.html',
        ],
    )


def _build_overview_trend_document(context):
    content = _render_content(
        intro='This document explains the "Build Trend" visualization on the Overview page.',
        represents=[
            'The chart is a stability visualization for finished builds from the last 24 hours.',
            'It shows the sequence of recent build outcomes, not an average duration or count aggregation.',
        ],
        calculation=[
            'Only finished builds from the last 24 hours are rendered in this chart.',
            "The success line treats SUCCESS as a high point and non-success outcomes as a low point, so it visualizes stability rather than multiple numeric result levels.",
            'Dot color still preserves the final outcome: green for success, red for failure, and orange for aborted.',
            'When enough points exist, the badge compares the success rate of the latest five builds against the previous five builds. Otherwise it shows the recent success rate only.',
        ],
        notes=[
            'Aborted builds are part of the visualization and are visually distinct, but they share the low stability position with failures.',
            'This is a generic explanation of the chart behavior and does not include the current 24-hour success/failure mix.',
        ],
    )
    return _document(
        context=context,
        document_key='overview.build_trend_24h',
        dashboard_page='overview',
        title='Overview Build Trend',
        content=content,
        tags=['build trend', 'overview trend', '24h build trend', 'pass fail abort'],
        aliases=[
            'build trend',
            'overview build trend',
            'pass fail abort trend',
            'build stability trend',
        ],
        time_window='finished builds from the last 24 hours',
        aggregation='ordered build outcomes with a recent-vs-previous success-rate badge',
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_overview_history_document(context):
    content = _render_content(
        intro='This document explains the Build History panel on the Overview page.',
        represents=[
            'The panel is a last-24-hours operational history for the selected branch.',
            'It mixes running and finished builds so users can inspect recent behavior without leaving the dashboard.',
        ],
        calculation=[
            'The panel filters builds to the last 24 hours and sorts them from newest to oldest.',
            'Running builds stay at the top and receive live elapsed-time updates in the browser.',
            'Each stage strip renders one segment per stored stage, with the segment color reflecting the stage status.',
            'Finished rows display the final result badge, while running rows also show a progress bar paced against the average finished-build duration.',
        ],
        notes=[
            'This panel is more diagnostic than numerical, but it is still stored here because users often ask what the stage strip and result flow mean in the dashboard.',
            'This definition is generic and independent from the current number of visible rows.',
        ],
    )
    return _document(
        context=context,
        document_key='overview.build_history_24h',
        dashboard_page='overview',
        title='Overview Build History',
        content=content,
        tags=['build history', 'stage strip', 'last 24 hours history'],
        aliases=['build history', 'overview history', 'stage history'],
        time_window='all builds from the last 24 hours, including running ones',
        aggregation='newest-first build timeline with stage-level rendering',
        source_endpoints=['/api/pipeline/kpis', '/api/running_builds', '/api/running_stages'],
        source_files=[
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_overview_tests_duration_document(context):
    content = _render_content(
        intro='This document explains the "Test Stages Duration" chart on the Overview page.',
        represents=[
            'The chart shows how much time each recent build spent in unit-test-related stages during the last 24 hours.',
            'It combines unit tests, pylint, and SonarCloud stages into one per-build total so users can see QA pressure quickly.',
        ],
        calculation=[
            'The backend classifies stage names into three buckets: unit_tests, pylint, and sonarcloud.',
            "Names containing 'pytest', 'unit test', or a generic 'test' marker are treated as unit tests unless they clearly refer to integration, e2e, smoke, acceptance, performance, or load testing.",
            "Names containing 'pylint' are grouped as pylint, and names containing 'sonar' are grouped as SonarCloud.",
            'total_duration_ms = unit_tests_ms + pylint_ms + sonarcloud_ms.',
            'The Overview page then filters those points to the last 24 hours, shows one bar per build, computes a 24-hour average badge, and highlights the latest and peak durations.',
        ],
        notes=[
            'This chart intentionally focuses on unit-test-related stages and does not try to represent every possible test category in the pipeline.',
            'This document explains the signification of the chart only and does not include current duration values.',
        ],
    )
    return _document(
        context=context,
        document_key='overview.tests_duration_24h',
        dashboard_page='overview',
        title='Overview Test Stages Duration',
        content=content,
        tags=['tests duration', 'test stages duration', 'unit tests duration', '24h tests duration'],
        aliases=[
            'tests duration',
            'test stages duration',
            'unit tests duration',
            'overview tests duration',
        ],
        time_window='builds from the last 24 hours with positive classified test-stage duration',
        aggregation='per-build total of unit_tests_ms + pylint_ms + sonarcloud_ms',
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_pipeline_duration_grouped_document(context):
    content = _render_content(
        intro='This document explains the grouped "Latest Builds" duration chart on the Pipeline KPIs page.',
        represents=[
            'The chart summarizes finished build duration as weekly or monthly averages rather than as one bar per build.',
            'It helps users compare longer-term pace changes instead of only looking at the last 24 hours.',
        ],
        calculation=[
            'Only finished builds with a positive duration are eligible.',
            'The frontend groups builds by the user display timezone, using Monday-start weeks or calendar months depending on the selected toggle.',
            'For each group, avgDurationMs = totalDurationMs / buildCount.',
            'The toolbar badge shows the overall average across all eligible finished builds, while the summary pill highlights the latest group average.',
        ],
        notes=[
            'This is the grouped counterpart to the Overview 24-hour duration chart, so the time window and aggregation are intentionally different.',
            'This document defines the grouping logic only and does not include the current week or month averages.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.build_duration_grouped',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs Grouped Build Duration',
        content=content,
        tags=['build duration', 'week average', 'month average', 'pipeline latest builds'],
        aliases=[
            'pipeline build duration',
            'weekly build duration',
            'monthly build duration',
            'latest builds grouped by week',
        ],
        time_window='all stored finished builds for the selected branch',
        aggregation='average duration grouped by user-local week or month',
        source_endpoints=['/api/pipeline_kpis'],
        source_files=[
            'static/js/base.js',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_tests_duration_grouped_document(context):
    content = _render_content(
        intro='This document explains the grouped "Tests Duration" chart on the Pipeline KPIs page.',
        represents=[
            'The chart shows how unit-test-related pipeline time behaves over week and month buckets.',
            'It is a grouped trend view rather than a single-build diagnostic chart.',
        ],
        calculation=[
            'The source points are the same classified test-stage totals used by the Overview page.',
            'Only builds with a positive classified total_duration_ms are grouped.',
            'The selected week or month bucket average is computed as sum(total_duration_ms) / sampleCount.',
            'The overall toolbar badge shows the average across all eligible builds, and the summary pill shows the latest bucket average.',
        ],
        notes=[
            'This chart focuses on the combined unit_tests, pylint, and SonarCloud time, not full pipeline duration.',
            'This document is generic and does not include the currently displayed grouped duration values.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.tests_duration_grouped',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs Grouped Tests Duration',
        content=content,
        tags=['tests duration trend', 'weekly tests duration', 'monthly tests duration'],
        aliases=[
            'tests duration trend',
            'pipeline tests duration',
            'weekly tests duration',
            'monthly tests duration',
        ],
        time_window='all stored classified test-duration points for the selected branch',
        aggregation='average total_duration_ms grouped by user-local week or month',
        source_endpoints=['/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_junit_grouped_document(context):
    content = _render_content(
        intro='This document explains the grouped "Unit Test Results" chart on the Pipeline KPIs page.',
        represents=[
            'The chart summarizes JUnit passed, failed, and skipped test counts as week or month averages.',
            'It helps users see test-result volume and quality over time instead of inspecting one build at a time.',
        ],
        calculation=[
            'Only points with a numeric total test count are eligible.',
            'For each selected week or month bucket, the frontend averages passed, failed, skipped, and total counts across the builds in that bucket.',
            'The stacked bars therefore show average passed, average failed, and average skipped counts per build in the group.',
            'The toolbar badge shows the overall average total-test count across all eligible builds.',
        ],
        notes=[
            'This chart is based on stored JUnit report totals and does not infer test counts when Jenkins artifacts are missing.',
            'This is a definition document and does not include the current averaged JUnit totals.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.junit_grouped',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs Grouped Unit Test Results',
        content=content,
        tags=['junit', 'unit test results', 'passed failed skipped'],
        aliases=[
            'junit results',
            'unit test results',
            'passed failed skipped tests',
            'weekly junit',
            'monthly junit',
        ],
        time_window='stored JUnit points for the selected branch',
        aggregation='average passed, failed, skipped, and total counts grouped by user-local week or month',
        source_endpoints=['/api/pipeline_kpis'],
        source_files=[
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_coverage_grouped_document(context):
    content = _render_content(
        intro='This document explains the "Tests Coverage Trend" chart on the Pipeline KPIs page.',
        represents=[
            'The chart tracks average test coverage over weekly or monthly groups for the selected branch.',
            'It answers whether stored build coverage is drifting upward or downward over time.',
        ],
        calculation=[
            'Only coverage points with a numeric coverage value are eligible.',
            'Coverage values are sourced from stored build rows, typically after coverage artifacts are collected or backfilled.',
            'For each selected week or month bucket, avgCoverage = totalCoverage / sampleCount.',
            'The toolbar badge shows the overall average coverage across every eligible point.',
        ],
        notes=[
            'Tester accounts can see this chart even when some management widgets are hidden, because it is treated as a QA-focused metric.',
            'This document explains the metric definition only and does not expose the current coverage averages.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.coverage_grouped',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs Grouped Coverage Trend',
        content=content,
        tags=['coverage trend', 'test coverage', 'weekly coverage', 'monthly coverage'],
        aliases=[
            'coverage trend',
            'tests coverage trend',
            'weekly coverage',
            'monthly coverage',
            'coverage average',
        ],
        time_window='stored numeric coverage points for the selected branch',
        aggregation='average coverage grouped by user-local week or month',
        source_endpoints=['/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_stage_failure_rate_document(context):
    content = _render_content(
        intro='This document explains the "Failure Rate by Stage" chart on the Pipeline KPIs page.',
        represents=[
            'The chart highlights which pipeline stages fail most often in stored finished builds.',
            'It helps users pinpoint unstable steps rather than only knowing that the whole build failed.',
        ],
        calculation=[
            'Only finished builds are considered.',
            'For each stage name, the backend counts how many finished builds contained that stage and how many of those stage executions ended with FAILED status.',
            'failure_rate = failed_stage_executions / total_stage_occurrences * 100.',
            'The frontend sorts the computed rates in descending order and shows only the top three stages with the highest failure rates.',
        ],
        notes=[
            'This is a frequency metric, not a duration-weighted metric, so a short stage and a long stage count equally per occurrence.',
            'This definition does not include the current top three stages for a specific pipeline snapshot.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.stage_failure_rate',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs Failure Rate by Stage',
        content=content,
        tags=['failure rate by stage', 'stage failure', 'top failing stages'],
        aliases=[
            'failure rate by stage',
            'stage failure rate',
            'top failing stages',
        ],
        time_window='all stored finished builds for the selected branch',
        aggregation='FAILED stage occurrences divided by total finished-build stage occurrences',
        source_endpoints=['/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_documents(context):
    return [
        _build_volume_document(context),
        _build_success_rate_document(context),
        _build_health_score_document(context),
        _build_active_builds_document(context),
        _build_overview_latest_duration_document(context),
        _build_overview_trend_document(context),
        _build_overview_history_document(context),
        _build_overview_tests_duration_document(context),
        _build_pipeline_duration_grouped_document(context),
        _build_pipeline_tests_duration_grouped_document(context),
        _build_pipeline_junit_grouped_document(context),
        _build_pipeline_coverage_grouped_document(context),
        _build_stage_failure_rate_document(context),
    ]


def list_dashboard_kpi_documents(limit=50, dashboard_page=None):
    context = _resolve_context()
    query = DashboardKpiDocument.query.filter_by(
        pipeline_job_path=context['pipeline_job_path'],
        branch_name=context['branch_name'],
    )
    if dashboard_page:
        query = query.filter_by(dashboard_page=str(dashboard_page).strip())
    return (
        query
        .order_by(
            DashboardKpiDocument.dashboard_page.asc(),
            DashboardKpiDocument.title.asc(),
        )
        .limit(max(_coerce_int(limit, default=50), 1))
        .all()
    )


def get_dashboard_kpi_document(document_key):
    clean_key = str(document_key or '').strip()
    if not clean_key:
        return None
    context = _resolve_context()
    return DashboardKpiDocument.query.filter_by(
        pipeline_job_path=context['pipeline_job_path'],
        branch_name=context['branch_name'],
        document_key=clean_key,
    ).one_or_none()


def _chunk_summary(document_row, *, chunk_index, chunk_count):
    document_summary = document_row.summary or {}
    tags = [str(item).strip() for item in (document_summary.get('tags') or []) if item]
    aliases = [str(item).strip() for item in (document_summary.get('aliases') or []) if item]

    return {
        'kind': document_summary.get('kind') or 'dashboard_kpi_explanation',
        'value_mode': document_summary.get('value_mode') or 'definition_only',
        'tags': tags,
        'aliases': aliases,
        'tag_csv': ', '.join(tags),
        'alias_csv': ', '.join(aliases),
        'time_window': str(document_summary.get('time_window') or ''),
        'aggregation': str(document_summary.get('aggregation') or ''),
        'chunk_index': int(chunk_index),
        'chunk_count': int(chunk_count),
    }


def _build_chunk_records_for_document(document_row, chunk_config):
    chunks = split_text_into_chunks(
        document_row.content,
        chunk_config['chunk_size'],
        chunk_config['chunk_overlap'],
    )
    if not chunks:
        return []

    chunk_count = len(chunks)
    return [
        {
            'chunk_index': index,
            'chunk_count': chunk_count,
            'title': document_row.title,
            'content': chunk,
            'summary': _chunk_summary(
                document_row,
                chunk_index=index,
                chunk_count=chunk_count,
            ),
        }
        for index, chunk in enumerate(chunks)
    ]


def _sync_document_chunks(document_rows, chunk_config, now):
    document_ids = [row.id for row in document_rows if row.id is not None]
    if not document_ids:
        return {
            'generated': 0,
            'created': 0,
            'updated': 0,
            'deleted': 0,
        }

    existing_rows = (
        DashboardKpiDocumentChunk.query
        .filter(DashboardKpiDocumentChunk.document_id.in_(document_ids))
        .all()
    )
    existing_by_key = {
        (row.document_id, row.chunk_index): row
        for row in existing_rows
    }

    created = 0
    updated = 0
    deleted = 0
    generated = 0

    for document_row in document_rows:
        chunk_records = _build_chunk_records_for_document(document_row, chunk_config)
        keep_indexes = {record['chunk_index'] for record in chunk_records}

        for record in chunk_records:
            key = (document_row.id, record['chunk_index'])
            row = existing_by_key.get(key)
            if row is None:
                row = DashboardKpiDocumentChunk(
                    document_id=document_row.id,
                    chunk_index=record['chunk_index'],
                )
                db.session.add(row)
                existing_by_key[key] = row
                created += 1
            else:
                updated += 1

            row.pipeline_job_path = document_row.pipeline_job_path
            row.pipeline_name = document_row.pipeline_name
            row.branch_name = document_row.branch_name
            row.document_key = document_row.document_key
            row.dashboard_page = document_row.dashboard_page
            row.chunk_count = record['chunk_count']
            row.title = record['title']
            row.content = record['content']
            row.summary = record['summary']
            row.source_system = 'dashboard_kpis_rag'
            row.last_generated_at = now
            generated += 1

        for row in existing_rows:
            if row.document_id != document_row.id:
                continue
            if row.chunk_index in keep_indexes:
                continue
            db.session.delete(row)
            deleted += 1

    return {
        'generated': generated,
        'created': created,
        'updated': updated,
        'deleted': deleted,
    }


def _build_document_chunk_query(pipeline_job_path, branch_name):
    return DashboardKpiDocumentChunk.query.filter_by(
        pipeline_job_path=pipeline_job_path,
        branch_name=branch_name,
    )


def get_dashboard_kpi_document_chunks(document_key):
    clean_key = str(document_key or '').strip()
    if not clean_key:
        return []

    context = _resolve_context()
    return (
        _build_document_chunk_query(
            context['pipeline_job_path'],
            context['branch_name'],
        )
        .filter_by(document_key=clean_key)
        .order_by(
            DashboardKpiDocumentChunk.document_id.asc(),
            DashboardKpiDocumentChunk.chunk_index.asc(),
            DashboardKpiDocumentChunk.id.asc(),
        )
        .all()
    )


def list_dashboard_kpi_document_chunks(limit=200, dashboard_page=None):
    context = _resolve_context()
    query = _build_document_chunk_query(
        context['pipeline_job_path'],
        context['branch_name'],
    )
    if dashboard_page:
        query = query.filter_by(dashboard_page=str(dashboard_page).strip())
    return (
        query
        .order_by(
            DashboardKpiDocumentChunk.dashboard_page.asc(),
            DashboardKpiDocumentChunk.document_key.asc(),
            DashboardKpiDocumentChunk.chunk_index.asc(),
        )
        .limit(max(_coerce_int(limit, default=200), 1))
        .all()
    )


def sync_dashboard_kpi_documents():
    context = _resolve_context()
    documents = _build_documents(context)
    existing_rows = {
        row.document_key: row
        for row in DashboardKpiDocument.query.filter_by(
            pipeline_job_path=context['pipeline_job_path'],
            branch_name=context['branch_name'],
        ).all()
    }

    created = 0
    updated = 0
    now = _utcnow()
    keep_keys = set()
    processed_rows = []
    chunk_config = _get_chunking_config()

    try:
        for item in documents:
            keep_keys.add(item['document_key'])
            row = existing_rows.get(item['document_key'])
            if row is None:
                row = DashboardKpiDocument(
                    pipeline_job_path=context['pipeline_job_path'],
                    branch_name=context['branch_name'],
                    document_key=item['document_key'],
                )
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.pipeline_name = context['pipeline_name']
            row.dashboard_page = item['dashboard_page']
            row.title = item['title']
            row.content = item['content']
            row.summary = item['summary']
            row.source_system = 'dashboard_kpis_rag'
            row.last_generated_at = now
            processed_rows.append(row)

        deleted = 0
        stale_document_ids = [
            row.id
            for key, row in existing_rows.items()
            if key not in keep_keys and row.id is not None
        ]
        stale_chunk_delete_count = 0
        if stale_document_ids:
            stale_chunk_delete_count = (
                DashboardKpiDocumentChunk.query
                .filter(DashboardKpiDocumentChunk.document_id.in_(stale_document_ids))
                .count()
            )

        for key, row in existing_rows.items():
            if key in keep_keys:
                continue
            db.session.delete(row)
            deleted += 1

        db.session.flush()
        chunk_stats = _sync_document_chunks(processed_rows, chunk_config, now)
        chunk_stats['deleted'] += stale_chunk_delete_count
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to sync dashboard KPI documents.')
        raise

    return {
        'pipeline_job_path': context['pipeline_job_path'],
        'pipeline_name': context['pipeline_name'],
        'branch_name': context['branch_name'],
        'generated': len(documents),
        'created': created,
        'updated': updated,
        'deleted': deleted,
        'document_keys': sorted(keep_keys),
        'chunks_generated': chunk_stats['generated'],
        'chunks_created': chunk_stats['created'],
        'chunks_updated': chunk_stats['updated'],
        'chunks_deleted': chunk_stats['deleted'],
        'chunk_size': chunk_config['chunk_size'],
        'chunk_overlap': chunk_config['chunk_overlap'],
    }


def _score_document(row, normalized_query, query_tokens):
    if not normalized_query or not query_tokens:
        return 0

    summary = row.summary or {}
    aliases = [str(item).strip().lower() for item in (summary.get('aliases') or []) if item]
    tags = [str(item).strip().lower() for item in (summary.get('tags') or []) if item]
    page = str(row.dashboard_page or '').strip().lower()
    key_text = str(row.document_key or '').replace('.', ' ').replace('_', ' ').lower()
    title_text = str(row.title or '').strip().lower()
    content_text = str(row.content or '').strip().lower()

    score = 0
    if page and page in normalized_query:
        score += 3

    key_tokens = set(_tokenize(key_text))
    title_tokens = set(_tokenize(title_text))
    tag_tokens = set(_tokenize(' '.join(tags)))
    content_tokens = set(_tokenize(content_text))

    for alias in aliases:
        if alias and alias in normalized_query:
            score += 12 if ' ' in alias else 8

    for tag in tags:
        if tag and tag in normalized_query:
            score += 7 if ' ' in tag else 4

    if title_text and title_text in normalized_query:
        score += 10
    if key_text and key_text in normalized_query:
        score += 10

    for token in query_tokens:
        if token in title_tokens:
            score += 3
        if token in key_tokens:
            score += 3
        if token in tag_tokens:
            score += 2
        if token in content_tokens:
            score += 1

    return score


def query_dashboard_kpi_documents(query_text, *, limit=3, auto_generate=False):
    normalized_query = _normalize_text(query_text)
    query_tokens = set(_tokenize(normalized_query))
    if not normalized_query or not query_tokens:
        return []

    rows = list_dashboard_kpi_documents(limit=200)
    if not rows and auto_generate:
        sync_dashboard_kpi_documents()
        rows = list_dashboard_kpi_documents(limit=200)

    scored = []
    for row in rows:
        score = _score_document(row, normalized_query, query_tokens)
        if score > 0:
            scored.append((score, row))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].dashboard_page,
            item[1].title,
        )
    )
    min_score = 5
    return [
        row
        for score, row in scored
        if score >= min_score
    ][:max(_coerce_int(limit, default=3), 1)]
