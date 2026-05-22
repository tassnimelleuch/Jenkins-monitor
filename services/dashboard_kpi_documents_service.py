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

BLOCKED_SOURCE_FILE_NAMES = frozenset({
    'pylint-report.json',
})


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


def _normalize_source_file_name(value):
    text = str(value or '').strip().replace('\\', '/')
    if not text:
        return ''
    return text.rsplit('/', 1)[-1].lower()


def _sanitize_source_files(source_files):
    cleaned = []
    seen = set()
    for item in (source_files or []):
        text = str(item or '').strip()
        if not text:
            continue
        if _normalize_source_file_name(text) in BLOCKED_SOURCE_FILE_NAMES:
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def sanitize_dashboard_kpi_text(value):
    text = str(value or '')
    for blocked_name in BLOCKED_SOURCE_FILE_NAMES:
        text = text.replace(blocked_name, '')
    return text


def sanitize_dashboard_kpi_summary(summary):
    summary_dict = dict(summary or {})
    summary_dict['source_files'] = _sanitize_source_files(summary_dict.get('source_files'))
    return summary_dict


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
        'content': sanitize_dashboard_kpi_text(content),
        'summary': {
            'kind': 'dashboard_kpi_explanation',
            'value_mode': 'definition_only',
            'tags': list(tags or []),
            'aliases': list(aliases or []),
            'time_window': time_window,
            'aggregation': aggregation,
            'source_endpoints': list(source_endpoints or []),
            'source_files': _sanitize_source_files(source_files),
        },
    }


def _build_volume_document(context):
    content = _render_content(
        intro=(
            'The Total Builds, Successful, Failed, and Aborted cards are shown on the '
            'Overview page for the selected Jenkins branch.'
        ),
        represents=[
            'These Overview cards summarize completed build volume for the selected pipeline branch.',
            'Total Builds is the total number of stored finished builds for the selected branch.',
            'Successful, Failed, and Aborted show how that finished-build population is distributed by final status.',
            "Successful means builds finished with 'SUCCESS', Failed means builds finished with 'FAILURE', and Aborted means builds finished with 'ABORTED', which commonly reflects manually stopped builds.",
        ],
        calculation=[
            'Only finished builds are counted in these cards.',
            'Total Builds = count of finished builds stored for the selected branch.',
            "Successful = count of finished builds where result = 'SUCCESS'.",
            "Failed = count of finished builds where result = 'FAILURE'.",
            "Aborted = count of finished builds where result = 'ABORTED'.",
            'Running builds are excluded from these cards and shown separately through Active Builds on the Overview page.',
        ],
        notes=[
            'These cards reflect completed build outcomes since the app began storing build history for the selected branch.',
            'These KPI cards are currently available only on the Overview page.',
            'These KPI definitions describe the meaning and general formulas, not the current numeric values on a specific dashboard snapshot.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.build_volume_summary',
        dashboard_page='overview',
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
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'templates/overview.html',
            'templates/partials/kpi_cards.html',
        ],
    )


def _build_success_rate_document(context):
    content = _render_content(
        intro='Success Rate is a circular KPI shown on the Overview page.',
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
            'This KPI is currently available only on the Overview page.',
            'It defines the KPI and does not provide the current success-rate value.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.success_rate',
        dashboard_page='overview',
        title='Success Rate',
        content=content,
        tags=['success rate', 'build stability', 'successful percentage'],
        aliases=['success rate', 'success ratio', 'build success percentage'],
        time_window='all stored finished builds for the selected branch',
        aggregation='successful finished builds divided by all finished builds',
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_health_score_document(context):
    content = _render_content(
        intro='Jenkins Health Score is shown on the Overview page.',
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
            'This KPI is currently available only on the Overview page.',
            'It defines the KPI and does not include the current Health Score value.',
        ],
    )
    return _document(
        context=context,
        document_key='shared.health_score',
        dashboard_page='overview',
        title='Jenkins Health Score',
        content=content,
        tags=['health score', 'jenkins health', 'pipeline health'],
        aliases=['health score', 'jenkins health score', 'pipeline health indicator'],
        time_window='Jenkins-provided current job health indicator for the selected branch',
        aggregation='Jenkins-provided score; not recalculated by the app',
        source_endpoints=['/api/pipeline/kpis'],
        source_files=[
            'collectors/jenkins_collector.py',
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/overview.js',
            'templates/overview.html',
        ],
    )


def _build_active_builds_document(context):
    content = _render_content(
        intro='Active Builds is a live card shown on the Overview page.',
        represents=[
            'The card shows builds that are currently running and do not yet have a final Jenkins result.',
            'It reflects live execution activity for the selected branch rather than historical completed-build counts.',
        ],
        calculation=[
            'A build is considered active when its result is null.',
            'Running builds are excluded from Total Builds, Successful, Failed, and Aborted.',
            'The elapsed timer is computed in the browser from the current time minus the build timestamp.',
            'The progress bar is a pacing hint based on elapsed_seconds / average_finished_build_duration, capped at 95%.',
            'That progress bar is not Jenkins-native completion percentage and should not be read as exact stage progress.',
        ],
        notes=[
            'When live polling is available, the Overview page merges stored snapshot data with the running-build endpoint so this card stays current between full refreshes.',
            'Users can open the running build console directly from this card on the Overview page.',
            'It defines the card behavior and does not expose the current running-build count.',
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
        intro='"Latest Builds Duration" is a chart shown on the Overview page.',
        represents=[
            'The chart shows the duration of each finished build from the last 24 hours.',
            'It reflects recent build-by-build execution time on the Overview page and also shows how many builds passed, failed, or were aborted within the same 24-hour window.',
            'The same build-duration metric is also presented on the Pipeline KPIs page in grouped form as weekly averages or monthly averages instead of one bar per build.',
        ],
        calculation=[
            'On the Overview page, only finished builds whose timestamps fall within the last 24 hours are included.',
            'On the Overview page, each included build is represented as a single duration bar using its stored build duration value.',
            'On the Overview page, passed, failed, and aborted builds are counted over that same 24-hour finished-build set.',
            'On the Overview page, the average badge is the mean duration of all included finished builds in the 24-hour window.',
            'On the Overview page, bar heights are normalized against the longest build in the same window, so the column height is a relative comparison and not a direct time percentage.',
            'On the Pipeline KPIs page, only finished builds with a positive duration are eligible.',
            'On the Pipeline KPIs page, builds are grouped by the user display timezone into Monday-start weeks or calendar months depending on the selected toggle.',
            'On the Pipeline KPIs page, each group average is calculated as avgDurationMs = totalDurationMs / buildCount.',
        ],
        notes=[
            'The Overview page is a recent per-build view, while the Pipeline KPIs page is a grouped trend view.',
            'It defines the chart behavior and does not include the latest build duration currently shown in the UI.',
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
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/overview.js',
            'static/js/pipeline_kpis.js',
            'templates/overview.html',
            'templates/partials/kpi_cards.html',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_overview_trend_document(context):
    content = _render_content(
        intro='"Build Trend" is a visualization shown on the Overview page.',
        represents=[
            'The chart visualizes the behavior of the selected pipeline branch over the last 24 hours.',
            'It reflects recent stability by showing the sequence of finished build outcomes rather than a duration average or build-count total.',
        ],
        calculation=[
            'Only finished builds from the last 24 hours are rendered in this chart.',
            'Each build is plotted in chronological order using its final outcome.',
            "The success line treats SUCCESS as a high point and non-success outcomes as a low point, so it visualizes stability rather than multiple numeric result levels.",
            'Dot color still preserves the final outcome: green for success, red for failure, and orange for aborted.',
            'When enough points exist, the badge compares the success rate of the latest five builds against the previous five builds. Otherwise it shows the recent success rate only.',
        ],
        notes=[
            'Aborted builds are part of the visualization and are visually distinct, but they share the low stability position with failures.',
            'This is a behavior and stability view on the Overview page and does not include the current 24-hour success/failure mix.',
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
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/overview.js',
            'static/js/pipeline_kpis.js',
            'templates/overview.html',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_overview_history_document(context):
    content = _render_content(
        intro='Build History is a panel shown on the Overview page.',
        represents=[
            'The panel shows builds from the last 24 hours for the selected branch.',
            'It reflects recent pipeline activity with stage-level visibility so users can inspect behavior without leaving the dashboard.',
        ],
        calculation=[
            'The panel filters builds to the last 24 hours and sorts them from newest to oldest.',
            'Each row represents one build together with its stage strip and current or final result.',
            'Running builds stay at the top and receive live elapsed-time updates in the browser.',
            'Each stage strip renders one segment per stored stage, with the segment color reflecting the stage status.',
            'Users can hover a stage segment to inspect its details and click a row to open the build console.',
            'Finished rows display the final result badge, while running rows also show a progress bar paced against the average finished-build duration.',
        ],
        notes=[
            'The panel can include both running and finished builds if they fall within the last 24-hour window.',
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
        intro='"Test Stages Duration" is a chart shown on the Overview page.',
        represents=[
            'The chart shows the duration of test-related stages for each build during the last 24 hours.',
            'It reflects how much time each build spends in unit tests, pylint, and SonarCloud or SonarQube analysis.',
            'The same metric is also presented on the Pipeline KPIs page in grouped form as weekly averages or monthly averages instead of one bar per build.',
        ],
        calculation=[
            'For each build, the metric sums the duration of the unit-test stage or stages, the pylint stage or stages, and the SonarCloud or SonarQube stage or stages.',
            'General formula: Test Stages Duration = Unit Tests duration + Pylint duration + SonarCloud or SonarQube duration.',
            'On the Overview page, only builds from the last 24 hours with a positive test-stage total are shown.',
            'On the Overview page, each eligible build is shown as one bar and the chart also computes a 24-hour average badge plus latest-build and peak-duration highlights.',
            'On the Pipeline KPIs page, only builds with a positive classified test-stage total are eligible.',
            'On the Pipeline KPIs page, builds are grouped by the user display timezone into Monday-start weeks or calendar months depending on the selected toggle.',
            'On the Pipeline KPIs page, each group average is calculated as avgTestsDuration = sum(total_duration_ms) / sampleCount.',
        ],
        notes=[
            'The Overview page is a recent per-build view, while the Pipeline KPIs page is a grouped trend view.',
            'This metric does not represent the full end-to-end pipeline duration.',
            'This chart intentionally focuses on unit-test-related stages and does not try to represent every possible test category in the pipeline.',
            'It defines the chart behavior and does not include current duration values.',
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
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/overview.js',
            'static/js/pipeline_kpis.js',
            'templates/overview.html',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_duration_grouped_document(context):
    content = _render_content(
        intro='The grouped "Latest Builds" duration chart is shown on the Pipeline KPIs page.',
        represents=[
            'The chart summarizes finished build duration as weekly or monthly averages rather than as one bar per build.',
            'It helps users compare longer-term pace changes instead of only looking at the last 24 hours.',
            'The same build-duration metric also appears on the Overview page, where it is shown per build for finished builds from the last 24 hours.',
        ],
        calculation=[
            'Only finished builds with a positive duration are eligible.',
            'The frontend groups builds by the user display timezone, using Monday-start weeks or calendar months depending on the selected toggle.',
            'For each group, avgDurationMs = totalDurationMs / buildCount.',
            'The toolbar badge shows the overall average across all eligible finished builds, while the summary pill highlights the latest group average.',
            'On the Overview page, the corresponding chart instead filters finished builds to the last 24 hours and shows one duration bar per build.',
        ],
        notes=[
            'The Pipeline KPIs page is a grouped trend view, while the Overview page is a recent per-build view.',
            'It defines the grouping logic and does not include the current week or month averages.',
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
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/base.js',
            'static/js/overview.js',
            'static/js/pipeline_kpis.js',
            'templates/overview.html',
            'templates/partials/kpi_cards.html',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_tests_duration_grouped_document(context):
    content = _render_content(
        intro='The grouped "Tests Duration" chart is shown on the Pipeline KPIs page.',
        represents=[
            'The chart shows how unit-test-related pipeline time behaves over week and month buckets.',
            'It is a grouped trend view rather than a single-build diagnostic chart.',
            'The same test-stages-duration metric also appears on the Overview page, where it is shown per build for the last 24 hours.',
        ],
        calculation=[
            'The source points are the same classified test-stage totals used by the Overview page.',
            'Only builds with a positive classified total_duration_ms are grouped.',
            'The selected week or month bucket average is computed as sum(total_duration_ms) / sampleCount.',
            'The overall toolbar badge shows the average across all eligible builds, and the summary pill shows the latest bucket average.',
            'On the Overview page, the corresponding chart instead filters those points to the last 24 hours and shows one bar per build.',
        ],
        notes=[
            'The Pipeline KPIs page is a grouped trend view, while the Overview page is a recent per-build view.',
            'This chart focuses on the combined unit_tests, pylint, and SonarCloud time, not full pipeline duration.',
            'It defines the grouped metric and does not include the currently displayed grouped duration values.',
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
        source_endpoints=['/api/pipeline/kpis', '/api/pipeline_kpis'],
        source_files=[
            'services/pipeline_storage_service.py',
            'static/js/overview.js',
            'static/js/pipeline_kpis.js',
            'templates/overview.html',
            'templates/pipeline_kpis.html',
        ],
    )


def _build_pipeline_junit_grouped_document(context):
    content = _render_content(
        intro='The grouped "Unit Test Results" chart is shown on the Pipeline KPIs page.',
        represents=[
            'The chart summarizes JUnit unit-test results as week or month averages.',
            'It reflects how many builds have stored JUnit results and how the average passed, failed, skipped, and total test counts evolve over time.',
        ],
        calculation=[
            'Only builds with a numeric JUnit total test count and a timestamp are eligible.',
            'Builds are grouped by the user display timezone into Monday-start weeks or calendar months depending on the selected toggle.',
            'For each selected week or month bucket, the chart counts how many builds in that bucket have JUnit results.',
            'For each selected week or month bucket, avgPassed = totalPassed / sampleCount, avgFailed = totalFailed / sampleCount, avgSkipped = totalSkipped / sampleCount, and avgTotal = totalTests / sampleCount.',
            'The stacked bars show the average passed, average failed, and average skipped test counts per build in the selected week or month.',
            'The summary pills show the number of displayed weeks or months and the latest week or month average total-test count.',
            'The toolbar badge shows the overall average total-test count across all eligible builds.',
        ],
        notes=[
            'This chart is based on stored JUnit report totals and does not infer test counts when Jenkins artifacts are missing.',
            'It defines the grouped metric and does not include the current averaged JUnit totals.',
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
        intro='"Tests Coverage Trend" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart tracks average test coverage percentage over weekly or monthly groups for the selected branch.',
            'It reflects the average coverage percentage of builds in each displayed week or month and helps show whether stored build coverage is drifting upward or downward over time.',
        ],
        calculation=[
            'Only builds with a numeric stored coverage value and a timestamp are eligible.',
            'Coverage values are sourced from stored build rows, typically after coverage artifacts are collected or backfilled.',
            'Builds are grouped by the user display timezone into Monday-start weeks or calendar months depending on the selected toggle.',
            'For each selected week or month bucket, avgCoverage = totalCoverage / sampleCount.',
            'Each plotted point therefore represents the average coverage percentage of the builds that fall in that week or month bucket.',
            'The summary pills show the number of displayed weeks or months and the latest week or month average coverage.',
            'The toolbar badge shows the overall average coverage across every eligible build point.',
        ],
        notes=[
            'This chart is treated as a QA-focused metric in the dashboard.',
            'It defines the metric and does not expose the current coverage averages.',
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
        intro='"Failure Rate by Stage" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart highlights which pipeline stages fail most often in stored finished builds.',
            'It reflects the top three stages with the highest failure rates so users can pinpoint unstable steps rather than only knowing that the whole build failed.',
        ],
        calculation=[
            'Only finished builds are considered.',
            'For each stage name, the backend counts how many finished builds contained that stage and how many of those stage executions ended with status FAILED.',
            'For each stage, failure_rate = failed_stage_executions / total_stage_occurrences * 100.',
            'The failure rate is rounded to one decimal place for display.',
            'After all stage rates are computed, the frontend sorts them in descending order and shows only the top three stages with the highest failure rates.',
        ],
        notes=[
            'This is a frequency metric, not a duration-weighted metric, so a short stage and a long stage count equally per occurrence.',
            'A stage must appear in finished builds to be eligible for this ranking.',
            'It defines the metric and does not include the current top three stages for a specific pipeline snapshot.',
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


def _build_vm_cpu_document(context):
    content = _render_content(
        intro='"VM CPU" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart tracks the real-time CPU behavior of the Azure virtual machine where Jenkins is running.',
            'It shows per-core CPU usage over time so users can see whether one core or several cores are under pressure.',
        ],
        calculation=[
            'The metric is sourced from Prometheus node_exporter data for the Jenkins VM.',
            'CPU usage is calculated from CPU idle time over a rolling 5-minute rate window.',
            'General formula: CPU usage % = 100 - idle CPU percentage.',
            'The chart requests approximately the last 30 minutes of history and plots one line per CPU core.',
            'The badge shows the latest visible percentage for each core, and the page refreshes the VM metrics every 30 seconds.',
        ],
        notes=[
            'This chart is shown on the Pipeline KPIs page.',
            'If no VM charts appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.vm_cpu',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs VM CPU',
        content=content,
        tags=['vm cpu', 'per-core cpu', 'jenkins vm cpu', 'azure vm cpu'],
        aliases=['vm cpu', 'cpu usage over time', 'per-core cpu usage'],
        time_window='approximately the last 30 minutes of Prometheus history for the Jenkins VM',
        aggregation='per-core CPU usage percentage derived from Prometheus idle-rate data',
        source_endpoints=['/api/vm-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
            'config.py',
        ],
    )


def _build_vm_ram_document(context):
    content = _render_content(
        intro='"VM RAM" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart tracks the real-time memory behavior of the Azure virtual machine where Jenkins is running.',
            'It shows memory usage percentage over time so users can see whether the Jenkins VM is under RAM pressure.',
        ],
        calculation=[
            'The metric is sourced from Prometheus node_exporter data for the Jenkins VM.',
            'RAM usage percentage is calculated from total memory and available memory.',
            'General formula: RAM usage % = (1 - MemAvailable / MemTotal) * 100.',
            'The chart requests approximately the last 30 minutes of history and plots memory usage percentage over time.',
            'The badge shows the average RAM usage percentage across the visible history window, and the page refreshes the VM metrics every 30 seconds.',
        ],
        notes=[
            'This chart is shown on the Pipeline KPIs page.',
            'If no VM charts appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.vm_ram',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs VM RAM',
        content=content,
        tags=['vm ram', 'memory usage', 'jenkins vm memory', 'azure vm ram'],
        aliases=['vm ram', 'memory usage over time', 'vm memory'],
        time_window='approximately the last 30 minutes of Prometheus history for the Jenkins VM',
        aggregation='memory usage percentage derived from available and total VM memory',
        source_endpoints=['/api/vm-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
            'config.py',
        ],
    )


def _build_vm_network_traffic_document(context):
    content = _render_content(
        intro='"VM Network Traffic" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart tracks the real-time inbound and outbound network behavior of the Azure virtual machine where Jenkins is running.',
            'It helps users see whether the Jenkins VM is experiencing higher receive or transmit traffic over time.',
        ],
        calculation=[
            'The metric is sourced from Prometheus node_exporter data for the Jenkins VM.',
            'Inbound traffic is calculated from the receive-byte rate and outbound traffic is calculated from the transmit-byte rate.',
            'The Prometheus queries exclude loopback and container bridge interfaces such as lo, docker, veth, and br-*.',
            'General formulas: RX MB/s = rate(received bytes) / 1024 / 1024 and TX MB/s = rate(transmitted bytes) / 1024 / 1024.',
            'The chart requests approximately the last 30 minutes of history and plots separate RX and TX lines over time.',
            'The badge shows the average combined RX + TX throughput across the visible history window, and the page refreshes the VM metrics every 30 seconds.',
        ],
        notes=[
            'This chart is shown on the Pipeline KPIs page.',
            'If no VM charts appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.vm_network_traffic',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs VM Network Traffic',
        content=content,
        tags=['vm network traffic', 'network traffic', 'jenkins vm network', 'rx tx'],
        aliases=['vm network traffic', 'inbound outbound traffic', 'vm rx tx'],
        time_window='approximately the last 30 minutes of Prometheus history for the Jenkins VM',
        aggregation='receive and transmit throughput in MB/s derived from Prometheus byte-rate data',
        source_endpoints=['/api/vm-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
            'config.py',
        ],
    )


def _build_vm_disk_space_document(context):
    content = _render_content(
        intro='"VM Disk Space" is a chart shown on the Pipeline KPIs page.',
        represents=[
            'The chart tracks the real-time disk usage behavior of the Azure virtual machine where Jenkins is running.',
            'It shows used disk percentage over time so users can see whether the Jenkins VM root filesystem is filling up.',
        ],
        calculation=[
            'The metric is sourced from Prometheus node_exporter data for the Jenkins VM.',
            'The chart focuses on the root filesystem mounted at `/`.',
            'General formula: Disk used % = 100 - (available bytes / total filesystem size bytes * 100).',
            'The chart requests approximately the last 30 minutes of history and plots used disk percentage over time.',
            'The badge shows the average used disk percentage across the visible history window, and the page refreshes the VM metrics every 30 seconds.',
        ],
        notes=[
            'This chart is shown on the Pipeline KPIs page.',
            'If no VM charts appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='pipeline_kpis.vm_disk_space',
        dashboard_page='pipeline_kpis',
        title='Pipeline KPIs VM Disk Space',
        content=content,
        tags=['vm disk space', 'disk usage', 'jenkins vm disk', 'azure vm disk'],
        aliases=['vm disk space', 'disk usage over time', 'vm disk used'],
        time_window='approximately the last 30 minutes of Prometheus history for the Jenkins VM',
        aggregation='root filesystem used-percentage derived from Prometheus filesystem metrics',
        source_endpoints=['/api/vm-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/pipeline_kpis.js',
            'templates/pipeline_kpis.html',
            'config.py',
        ],
    )


def _build_deployment_namespace_cpu_document(context):
    content = _render_content(
        intro='"CPU by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart tracks 30-minute CPU usage percentage per namespace in the cluster.',
            'It reflects how CPU usage behaves over time for each namespace so users can compare namespace-level pressure rather than only cluster-wide totals.',
        ],
        calculation=[
            'The metric is sourced from Prometheus container CPU metrics for the cluster.',
            'The history is grouped by namespace, so each line represents one namespace.',
            'CPU usage is derived from Prometheus rate queries over recent container CPU usage and is rendered as a percentage series per namespace.',
            'The chart requests approximately the last 30 minutes of history, renders up to eight namespace series at once, and refreshes every 30 seconds.',
            'The badge shows the average CPU percentage across the returned namespace history points.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'If no namespace metrics appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.cpu_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs CPU by Namespace',
        content=content,
        tags=['cpu by namespace', 'namespace cpu', 'deployment cpu', 'cluster cpu namespace'],
        aliases=['cpu by namespace', 'namespace cpu usage', '30-min cpu usage per namespace'],
        time_window='approximately the last 30 minutes of Prometheus history for cluster namespaces',
        aggregation='namespace-grouped CPU usage percentage history derived from Prometheus series',
        source_endpoints=['/api/cluster-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
            'config.py',
        ],
    )


def _build_deployment_namespace_memory_document(context):
    content = _render_content(
        intro='"Memory by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart tracks 30-minute RAM usage in GB per namespace in the cluster.',
            'It reflects how memory usage behaves over time for each namespace so users can compare namespace-level memory pressure.',
        ],
        calculation=[
            'The metric is sourced from Prometheus container memory working-set data for the cluster.',
            'The history is grouped by namespace, so each line represents one namespace.',
            'Memory usage is rendered in gigabytes per namespace over time.',
            'The chart requests approximately the last 30 minutes of history, renders up to eight namespace series at once, and refreshes every 30 seconds.',
            'The badge shows the average RAM usage in GB across the returned namespace history points.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'If no namespace metrics appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.memory_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Memory by Namespace',
        content=content,
        tags=['memory by namespace', 'namespace memory', 'deployment ram', 'cluster ram namespace'],
        aliases=['memory by namespace', 'namespace ram usage', '30-min ram usage per namespace'],
        time_window='approximately the last 30 minutes of Prometheus history for cluster namespaces',
        aggregation='namespace-grouped RAM usage history in GB derived from Prometheus series',
        source_endpoints=['/api/cluster-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
            'config.py',
        ],
    )


def _build_deployment_namespace_network_document(context):
    content = _render_content(
        intro='"Network by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart tracks 30-minute network traffic in MB/s per namespace in the cluster.',
            'It reflects inbound and outbound traffic behavior over time for each namespace so users can compare namespace-level network activity.',
        ],
        calculation=[
            'The metric is sourced from Prometheus network byte-rate data for cluster workloads.',
            'The history is grouped by namespace, so each line represents one namespace.',
            'Traffic is calculated from inbound plus outbound byte-rate queries and rendered in MB/s.',
            'The chart requests approximately the last 30 minutes of history, renders up to eight namespace series at once, and refreshes every 30 seconds.',
            'The badge shows the average network throughput in MB/s across the returned namespace history points.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'If no namespace metrics appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.network_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Network by Namespace',
        content=content,
        tags=['network by namespace', 'namespace network', 'deployment network', 'cluster network namespace'],
        aliases=['network by namespace', 'namespace traffic', 'inbound outbound per namespace'],
        time_window='approximately the last 30 minutes of Prometheus history for cluster namespaces',
        aggregation='namespace-grouped inbound and outbound throughput history in MB/s derived from Prometheus series',
        source_endpoints=['/api/cluster-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
            'config.py',
        ],
    )


def _build_deployment_namespace_disk_document(context):
    content = _render_content(
        intro='"Disk by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart tracks 30-minute filesystem usage in GB per namespace in the cluster.',
            'It reflects how namespace-level storage usage behaves over time so users can compare persistent or filesystem-related footprint across namespaces.',
        ],
        calculation=[
            'The metric is sourced from Prometheus filesystem or volume-usage series for cluster workloads.',
            'The history is grouped by namespace, so each line represents one namespace.',
            'Disk usage is rendered in gigabytes per namespace over time.',
            'The chart requests approximately the last 30 minutes of history, renders up to eight namespace series at once, and refreshes every 30 seconds.',
            'The badge shows the average disk usage in GB across the returned namespace history points.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'If no namespace metrics appear, verify that Prometheus is configured correctly and reachable through PROMETHEUS_URL.',
            'A common setup is to expose Prometheus locally with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.disk_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Disk by Namespace',
        content=content,
        tags=['disk by namespace', 'namespace disk', 'deployment disk', 'cluster disk namespace'],
        aliases=['disk by namespace', 'filesystem usage per namespace', 'namespace storage usage'],
        time_window='approximately the last 30 minutes of Prometheus history for cluster namespaces',
        aggregation='namespace-grouped filesystem usage history in GB derived from Prometheus series',
        source_endpoints=['/api/cluster-metrics'],
        source_files=[
            'services/metrics_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
            'config.py',
        ],
    )


def _build_deployment_resource_counts_document(context):
    content = _render_content(
        intro='The Pods, ReplicaSets, and PVCs cards are shown on the Deployment KPIs page.',
        represents=[
            'These cards summarize the current live Kubernetes snapshot for the connected cluster.',
            'Pods is the current total number of pods across all namespaces.',
            'ReplicaSets is the current total number of ReplicaSet workload controllers across all namespaces.',
            'PVCs is the current total number of PersistentVolumeClaims across all namespaces.',
        ],
        calculation=[
            'The metrics are sourced from a live Kubernetes cluster snapshot, not from historical Prometheus series.',
            'Pods = count of pods returned by the cluster-wide pod list.',
            'ReplicaSets = count of ReplicaSets returned by the cluster-wide ReplicaSet list.',
            'PVCs = count of PersistentVolumeClaims returned by the cluster-wide PVC list.',
        ],
        notes=[
            'These cards are shown on the Deployment KPIs page.',
            'They reflect the current cluster state at fetch time rather than a historical trend window.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.resource_counts_summary',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Resource Counts',
        content=content,
        tags=['pods', 'replicasets', 'pvcs', 'resource counts', 'deployment resources'],
        aliases=['pods total', 'replicasets total', 'pvcs total', 'deployment page cards'],
        time_window='current live cluster snapshot',
        aggregation='cluster-wide counts over all namespaces',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'collectors/kubernetes_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
        ],
    )


def _build_deployment_pods_by_namespace_document(context):
    content = _render_content(
        intro='"Pods by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart shows which namespaces currently have the highest pod counts.',
            'It reflects namespace-level workload distribution in the current cluster snapshot.',
        ],
        calculation=[
            'The metric is sourced from a live Kubernetes cluster snapshot.',
            'Pods are grouped by namespace, so each bar represents one namespace.',
            'For each namespace, the value is the count of pods whose metadata namespace matches that namespace.',
            'The chart sorts namespaces by count and displays up to the top eight namespaces, with preferred ordering for kube-system and default when applicable.',
            'The badge shows the total pod count across the whole cluster snapshot.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'It reflects the current cluster state at fetch time rather than a historical trend window.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.pods_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Pods by Namespace',
        content=content,
        tags=['pods by namespace', 'namespace pods', 'pod count by namespace'],
        aliases=['pods by namespace', 'top namespaces by pod count', 'namespace pod count'],
        time_window='current live cluster snapshot',
        aggregation='pod counts grouped by namespace',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'collectors/kubernetes_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
        ],
    )


def _build_deployment_replicasets_by_namespace_document(context):
    content = _render_content(
        intro='"ReplicaSets by Namespace" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart shows how ReplicaSet workload controllers are distributed across namespaces.',
            'It reflects namespace-level controller footprint in the current cluster snapshot.',
        ],
        calculation=[
            'The metric is sourced from a live Kubernetes cluster snapshot.',
            'ReplicaSets are grouped by namespace, so each bar represents one namespace.',
            'For each namespace, the value is the count of ReplicaSets whose metadata namespace matches that namespace.',
            'The chart sorts namespaces by count and displays up to the top eight namespaces, with preferred ordering for kube-system and default when applicable.',
            'The badge shows the total ReplicaSet count across the whole cluster snapshot.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'It reflects the current cluster state at fetch time rather than a historical trend window.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.replicasets_by_namespace',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs ReplicaSets by Namespace',
        content=content,
        tags=['replicasets by namespace', 'namespace replicasets', 'replicaset count by namespace'],
        aliases=['replicasets by namespace', 'workload controllers by namespace', 'namespace replicaset count'],
        time_window='current live cluster snapshot',
        aggregation='ReplicaSet counts grouped by namespace',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'collectors/kubernetes_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
        ],
    )


def _build_deployment_pods_by_phase_document(context):
    content = _render_content(
        intro='"Pods by Phase" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart provides a cluster health snapshot by showing how many pods are in each Kubernetes phase.',
            'It reflects whether pods are mostly Running or whether more pods are Pending, Failed, Succeeded, or Unknown.',
        ],
        calculation=[
            'The metric is sourced from a live Kubernetes cluster snapshot.',
            'Pods are grouped by their current Kubernetes status phase, so each slice represents one phase.',
            'For each phase, the value is the count of pods whose current status phase matches that phase.',
            'The badge shows the total pod count across the whole cluster snapshot.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'It reflects the current cluster state at fetch time rather than a historical trend window.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.pods_by_phase',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Pods by Phase',
        content=content,
        tags=['pods by phase', 'pod phase', 'cluster health snapshot'],
        aliases=['pods by phase', 'cluster health snapshot', 'running pending failed pods'],
        time_window='current live cluster snapshot',
        aggregation='pod counts grouped by Kubernetes phase',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'collectors/kubernetes_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
        ],
    )


def _build_deployment_frequency_document(context):
    content = _render_content(
        intro='"Deployment Frequency" is a chart shown on the Deployment KPIs page.',
        represents=[
            'The chart shows the percentage of finished Jenkins builds that achieved a successful deployment stage.',
            'It reflects how often builds end in a successful deployment rather than only how many builds ran.',
        ],
        calculation=[
            'The metric is sourced from stored Jenkins build history for the selected branch.',
            'Only finished builds are eligible.',
            "For each eligible build, the dashboard looks for a stage whose name contains 'deploy to aks'.",
            'A build is counted as a successful deployment when at least one matching deploy stage has status SUCCESS.',
            'successful = count of eligible builds with a successful matching deploy stage.',
            'total = count of eligible finished builds considered by the metric.',
            'deployment_frequency = successful / total * 100.',
            'The doughnut chart shows Successful Deployments versus Other Builds, and the badge shows the resulting percentage.',
            'Other Builds are finished builds that did not achieve a successful matching deployment stage.',
            'In practice, that commonly includes builds that were aborted before deployment, builds that failed before reaching deployment, or builds that reached deployment but had real issues in the deployment stage itself.',
        ],
        notes=[
            'This chart is shown on the Deployment KPIs page.',
            'It is based on stored build-stage history rather than a live Kubernetes snapshot.',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.deployment_frequency',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Deployment Frequency',
        content=content,
        tags=['deployment frequency', 'successful deployments', 'deploy to aks'],
        aliases=['deployment frequency', 'successful deployments across jenkins builds', 'deploy success rate'],
        time_window='all stored finished builds considered for the selected branch',
        aggregation='successful deploy-stage builds divided by total eligible finished builds',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'services/pipeline_storage_service.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
        ],
    )


def _build_deployment_latest_image_artifact_document(context):
    content = _render_content(
        intro='"Latest Image Artifact" is a widget shown on the Deployment KPIs page.',
        represents=[
            'The widget shows Docker image details matched to recent Jenkins build tags.',
            'Docker packages an application and its dependencies into an image, and containers run that image in a consistent and portable way across environments.',
            'Teams use containers so deployments are reproducible, easier to move between environments, and easier to version, roll back, and scale.',
            'This widget helps connect the deployment view with the image artifact that Jenkins built or matched in Docker Hub.',
        ],
        calculation=[
            'The widget tries to identify the latest relevant Docker image artifact for the pipeline branch.',
            'If a configured Docker Hub tag is available, the dashboard first tries to load metadata for that tag.',
            'Otherwise, it checks recent finished Jenkins builds, tries to match a Docker Hub repository tag to the build number and branch, and if needed falls back to parsing a successful Docker-related Jenkins build log for the image name and tag.',
            'When a match is found, the widget displays the build number, build result, image name, tag, size, and timestamp for the image artifact.',
            'The badge shows the matched image tag when available, otherwise it falls back to a generic Docker Hub label.',
        ],
        notes=[
            'This widget is meant to show which built image artifact is associated with the recent deployment flow, not the full history of all Docker images.',
            'If you want to browse previous Docker images directly, use https://hub.docker.com/repository/docker/tasnimelleuchenis/django-contact-app/',
        ],
    )
    return _document(
        context=context,
        document_key='deployment_kpis.latest_image_artifact',
        dashboard_page='deployment_kpis',
        title='Deployment KPIs Latest Image Artifact',
        content=content,
        tags=['latest image artifact', 'docker image', 'docker hub', 'image artifact'],
        aliases=['latest image artifact', 'docker image details', 'latest docker image', 'docker hub image'],
        time_window='latest matched Docker image artifact associated with recent finished Jenkins builds or configured Docker Hub tag',
        aggregation='single latest matched image artifact record',
        source_endpoints=['/deployment_kpis/api/cluster'],
        source_files=[
            'services/deployment_kpis_service.py',
            'services/docker_image_service.py',
            'collectors/docker_image_collector.py',
            'static/js/deployment_kpis.js',
            'templates/deployment_kpis.html',
            'config.py',
        ],
    )


def _build_github_page_overview_document(context):
    content = _render_content(
        intro='The GitHub Repo page is a repo-level dashboard page that mixes live GitHub metadata, Jenkins-linked failure context, and Postgres-backed commit analytics.',
        represents=[
            'The Repository, Latest Branch Commits, and pull-request sections are live repository views for the configured GitHub owner and repository.',
            'The Last Failed Main Pipeline Commit, Fix for Latest Main Failure, and Time to Fix sections correlate Jenkins main-pipeline builds with GitHub commits.',
            'The Most Changed Files and Code Churn sections are analytics for the main branch over the last 24 hours, built from stored GitHub commit history rather than from a one-shot live GitHub list call.',
        ],
        calculation=[
            'The page frontend loads a single payload from `/api/github`.',
            'That payload combines `get_repo`, `get_branches`, `get_pull_requests`, `get_all_builds(branch_name="main")`, Jenkins build-detail lookups, and stored commit analytics from `get_cached_github_24h_commit_details(owner, repo, "main")`.',
            'Repository and pull-request sections are repo-wide, while failure attribution and 24-hour analytics are hard-wired to the main branch in the current implementation.',
        ],
        notes=[
            'This document explains the page structure and data sources, not the current numbers visible on the page.',
            'The page is available through the GitHub dashboard route and its documentation is stored in the same `dashboard_kpi_documents` table as the Overview, Pipeline KPIs, and Deployment KPIs docs.',
        ],
    )
    return _document(
        context=context,
        document_key='github.page_overview',
        dashboard_page='github',
        title='GitHub Repo Page Overview',
        content=content,
        tags=[
            'github repo page',
            'github dashboard',
            'github page overview',
            'repo page sections',
        ],
        aliases=[
            'github repo page',
            'github page',
            'github dashboard page',
            'what is on the github repo page',
        ],
        time_window='mixed: live GitHub repo snapshot plus main-branch 24-hour analytics and latest Jenkins failure context',
        aggregation='single `/api/github` response composed from GitHub API, Jenkins API, and stored GitHub commit history',
        source_endpoints=['/api/github'],
        source_files=[
            'github/routes.py',
            'services/github_service.py',
            'services/github_storage_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_repository_document(context):
    content = _render_content(
        intro='The Repository section is the top summary card on the GitHub Repo page.',
        represents=[
            'It shows the current repository identity and metadata for the configured GitHub repository.',
            'The repo title, description, stars, forks, open issues, default branch, primary language, updated timestamp, and repository link all come from the GitHub repository record.',
        ],
        calculation=[
            'The backend calls `get_repo(owner, repo)`, which fetches `GET /repos/{owner}/{repo}` from the GitHub API.',
            'Stars = `stargazers_count`.',
            'Forks = `forks_count`.',
            'Open Issues = `open_issues_count`.',
            'Default Branch = `default_branch`.',
            'Primary Language = `language`.',
            'Updated = `updated_at`.',
            'The Open Repository link uses the repo `html_url`.',
            'If the repo payload is missing the main metadata fields, the UI shows `Repository data unavailable.` and falls back to em dashes for missing values.',
        ],
        notes=[
            'This section is a live repository snapshot at fetch time, not a historical trend metric.',
        ],
    )
    return _document(
        context=context,
        document_key='github.repository_summary',
        dashboard_page='github',
        title='GitHub Repository Summary',
        content=content,
        tags=[
            'github repository summary',
            'stars',
            'forks',
            'open issues',
            'default branch',
            'primary language',
        ],
        aliases=[
            'repository card',
            'repo summary',
            'stars forks open issues',
            'github repository metadata',
        ],
        time_window='current GitHub repository snapshot',
        aggregation='single GitHub repository metadata record',
        source_endpoints=['/api/github'],
        source_files=[
            'collectors/github_collector.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_latest_branch_commits_document(context):
    content = _render_content(
        intro='Latest Branch Commits is the branch-head commit list on the GitHub Repo page.',
        represents=[
            'It shows the most recent commit for each selected branch and lets users jump to that commit on GitHub.',
            'The list is branch-head oriented, not a full commit history feed.',
        ],
        calculation=[
            'The backend first fetches up to 100 branches from GitHub with `get_branches(owner, repo, per_page=100)`.',
            'It then selects only the first branch subset returned by GitHub: 12 branches when a GitHub token is configured, otherwise 6 branches.',
            'For each selected branch, the backend calls `get_latest_commit_for_branch(owner, repo, branch_name)` and attaches the branch name to the returned commit.',
            'The collected branch-head commits are sorted by commit datetime descending before being sent to the UI.',
            'Each row shows the short SHA, the first line of the commit message, the author name, the commit timestamp, the branch pill, and a GitHub commit link when available.',
            'The author name prefers commit author metadata, then GitHub user name, then committer name.',
            'The scope label says `Most recent commit on each branch` only when every fetched branch is represented. If the list is truncated, it says `Most recent commit on the first N branches returned by GitHub`.',
            'A Tag button is rendered only for commits on `main` or branches starting with `release/`.',
            'Tag creation is additionally validated server-side so the tagged SHA must still be the current head commit of that branch.',
        ],
        notes=[
            'This is not guaranteed to cover every branch in the repository because the backend intentionally caps the branch-head fetch count.',
            'Branch ordering depends on the GitHub branches API response order before the dashboard applies its own date sort to the returned head commits.',
        ],
    )
    return _document(
        context=context,
        document_key='github.latest_branch_commits',
        dashboard_page='github',
        title='GitHub Latest Branch Commits',
        content=content,
        tags=[
            'latest branch commits',
            'branch head commits',
            'tag commit',
            'github branches',
        ],
        aliases=[
            'latest branch commits',
            'branch commits',
            'most recent commit on each branch',
            'github tag button',
        ],
        time_window='latest available commit per selected branch at GitHub fetch time',
        aggregation='one branch-head commit per selected branch, sorted newest first',
        source_endpoints=['/api/github', '/api/github/tag'],
        source_files=[
            'collectors/github_collector.py',
            'github/routes.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_open_pull_requests_document(context):
    content = _render_content(
        intro='Open Pull Requests is the live open-PR list on the GitHub Repo page.',
        represents=[
            'It shows repository pull requests that are still open, including drafts.',
            'This section is repo-wide and is not restricted to the main branch.',
        ],
        calculation=[
            'The backend fetches pull requests with `get_pull_requests(owner, repo, state="all", per_page=30)`.',
            'That GitHub API request uses `state=all`, `sort=updated`, and `direction=desc`, so the input set is the latest updated pull requests returned by GitHub.',
            'Each pull request is formatted from the GitHub payload with its number, title, state, author details, created and updated timestamps, `merged_at`, `additions`, `deletions`, `changed_files`, `comments`, and `review_comments`.',
            'A pull request goes into the Open Pull Requests section when `state == "open"` and `merged_at` is empty.',
            'The frontend renders only the first 10 open pull requests from that already-filtered list.',
            'Each rendered PR row shows the PR number, title link, author, updated date, changed-file count when available, and additions/deletions when available.',
        ],
        notes=[
            'Draft PRs are included because they are still open pull requests in the current implementation.',
            'If no open pull requests are available in the fetched set, the UI shows `No open pull requests.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.open_pull_requests',
        dashboard_page='github',
        title='GitHub Open Pull Requests',
        content=content,
        tags=[
            'open pull requests',
            'github prs',
            'repo pull requests',
            'draft pull requests',
        ],
        aliases=[
            'open prs',
            'github open prs',
            'pull requests',
            'repo pr list',
        ],
        time_window='latest updated pull requests returned by GitHub at fetch time',
        aggregation='repo-wide pull requests filtered to open state, then capped to 10 rendered rows',
        source_endpoints=['/api/github'],
        source_files=[
            'collectors/github_collector.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_merged_pull_requests_document(context):
    content = _render_content(
        intro='Recently Merged is the merged-pull-request list on the GitHub Repo page.',
        represents=[
            'It shows pull requests that have been merged into the repository, regardless of their final closed state label.',
            'This section is repo-wide and is not restricted to the main branch.',
        ],
        calculation=[
            'The backend uses the same `get_pull_requests(owner, repo, state="all", per_page=30)` source set as the open-PR section.',
            'A pull request is treated as merged when `merged_at` is present.',
            'Merged PRs are separated from open and closed-unmerged PRs after formatting.',
            'The frontend renders only the first 10 merged pull requests from that merged list.',
            'Each rendered PR row shows the PR number, title link, author, merged date, changed-file count when available, and additions/deletions when available.',
            'The date shown in this section is `merged_at`, while the open-PR section shows `updated_at`.',
        ],
        notes=[
            'Closed but unmerged pull requests are collected server-side into a separate list, but they are not displayed anywhere on the current GitHub page.',
            'If no merged pull requests are available in the fetched set, the UI shows `No merged pull requests yet.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.merged_pull_requests',
        dashboard_page='github',
        title='GitHub Recently Merged Pull Requests',
        content=content,
        tags=[
            'merged pull requests',
            'recently merged',
            'github merged prs',
        ],
        aliases=[
            'merged prs',
            'recently merged prs',
            'github merged pull requests',
        ],
        time_window='latest updated pull requests returned by GitHub at fetch time',
        aggregation='repo-wide pull requests filtered to merged items, then capped to 10 rendered rows',
        source_endpoints=['/api/github'],
        source_files=[
            'collectors/github_collector.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_failed_pipeline_commit_document(context):
    content = _render_content(
        intro='Last Failed Main Pipeline Commit is the Jenkins-linked failure attribution card on the GitHub Repo page.',
        represents=[
            'It identifies the latest failed Jenkins build on the main pipeline and the GitHub commit the dashboard treats as the likely failing commit.',
            'The `Failed by` user card is built from the selected GitHub commit author or committer metadata.',
        ],
        calculation=[
            'The backend fetches Jenkins builds with `get_all_builds(branch_name="main")`.',
            'It picks the first build whose Jenkins result is `FAILURE` by calling `get_last_failed_build(builds=builds, branch_name="main")`.',
            'The backend then loads detailed Jenkins build data with `get_build_info(build_number, branch_name="main")`.',
            'From that build detail, it extracts up to 5 commits from Jenkins `changeSets` and up to 3 culprits from Jenkins `culprits`.',
            'To choose the failing commit SHA, the dashboard first normalizes culprit full names and commit author names by collapsing whitespace and case-folding them.',
            'It then picks the first extracted build commit whose author name matches a Jenkins culprit name.',
            'If no culprit-to-author match is found, it falls back to `extract_build_commit_sha`, which checks `actions.lastBuiltRevision.SHA1`, then a `GIT_COMMIT` parameter, then the first Jenkins change-set `commitId`.',
            'Once a SHA is selected, the backend tries to load the live GitHub commit for that SHA. If GitHub detail is missing, it falls back to Jenkins change-set message and author data.',
            'The UI shows the chosen commit short SHA, the commit message first line, the Jenkins build number, the commit date, and a Jenkins build link when available.',
            'The `Failed by` card uses the chosen commit author or committer profile fields from GitHub when available, not the raw Jenkins culprit URL directly.',
        ],
        notes=[
            'This section is hard-wired to the main branch in the current implementation.',
            'If multiple commits in the Jenkins change set share the same culprit name, the first matching commit in the extracted list wins.',
            'If no failed build or no commit SHA can be resolved, the page shows `No failed build commit found.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.failed_main_pipeline_commit',
        dashboard_page='github',
        title='GitHub Last Failed Main Pipeline Commit',
        content=content,
        tags=[
            'last failed main pipeline commit',
            'failed pipeline commit',
            'failed by user',
            'jenkins culprit',
        ],
        aliases=[
            'who failed the pipeline',
            'user that failed the pipeline',
            'failed by',
            'latest failed main build commit',
        ],
        time_window='latest failed Jenkins build on the main pipeline',
        aggregation='single latest failed main build correlated to one selected GitHub commit SHA',
        source_endpoints=['/api/github'],
        source_files=[
            'collectors/jenkins_collector.py',
            'collectors/github_collector.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_fix_commit_document(context):
    content = _render_content(
        intro='Fix for Latest Main Failure is the recovery-commit card paired with the failed main pipeline commit section.',
        represents=[
            'It shows the successful Jenkins build the dashboard treats as the recovery for the latest failed main build and the GitHub commit associated with that successful build.',
            'Depending on the SHA match, it can indicate either a new fixing commit or a later successful rerun of the same commit.',
        ],
        calculation=[
            'After identifying the latest failed main build, the backend searches the Jenkins build list for the fix build with `_find_fix_build_for_failure`.',
            'That helper sorts builds by build number descending and keeps the most recent successful build seen while scanning backward toward the failed build.',
            'When the scan reaches the failed build number, the latest success encountered on the way is returned as the fix build.',
            'The backend loads that successful build detail, extracts its commit SHA with `extract_build_commit_sha`, and then fetches the live GitHub commit when possible.',
            'If GitHub detail is unavailable, the dashboard falls back to Jenkins change-set message and author data for the fix commit.',
            'The UI label is `Recovered in` when the fix build SHA is the same as the failed SHA, and `Fixed by` when the successful build points to a different SHA.',
            'The card also shows the successful Jenkins build number and links to the successful build and commit when those URLs are available.',
        ],
        notes=[
            'This section is hard-wired to the main branch in the current implementation.',
            'If no later successful build is found or no fix SHA can be resolved, the page shows `No fix commit found yet.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.fix_for_latest_main_failure',
        dashboard_page='github',
        title='GitHub Fix for Latest Main Failure',
        content=content,
        tags=[
            'fix for latest main failure',
            'fix commit',
            'recovered in',
            'fixed by',
        ],
        aliases=[
            'fix commit for failed pipeline',
            'latest main failure fix',
            'recovery commit',
        ],
        time_window='latest successful Jenkins build found after the latest failed main build',
        aggregation='single successful main build correlated to one selected GitHub commit SHA',
        source_endpoints=['/api/github'],
        source_files=[
            'collectors/jenkins_collector.py',
            'collectors/github_collector.py',
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_time_to_fix_document(context):
    content = _render_content(
        intro='Time to Fix is the recovery-duration section for the latest failed main pipeline build.',
        represents=[
            'It shows how long it took to go from the failed main Jenkins build to the successful build the dashboard treats as the recovery.',
            'This is a build-to-build recovery metric, not a developer effort estimate.',
        ],
        calculation=[
            'The metric is only rendered when both a failed main build and a resolved fix build are available.',
            'Failure time prefers the failed Jenkins build timestamp. If that timestamp is missing, the dashboard falls back to the failed commit date.',
            'Fix time prefers the successful Jenkins build timestamp. If that timestamp is missing, the dashboard falls back to the fix commit date.',
            'Time to Fix = fix timestamp - failure timestamp.',
            'The UI formats the difference as days, hours, minutes, and seconds depending on the size of the gap.',
            'If the computed difference is negative, the UI does not render a duration and instead shows `Fix appears to be before failure.`',
        ],
        notes=[
            'Because Jenkins build timestamps are preferred over commit author timestamps, this metric is closest to pipeline recovery time rather than commit-authoring time.',
            'This section is hard-wired to the main branch in the current implementation.',
        ],
    )
    return _document(
        context=context,
        document_key='github.time_to_fix_latest_main_failure',
        dashboard_page='github',
        title='GitHub Time to Fix',
        content=content,
        tags=[
            'time to fix',
            'pipeline recovery time',
            'failed build to successful build',
        ],
        aliases=[
            'time to fix latest main failure',
            'recovery duration',
            'failed build to success time',
        ],
        time_window='from the latest failed main build timestamp to the selected successful recovery build timestamp',
        aggregation='single build-to-build duration difference',
        source_endpoints=['/api/github'],
        source_files=[
            'services/github_service.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_most_changed_files_document(context):
    content = _render_content(
        intro='Most Changed Files in the last 24 hours is the file-activity ranking on the GitHub Repo page.',
        represents=[
            'It ranks which main-branch files changed the most across the stored 24-hour commit window.',
            'The section focuses on file-level activity, not pull requests or branch-head snapshots.',
        ],
        calculation=[
            'The backend loads a cached analytics payload from `get_cached_github_24h_commit_details(owner, repo, "main")`.',
            'That payload comes from Postgres-backed GitHub commit storage for main-branch commits whose `committed_at` falls within the last 24 hours.',
            'For each commit file entry, the dashboard groups rows by filename across the 24-hour main-branch window.',
            'For each file, `touches` increments every time the file appears in a commit.',
            'For each file, `additions` and `deletions` are summed across all appearances of that file.',
            'For each file, `line_changes = additions + deletions`.',
            'The dashboard also counts how many times the file was seen with normalized statuses `added`, `modified`, `removed`, and `renamed`.',
            'Files are ranked by descending `line_changes`, then descending `touches`, then filename ascending.',
            'The backend computes totals across all changed files, but the UI displays only the top 5 ranked files.',
            'The summary pills show total changed files, commit count in the 24-hour window, an optional commit-detail coverage pill when file backfill is partial, and total lines changed.',
        ],
        notes=[
            'This section is hard-wired to the main branch in the current implementation.',
            'If 24-hour commits exist but none currently have file details in storage, the UI says file-level analytics are still being backfilled.',
            'If no main-branch commits exist in the last 24 hours, the UI shows `No files were changed on main in the last 24 hours.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.most_changed_files_24h',
        dashboard_page='github',
        title='GitHub Most Changed Files in the Last 24 Hours',
        content=content,
        tags=[
            'most changed files',
            'file activity',
            'top changed files',
            '24h file changes',
        ],
        aliases=[
            'most changed files in the last 24 hours',
            'top 5 most changed files',
            'file touches',
            'github file activity',
        ],
        time_window='main-branch commits stored in the last 24 hours',
        aggregation='file-level additions, deletions, touches, and status counts grouped by filename',
        source_endpoints=['/api/github'],
        source_files=[
            'services/github_storage_service.py',
            'services/github_service.py',
            'github_storage_models.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_github_code_churn_document(context):
    content = _render_content(
        intro='Code Churn in the last 24 hours is the line-change summary on the GitHub Repo page.',
        represents=[
            'It summarizes how many lines were added and deleted across stored main-branch commits in the last 24 hours.',
            'This is a line-based churn metric, not a repository size metric and not a pull-request count.',
        ],
        calculation=[
            'The metric uses the same Postgres-backed 24-hour main-branch commit payload as the Most Changed Files section.',
            'For each stored commit in the 24-hour window, the dashboard first tries to read `commit.stats.additions` and `commit.stats.deletions`.',
            'If those commit-level stats are missing, it falls back to summing file-level additions and deletions across the commit files.',
            'Total Lines Added = sum of commit additions across the 24-hour main-branch window.',
            'Total Lines Deleted = sum of commit deletions across the 24-hour main-branch window.',
            'Total Lines Changed = additions + deletions.',
            'Net Change = additions - deletions.',
            'Changed Files is taken from the total distinct-file count already computed by the 24-hour file-change dataset.',
            'The frontend visualization renders two bars only: one for added lines and one for deleted lines.',
            'Each bar width is normalized against the larger of additions or deletions in that 24-hour window, so the width is a relative comparison rather than a percentage of repository size.',
        ],
        notes=[
            'This section is hard-wired to the main branch in the current implementation.',
            'If 24-hour commits exist but none currently have file details in storage, the UI says the detail backfill is still in progress.',
            'If additions, deletions, and commit count are all zero for the window, the UI shows `No code churn recorded on main in the last 24 hours.`',
        ],
    )
    return _document(
        context=context,
        document_key='github.code_churn_24h',
        dashboard_page='github',
        title='GitHub Code Churn in the Last 24 Hours',
        content=content,
        tags=[
            'code churn',
            'lines added',
            'lines deleted',
            '24h code churn',
        ],
        aliases=[
            'code churn in the last 24 hours',
            'lines added and deleted',
            'github churn',
            'line changes',
        ],
        time_window='main-branch commits stored in the last 24 hours',
        aggregation='sum of commit additions and deletions across the stored 24-hour main-branch window',
        source_endpoints=['/api/github'],
        source_files=[
            'services/github_storage_service.py',
            'services/github_service.py',
            'github_storage_models.py',
            'templates/github.html',
            'static/js/github.js',
        ],
    )


def _build_finops_page_overview_document(context):
    content = _render_content(
        intro='The FinOps page is a monthly Azure-cost dashboard page that explains subscription-level daily actual cost for a selected month.',
        represents=[
            'The page shows one selected calendar month at a time and compares that month against the immediately previous month.',
            'The displayed cards and chart are generic cost KPIs derived from stored daily Azure cost totals, not from `finops_build_documents`.',
            'The page is focused on subscription-wide daily total cost, not service-by-service, resource-group, VM-only, or AKS-only breakdowns.',
        ],
        calculation=[
            'The frontend calls `/api/finops/daily-cost?year=YYYY&month=MM`.',
            'The backend serves data from `get_finops_daily_cost_chart(subscription_id, year, month)`.',
            'That service uses stored rows from `finops_daily_costs` when the month is already synced and fresh, and otherwise refreshes the current month and previous month from Azure Cost Management before returning the stored payload.',
            'The Azure query uses `ActualCost`, daily granularity, no grouping, and a single aggregation: sum of `PreTaxCost` over the whole subscription scope.',
            'The month selector offers the current month plus the previous 11 months and formats them as `YYYY-MM`.',
        ],
        notes=[
            'This page documents only the displayed FinOps KPIs and intentionally stays separate from the date-specific narrative stored in `finops_build_documents`.',
            'The FinOps dashboard route is currently admin-only.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.page_overview',
        dashboard_page='finops',
        title='FinOps Page Overview',
        content=content,
        tags=[
            'finops page',
            'monthly azure cost',
            'daily actual azure cost',
            'subscription cost dashboard',
        ],
        aliases=[
            'finops dashboard page',
            'what is on the finops page',
            'finops monthly cost page',
        ],
        time_window='selected calendar month, with the immediately previous month used for comparison',
        aggregation='subscription-wide daily sum of Azure PreTaxCost, stored in finops_daily_costs and returned through /api/finops/daily-cost',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'finops/routes.py',
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
            'finops_models.py',
        ],
    )


def _build_finops_total_cost_document(context):
    content = _render_content(
        intro='Total is the first summary card on the FinOps page.',
        represents=[
            'It shows the total actual Azure cost for the selected calendar month.',
            'This is a month total for the whole subscription scope used by the FinOps page.',
        ],
        calculation=[
            'The backend builds one daily row per calendar day in the selected month and fills missing days with `0.0` cost.',
            'Total = sum of `row.total` across every day row in the selected month.',
            'The meta line under the card compares the selected month total against the previous month total using `current_total_cost - previous_total_cost`.',
            'That delta amount is shown as currency, not as a percentage, on the Total card meta line.',
        ],
        notes=[
            'Because the month is padded with every calendar day, the total stays a full-month figure even when some days have zero cost or no returned Azure value yet.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.total_cost',
        dashboard_page='finops',
        title='FinOps Total Cost',
        content=content,
        tags=[
            'finops total',
            'monthly total cost',
            'total azure cost',
        ],
        aliases=[
            'total cost card',
            'finops total cost',
            'monthly cost total',
        ],
        time_window='selected calendar month',
        aggregation='sum of daily subscription total_cost values across every day in the selected month',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
        ],
    )


def _build_finops_average_daily_cost_document(context):
    content = _render_content(
        intro='Average / day is the second summary card on the FinOps page.',
        represents=[
            'It shows the mean daily Azure cost for the selected calendar month.',
            'This is a full-month calendar-day average for the whole subscription scope used by the page.',
        ],
        calculation=[
            'Average / day = monthly total cost / number of calendar days in the selected month.',
            'The backend computes this as `total_cost / len(rows)`, where `rows` already contains one day entry for every calendar day in the month.',
            'The meta line compares the selected month average daily cost against the previous month average daily cost using `current_average_daily_cost - previous_average_daily_cost`.',
        ],
        notes=[
            'This average is over all calendar days in the selected month, not only over days that had non-zero cost.',
            'For a partial current month, future or missing days can still be represented as zero-cost day rows in the current implementation.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.average_daily_cost',
        dashboard_page='finops',
        title='FinOps Average Daily Cost',
        content=content,
        tags=[
            'average day',
            'average daily cost',
            'finops average',
        ],
        aliases=[
            'average / day',
            'finops average day',
            'daily average cost card',
        ],
        time_window='selected calendar month',
        aggregation='monthly total cost divided by the number of calendar days in the selected month',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
        ],
    )


def _build_finops_highest_day_document(context):
    content = _render_content(
        intro='Highest day is the peak-day summary card on the FinOps page.',
        represents=[
            'It identifies the day in the selected month that had the highest daily total Azure cost and shows that day together with its cost.',
            'This is the peak daily total within the selected month for the full subscription scope used by the page.',
        ],
        calculation=[
            'The backend finds the day row with the maximum `total` value across the selected month.',
            'Highest day date = `day` of the row with the maximum daily total.',
            'Highest day cost = that same row `total` value.',
            'The card value is rendered as `YYYY-MM-DD (currency)`.',
            'The meta line compares the selected month peak-day cost against the previous month peak-day cost using `current_highest_day_cost - previous_highest_day_cost`.',
        ],
        notes=[
            'This comparison is between each month’s maximum daily cost, not between the same calendar date in both months.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.highest_day',
        dashboard_page='finops',
        title='FinOps Highest Day',
        content=content,
        tags=[
            'highest day',
            'peak day cost',
            'finops peak day',
        ],
        aliases=[
            'highest day card',
            'peak day',
            'highest cost day',
        ],
        time_window='selected calendar month',
        aggregation='maximum daily subscription total_cost value within the selected month',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
        ],
    )


def _build_finops_vs_previous_month_document(context):
    content = _render_content(
        intro='Vs previous month is the month-over-month comparison card on the FinOps page.',
        represents=[
            'It shows how the selected month total cost changed relative to the previous month total cost.',
            'The main value is a percentage change, while the meta line repeats the absolute currency change in total cost.',
        ],
        calculation=[
            'The card uses the Total card’s monthly total-cost figures as its base.',
            'Absolute change amount = `current_total_cost - previous_total_cost`.',
            'Percentage change = `((current_total_cost - previous_total_cost) / previous_total_cost) * 100`.',
            'If the previous month total cost is zero, the percentage is unavailable and the card value shows `-`.',
            'The card color trend is driven by the absolute amount change: positive amount = increase styling, negative amount = decrease styling, zero = neutral styling.',
        ],
        notes=[
            'This card compares whole-month totals only. It is not a week-over-week or day-over-day metric.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.vs_previous_month',
        dashboard_page='finops',
        title='FinOps Vs Previous Month',
        content=content,
        tags=[
            'vs previous month',
            'month over month cost change',
            'finops month change',
        ],
        aliases=[
            'previous month comparison',
            'month change card',
            'finops vs previous month',
        ],
        time_window='selected calendar month compared with the immediately previous calendar month',
        aggregation='percentage and amount change between current month total cost and previous month total cost',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
        ],
    )


def _build_finops_daily_total_cost_chart_document(context):
    content = _render_content(
        intro='Daily Total Cost is the bar chart on the FinOps page.',
        represents=[
            'It plots the daily actual Azure cost for every day in the selected month.',
            'This is a daily time series for the whole subscription scope used by the page, not a cumulative running total.',
        ],
        calculation=[
            'The chart labels come from every calendar day in the selected month, formatted as ISO dates such as `YYYY-MM-DD`.',
            'The bar values come from the `series.total` array returned by `/api/finops/daily-cost`.',
            'Each bar is one day’s subscription-wide total cost for that date.',
            'The Y-axis is currency and starts at zero.',
            'The subtitle compares the selected month label with the previous month label.',
            'The API payload also includes `series.previous_month_total`, but the current frontend does not plot that comparison series on the chart.',
        ],
        notes=[
            'Because the backend pads the month with zero-cost rows, missing or future days in a partial month can appear as zero-height bars.',
            'If the payload columns are invalid or the series length does not match the labels length, the page shows an error instead of rendering the chart cleanly.',
        ],
    )
    return _document(
        context=context,
        document_key='finops.daily_total_cost_chart',
        dashboard_page='finops',
        title='FinOps Daily Total Cost Chart',
        content=content,
        tags=[
            'daily total cost',
            'finops chart',
            'daily cost chart',
        ],
        aliases=[
            'daily total cost chart',
            'finops daily cost bars',
            'monthly daily cost chart',
        ],
        time_window='every calendar day in the selected month',
        aggregation='one subscription-wide daily total_cost bar per calendar day in the selected month',
        source_endpoints=['/api/finops/daily-cost'],
        source_files=[
            'services/finops_storage_service.py',
            'services/finops_service.py',
            'templates/finops.html',
            'static/js/finops.js',
        ],
    )


def _build_ecoops_page_overview_document(context):
    content = _render_content(
        intro='The EcoOps page is a real-time sustainability dashboard page synchronized with Prometheus.',
        represents=[
            'The page shows live CO2 estimation for the AKS cluster, the Azure virtual machine, and their combined latest-hour footprint.',
            'The displayed values are calculated from live Prometheus CPU and memory telemetry rather than from stored historical documents.',
            'The visible page sections are AKS CO2 / hour, VM CO2 / hour, Combined last hour, and the CO2 Emission Rate - Latest Hour chart.',
        ],
        calculation=[
            'The frontend loads `/api/ecoops/live` on page load and refreshes it every 30 seconds.',
            'The backend queries Prometheus live for VM and cluster capacity plus the last 60 minutes of CPU and RAM history.',
            'The current implementation uses a 60-minute window and a 60-second step between history points.',
            'The power and CO2 formulas are: `CPU Power = vCPUs x [0.74 + (CPU% / 100) x 2.76]`, `RAM Power = RAM used (GB) x 0.38`, `Wall Power = (CPU Power + RAM Power) x 1.125`, and `CO2 / hour = (Wall Power / 1000) x 56`.',
            'AKS and VM are calculated separately from their own Prometheus series, and combined figures are built by summing those two resource estimates.',
        ],
        notes=[
            'If EcoOps metrics are missing, verify that Prometheus is reachable with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0`.',
            'If the page is still empty after that, also check that the Azure VM and AKS cluster are up and exposing metrics to Prometheus.',
            'The EcoOps dashboard route is currently admin-only.',
        ],
    )
    return _document(
        context=context,
        document_key='ecoops.page_overview',
        dashboard_page='ecoops',
        title='EcoOps Page Overview',
        content=content,
        tags=[
            'ecoops page',
            'real time prometheus',
            'co2 estimation',
            'live sustainability telemetry',
        ],
        aliases=[
            'ecoops dashboard page',
            'what is on the ecoops page',
            'ecoops overview',
        ],
        time_window='live Prometheus snapshot plus the latest 60 minutes of Prometheus history',
        aggregation='AKS and VM power and CO2 estimates derived from Prometheus CPU and RAM telemetry',
        source_endpoints=['/api/ecoops/live'],
        source_files=[
            'ecoops/routes.py',
            'services/ecoops_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'templates/ecoops.html',
            'static/js/ecoops.js',
        ],
    )


def _build_ecoops_aks_co2_hour_document(context):
    content = _render_content(
        intro='AKS CO2 / hour is the first summary card on the EcoOps page.',
        represents=[
            'It shows the current estimated CO2 emission rate for the AKS cluster in grams of CO2 equivalent per hour.',
            'The card is based on the latest available AKS CPU and RAM readings from Prometheus inside the live one-hour telemetry window.',
        ],
        calculation=[
            'The backend queries live AKS Prometheus history for cluster CPU percentage, cluster RAM percentage, total cluster RAM bytes, and total cluster vCPUs.',
            'Current AKS RAM used in GB = total cluster RAM GB x (latest cluster RAM% / 100).',
            'The power and CO2 formulas are: `CPU Power = vCPUs x [0.74 + (CPU% / 100) x 2.76]`, `RAM Power = RAM used (GB) x 0.38`, `Wall Power = (CPU Power + RAM Power) x 1.125`, and `CO2 / hour = (Wall Power / 1000) x 56`.',
            'The card value shows the latest computed AKS `co2_hour_g`.',
            'The card meta line shows the latest AKS wall power in watts together with the cluster vCPU count.',
        ],
        notes=[
            'This is a real-time Prometheus-derived estimate, not a cloud billing metric.',
        ],
    )
    return _document(
        context=context,
        document_key='ecoops.aks_co2_hour',
        dashboard_page='ecoops',
        title='EcoOps AKS CO2 per Hour',
        content=content,
        tags=[
            'aks co2 hour',
            'aks emissions',
            'aks wall power',
        ],
        aliases=[
            'aks co2 / hour',
            'ecoops aks card',
            'aks co2 per hour',
        ],
        time_window='latest AKS point from the live 60-minute Prometheus window',
        aggregation='current AKS power and CO2 estimate from cluster CPU%, RAM used GB, and vCPU capacity',
        source_endpoints=['/api/ecoops/live'],
        source_files=[
            'services/ecoops_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'templates/ecoops.html',
            'static/js/ecoops.js',
        ],
    )


def _build_ecoops_vm_co2_hour_document(context):
    content = _render_content(
        intro='VM CO2 / hour is the second summary card on the EcoOps page.',
        represents=[
            'It shows the current estimated CO2 emission rate for the Azure virtual machine in grams of CO2 equivalent per hour.',
            'The card is based on the latest available VM CPU and RAM readings from Prometheus inside the live one-hour telemetry window.',
        ],
        calculation=[
            'The backend queries live VM Prometheus history for VM CPU percentage, VM RAM percentage, total VM RAM bytes, and total VM vCPUs.',
            'Current VM RAM used in GB = total VM RAM GB x (latest VM RAM% / 100).',
            'The power and CO2 formulas are: `CPU Power = vCPUs x [0.74 + (CPU% / 100) x 2.76]`, `RAM Power = RAM used (GB) x 0.38`, `Wall Power = (CPU Power + RAM Power) x 1.125`, and `CO2 / hour = (Wall Power / 1000) x 56`.',
            'The card value shows the latest computed VM `co2_hour_g`.',
            'The card meta line shows the latest VM wall power in watts together with the VM vCPU count.',
        ],
        notes=[
            'This is a real-time Prometheus-derived estimate, not a billing or electricity-meter reading.',
        ],
    )
    return _document(
        context=context,
        document_key='ecoops.vm_co2_hour',
        dashboard_page='ecoops',
        title='EcoOps VM CO2 per Hour',
        content=content,
        tags=[
            'vm co2 hour',
            'vm emissions',
            'vm wall power',
        ],
        aliases=[
            'vm co2 / hour',
            'ecoops vm card',
            'vm co2 per hour',
        ],
        time_window='latest VM point from the live 60-minute Prometheus window',
        aggregation='current VM power and CO2 estimate from VM CPU%, RAM used GB, and vCPU capacity',
        source_endpoints=['/api/ecoops/live'],
        source_files=[
            'services/ecoops_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'templates/ecoops.html',
            'static/js/ecoops.js',
        ],
    )


def _build_ecoops_combined_last_hour_document(context):
    content = _render_content(
        intro='Combined last hour is the third summary card on the EcoOps page.',
        represents=[
            'It shows the estimated combined CO2 emitted by AKS plus the VM over the latest hour.',
            'This card is a last-hour total, not just the current combined CO2-per-hour rate.',
        ],
        calculation=[
            'AKS and VM each get their own 60-minute `co2_hour_g` time series from the same Prometheus-backed formulas.',
            'The page uses a 60-second step, so each point represents 1/60 of an hour.',
            'For each resource, `CO2 last hour = sum(CO2 / hour at each point x 1/60 hour)` across the latest 60 minutes.',
            'Combined last hour = AKS `co2_last_hour_g` + VM `co2_last_hour_g`.',
            'The card meta line shows current combined wall power, which is the latest AKS wall power plus the latest VM wall power.',
        ],
        notes=[
            'This card is an estimated last-hour accumulation built from Prometheus history, so it differs from the current instantaneous CO2/hour cards.',
        ],
    )
    return _document(
        context=context,
        document_key='ecoops.combined_last_hour',
        dashboard_page='ecoops',
        title='EcoOps Combined Last Hour',
        content=content,
        tags=[
            'combined last hour',
            'combined co2',
            'latest hour emissions',
        ],
        aliases=[
            'ecoops combined last hour',
            'combined emissions last hour',
            'aks plus vm last hour',
        ],
        time_window='latest 60 minutes of Prometheus history',
        aggregation='AKS last-hour CO2 estimate plus VM last-hour CO2 estimate, with a 60-second step integration',
        source_endpoints=['/api/ecoops/live'],
        source_files=[
            'services/ecoops_service.py',
            'templates/ecoops.html',
            'static/js/ecoops.js',
        ],
    )


def _build_ecoops_latest_hour_chart_document(context):
    content = _render_content(
        intro='CO2 Emission Rate - Latest Hour is the line chart on the EcoOps page.',
        represents=[
            'It shows how AKS, VM, and combined CO2 emission rates behaved across the latest hour.',
            'This is a real-time Prometheus-backed time-series view rather than a monthly or daily aggregate chart.',
        ],
        calculation=[
            'The backend queries the latest 60 minutes of Prometheus history using a 60-second step.',
            'For each timestamp, AKS CO2/hour and VM CO2/hour are computed from the same formulas used by the cards: `CPU Power = vCPUs x [0.74 + (CPU% / 100) x 2.76]`, `RAM Power = RAM used (GB) x 0.38`, `Wall Power = (CPU Power + RAM Power) x 1.125`, and `CO2 / hour = (Wall Power / 1000) x 56`.',
            'Combined CO2/hour at each timestamp = AKS CO2/hour + VM CO2/hour.',
            'The chart renders three lines: AKS, VM, and Combined.',
            'The Y-axis is grams of CO2 equivalent per hour and starts at zero.',
            'The frontend refreshes the whole EcoOps payload every 30 seconds, so the chart stays synchronized with Prometheus.',
        ],
        notes=[
            'If the chart is empty, verify Prometheus reachability with `kubectl port-forward svc/monitoring-kube-prometheus-prometheus -n monitoring 9090:9090 --address 0.0.0.0` and then confirm the Azure VM and AKS cluster are up and exporting metrics.',
        ],
    )
    return _document(
        context=context,
        document_key='ecoops.latest_hour_chart',
        dashboard_page='ecoops',
        title='EcoOps CO2 Emission Rate - Latest Hour',
        content=content,
        tags=[
            'co2 emission rate',
            'latest hour chart',
            'ecoops chart',
            'prometheus live chart',
        ],
        aliases=[
            'ecoops latest hour chart',
            'co2 emission rate latest hour',
            'ecoops line chart',
        ],
        time_window='latest 60 minutes of Prometheus history',
        aggregation='AKS, VM, and combined CO2/hour series sampled every 60 seconds',
        source_endpoints=['/api/ecoops/live'],
        source_files=[
            'services/ecoops_service.py',
            'services/prometheus_queries.py',
            'collectors/prometheus_collector.py',
            'templates/ecoops.html',
            'static/js/ecoops.js',
        ],
    )


def _build_sonarcloud_page_overview_document(context):
    content = _render_content(
        intro='The SonarCloud Scan page is a code-quality dashboard page for the configured SonarCloud project.',
        represents=[
            'The page shows the configured project key, current quality gate status, issue counts, two structural code metrics, and the current quality gate conditions.',
            'The issue counters focus on unresolved SonarCloud issues, while the quality gate section explains whether the project currently passes the configured gate.',
            'Issue cards are interactive and open a drawer with live issue details from SonarCloud.',
        ],
        calculation=[
            'The frontend loads a single payload from `/api/sonarcloud`.',
            'The backend builds that payload from two live SonarCloud API sources fetched in parallel: project measures and quality gate status.',
            'It also computes bug totals by severity using separate SonarCloud issue searches for low, medium, and high bug severities, then sums them into the Bugs card.',
            'The page additionally supports drawer requests through `/api/sonarcloud/issues` for BUG, VULNERABILITY, CODE_SMELL, and SECURITY_HOTSPOT issue lists.',
        ],
        notes=[
            'This page is a live SonarCloud snapshot at fetch time, not a historical trend page.',
            'If the page is empty, the first configuration checks are `SONARCLOUD_PROJECT_KEY` and `SONARCLOUD_TOKEN`.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.page_overview',
        dashboard_page='sonarcloud',
        title='SonarCloud Scan Page Overview',
        content=content,
        tags=[
            'sonarcloud page',
            'sonarqube page',
            'quality scan dashboard',
            'quality gate page',
        ],
        aliases=[
            'sonarcloud dashboard page',
            'what is on the sonarcloud page',
            'sonarqube dashboard page',
        ],
        time_window='current live SonarCloud project snapshot',
        aggregation='single live SonarCloud summary response plus on-demand issue drawer queries',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues'],
        source_files=[
            'sonarcloud/routes.py',
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_quality_gate_document(context):
    content = _render_content(
        intro='Quality Gate is the pass/fail status pill at the top of the SonarCloud Scan page.',
        represents=[
            'It shows whether the configured SonarCloud project currently passes its quality gate.',
            'A failing quality gate means one or more configured gate conditions are currently outside their allowed thresholds.',
        ],
        calculation=[
            'The backend fetches `qualitygates/project_status` from SonarCloud for the configured project key.',
            'The page pill displays the returned project status, typically `OK`, `ERROR`, or `WARN` when present.',
            'The backend also counts how many gate conditions are currently failing by filtering conditions whose status is `ERROR`.',
            'The quality gate is not recalculated locally by Jenkins Monitor; the dashboard only mirrors SonarCloud’s returned gate result and condition details.',
        ],
        notes=[
            'Common ways to recover a failing quality gate are to fix the specific failing conditions first, such as unresolved bugs, unresolved vulnerabilities, high duplication, or coverage-related thresholds configured in SonarCloud.',
            'If the gate status is missing entirely, common causes are an invalid project key, missing token permissions, or a SonarCloud analysis that has not produced a recent project status yet.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.quality_gate_status',
        dashboard_page='sonarcloud',
        title='SonarCloud Quality Gate Status',
        content=content,
        tags=[
            'quality gate',
            'sonarcloud gate',
            'sonarqube gate',
            'pass fail quality gate',
        ],
        aliases=[
            'quality gate pill',
            'sonar quality gate',
            'why did the quality gate fail',
        ],
        time_window='current live SonarCloud project gate snapshot',
        aggregation='SonarCloud-provided project status and failing-condition count',
        source_endpoints=['/api/sonarcloud'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_bugs_document(context):
    content = _render_content(
        intro='Bugs is the first issue KPI card on the SonarCloud Scan page.',
        represents=[
            'It measures unresolved issues that SonarCloud classifies as bugs, meaning likely correctness or logic problems in the code.',
            'Higher bug counts usually indicate more code paths that can behave incorrectly at runtime.',
        ],
        calculation=[
            'The dashboard does not read a single SonarCloud bug metric field for this card.',
            'Instead, it runs three SonarCloud issue searches in parallel for unresolved BUG issues grouped into low, medium, and high severity buckets.',
            'Low bugs = MINOR + INFO, Medium bugs = MAJOR, High bugs = CRITICAL + BLOCKER.',
            'The card value is the sum of those unresolved bug counts across all three severity groups.',
            'Clicking the Bugs card opens a drawer backed by `/api/sonarcloud/issues?type=BUG`.',
        ],
        notes=[
            'Common ways to reduce bug findings are to fix null-handling mistakes, boundary-condition errors, incorrect conditional logic, unsafe assumptions about input shape, and missing tests around failure paths.',
            'When a bug count is unexpectedly high, a practical workflow is to sort by severity first, reproduce the behavior locally, add or strengthen tests, and then refactor only after the failing path is understood.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.bugs',
        dashboard_page='sonarcloud',
        title='SonarCloud Bugs',
        content=content,
        tags=[
            'bugs',
            'sonar bugs',
            'unresolved bugs',
        ],
        aliases=[
            'bugs card',
            'sonarcloud bugs',
            'code correctness issues',
        ],
        time_window='current unresolved SonarCloud BUG issues',
        aggregation='sum of unresolved BUG issue counts across low, medium, and high severity groups',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues', '/api/sonarcloud/bugs'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_vulnerabilities_document(context):
    content = _render_content(
        intro='Vulnerabilities is the second issue KPI card on the SonarCloud Scan page.',
        represents=[
            'It counts unresolved issues that SonarCloud classifies as vulnerabilities, meaning security weaknesses that can expose the application to attack.',
            'This KPI is a security-oriented issue count, not a deployment or infrastructure metric.',
        ],
        calculation=[
            'The backend reads the `vulnerabilities` measure from SonarCloud project measures.',
            'The page displays that live unresolved vulnerability count directly as an integer.',
            'Clicking the Vulnerabilities card opens a drawer backed by `/api/sonarcloud/issues?type=VULNERABILITY`.',
        ],
        notes=[
            'Common ways to reduce vulnerability findings are to validate and sanitize inputs, use parameterized database access, avoid hard-coded secrets, patch insecure dependencies, enforce authentication and authorization checks, and prefer safe cryptography defaults.',
            'If vulnerability counts seem wrong, check whether the latest SonarCloud analysis completed successfully and whether the project measures endpoint is returning current values for the configured project key.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.vulnerabilities',
        dashboard_page='sonarcloud',
        title='SonarCloud Vulnerabilities',
        content=content,
        tags=[
            'vulnerabilities',
            'security issues',
            'sonar vulnerabilities',
        ],
        aliases=[
            'vulnerabilities card',
            'sonarcloud vulnerabilities',
            'security weakness count',
        ],
        time_window='current unresolved SonarCloud VULNERABILITY issues',
        aggregation='live integer vulnerability count returned by SonarCloud measures',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_code_smells_document(context):
    content = _render_content(
        intro='Code Smells is the third issue KPI card on the SonarCloud Scan page.',
        represents=[
            'It counts unresolved issues that SonarCloud classifies as code smells, meaning maintainability problems or design choices that make the code harder to understand, extend, or keep safe over time.',
            'This KPI is about maintainability risk rather than direct runtime breakage.',
        ],
        calculation=[
            'The backend reads the `code_smells` measure from SonarCloud project measures.',
            'The page displays that live unresolved code-smell count directly as an integer.',
            'Clicking the Code Smells card opens a drawer backed by `/api/sonarcloud/issues?type=CODE_SMELL`.',
        ],
        notes=[
            'Common ways to reduce code smells are to split overly large functions, simplify nested conditionals, remove dead code, improve naming, extract repeated logic into shared helpers, and reduce excessive class or module responsibility.',
            'When smell counts are high, a good strategy is to fix the highest-severity or highest-churn areas first so cleanup work improves code that the team actually touches often.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.code_smells',
        dashboard_page='sonarcloud',
        title='SonarCloud Code Smells',
        content=content,
        tags=[
            'code smells',
            'maintainability issues',
            'sonar code smells',
        ],
        aliases=[
            'code smells card',
            'sonarcloud code smells',
            'maintainability debt',
        ],
        time_window='current unresolved SonarCloud CODE_SMELL issues',
        aggregation='live integer code-smell count returned by SonarCloud measures',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_security_hotspots_document(context):
    content = _render_content(
        intro='Security Hotspots is the fourth issue KPI card on the SonarCloud Scan page.',
        represents=[
            'It counts unresolved security hotspots, which are places SonarCloud wants a human to review for secure usage rather than automatic proof of an exploitable vulnerability.',
            'This KPI highlights review-required security-sensitive code paths.',
        ],
        calculation=[
            'The backend reads the `security_hotspots` measure from SonarCloud project measures.',
            'The page displays that live unresolved security-hotspot count directly as an integer.',
            'Clicking the Security Hotspots card opens a drawer backed by `/api/sonarcloud/issues?type=SECURITY_HOTSPOT`.',
        ],
        notes=[
            'Common ways to resolve hotspots are to review the flagged code path carefully, verify whether the usage is actually safe, add explicit validation or access checks where needed, and document or mark the review outcome in SonarCloud when the hotspot is acceptable.',
            'Hotspots are often tied to security-sensitive areas such as authentication, authorization, encryption, deserialization, file access, and external input handling.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.security_hotspots',
        dashboard_page='sonarcloud',
        title='SonarCloud Security Hotspots',
        content=content,
        tags=[
            'security hotspots',
            'security review',
            'sonar hotspots',
        ],
        aliases=[
            'security hotspots card',
            'sonarcloud hotspots',
            'security review items',
        ],
        time_window='current unresolved SonarCloud SECURITY_HOTSPOT issues',
        aggregation='live integer security-hotspot count returned by SonarCloud measures',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_duplication_document(context):
    content = _render_content(
        intro='Duplications is the duplicated-lines-density KPI card on the SonarCloud Scan page.',
        represents=[
            'It shows how much of the codebase SonarCloud considers duplicated, expressed as duplicated lines density.',
            'Higher duplication usually means more copy-paste logic, which increases maintenance cost and raises the chance of inconsistent fixes.',
        ],
        calculation=[
            'The backend reads the `duplicated_lines_density` measure from SonarCloud project measures.',
            'The frontend formats that value as a percentage with two decimal places.',
            'This KPI card is also checked against failing quality gate conditions, so it is visually highlighted when duplication-related conditions are in `ERROR`.',
        ],
        notes=[
            'Common ways to reduce duplication are to extract shared helpers, centralize repeated constants or validation rules, factor common UI fragments or pipeline helpers, and replace copy-paste code with parameterized reusable functions.',
            'Before deduplicating, confirm the repeated code really has the same responsibility; forced abstraction can make code harder to read if the duplicated blocks actually need to diverge.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.duplication_density',
        dashboard_page='sonarcloud',
        title='SonarCloud Duplications',
        content=content,
        tags=[
            'duplications',
            'duplicated lines density',
            'copy paste code',
        ],
        aliases=[
            'duplications card',
            'duplicated lines density',
            'sonar duplication metric',
        ],
        time_window='current live SonarCloud project measure',
        aggregation='live duplicated_lines_density percentage returned by SonarCloud measures',
        source_endpoints=['/api/sonarcloud'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_ncloc_document(context):
    content = _render_content(
        intro='Lines of Code is the ncloc KPI card on the SonarCloud Scan page.',
        represents=[
            'It shows non-comment lines of code, often abbreviated as `ncloc` in SonarCloud.',
            'This metric is primarily a size indicator and should be interpreted together with issue density, duplication, and quality-gate outcomes rather than as a quality score by itself.',
        ],
        calculation=[
            'The backend reads the `ncloc` measure from SonarCloud project measures.',
            'The page displays that live non-comment line count directly as an integer.',
        ],
        notes=[
            'Large ncloc is not automatically a problem, but it often makes unresolved bugs, vulnerabilities, duplication, and review overhead more expensive to manage.',
            'If ncloc grows quickly, useful follow-up checks are whether duplication is rising, whether hotspot review is keeping up, and whether the quality gate still protects new code effectively.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.ncloc',
        dashboard_page='sonarcloud',
        title='SonarCloud Lines of Code',
        content=content,
        tags=[
            'lines of code',
            'ncloc',
            'code size',
        ],
        aliases=[
            'lines of code card',
            'non comment lines of code',
            'sonar ncloc',
        ],
        time_window='current live SonarCloud project measure',
        aggregation='live ncloc integer returned by SonarCloud measures',
        source_endpoints=['/api/sonarcloud'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_quality_gate_conditions_document(context):
    content = _render_content(
        intro='Quality Gate Conditions is the condition list section on the SonarCloud Scan page.',
        represents=[
            'It shows the individual SonarCloud quality gate conditions that determine whether the project passes or fails the quality gate.',
            'Each row shows the metric being checked, the current status, the actual value, and the configured threshold.',
        ],
        calculation=[
            'The backend reads the project quality gate response and extracts every returned condition.',
            'Each condition row includes `metricKey`, `status`, `actualValue`, and `errorThreshold` from SonarCloud.',
            'The page counts a condition as failing when its status is `ERROR`.',
            'The dashboard also uses failing condition metric keys to visually highlight matching KPI cards such as duplications when those metric keys appear among the failing conditions.',
        ],
        notes=[
            'Common ways to fix failing conditions are to identify the specific metric that breached its threshold, address the underlying issues or test/coverage gaps, rerun analysis, and confirm the new result in SonarCloud.',
            'If the conditions list is empty or unavailable, common causes are a missing quality gate result, insufficient API access, or a project that has not produced a recent analysis snapshot.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.quality_gate_conditions',
        dashboard_page='sonarcloud',
        title='SonarCloud Quality Gate Conditions',
        content=content,
        tags=[
            'quality gate conditions',
            'failing conditions',
            'sonar conditions',
        ],
        aliases=[
            'conditions list',
            'quality gate condition list',
            'sonarcloud failing conditions',
        ],
        time_window='current live SonarCloud project gate snapshot',
        aggregation='live list of quality gate conditions returned by SonarCloud for the configured project',
        source_endpoints=['/api/sonarcloud'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
        ],
    )


def _build_sonarcloud_common_issues_document(context):
    content = _render_content(
        intro='This document gives generic ways to solve common SonarCloud and SonarQube issues, even when those problems are not currently shown on the dashboard.',
        represents=[
            'It is a troubleshooting and remediation guide for common scan, configuration, and quality-finding problems related to SonarCloud or SonarQube-style analysis.',
        ],
        calculation=[
            'Configuration and connectivity checks: verify the project key, organization or project binding, token validity, token scope, and the base URL used by the scanner or API client.',
            'Analysis-not-showing checks: confirm the scanner actually ran in CI, inspect the scanner log for authentication or project-binding errors, and verify that the branch or PR analysis was uploaded successfully.',
            'Quality-gate failure fixes: open the failing conditions first, then address the specific metric behind the failure such as bugs, vulnerabilities, duplication, or coverage-related thresholds.',
            'Coverage mismatch fixes: ensure the coverage report is generated before scanning, confirm the report path in scanner configuration, and verify the report format matches what Sonar expects.',
            'Issue-count surprises: check whether the analysis is on the intended branch, whether only unresolved issues are being counted, and whether the project key points to the correct repository or codebase slice.',
            'Duplication fixes: extract shared helpers, remove copy-paste blocks, and keep generated or vendored files out of analysis scope when appropriate.',
            'False-positive handling: review the rule context carefully, improve the code if the warning is valid, and otherwise mark the issue with the appropriate workflow in SonarCloud or adjust rule configuration deliberately rather than ignoring everything globally.',
            'Performance and noise reduction: exclude generated files, vendored assets, and build output from analysis, and keep rule profiles aligned with the language and framework actually used by the repository.',
        ],
        notes=[
            'These are generic best practices, not evidence that your current project has these issues right now.',
            'For this dashboard specifically, the first local checks are `SONARCLOUD_PROJECT_KEY`, `SONARCLOUD_TOKEN`, successful scanner execution in CI, and whether the latest SonarCloud analysis produced fresh project measures and quality gate status.',
        ],
    )
    return _document(
        context=context,
        document_key='sonarcloud.common_issue_resolution',
        dashboard_page='sonarcloud',
        title='SonarCloud and SonarQube Common Issue Resolution',
        content=content,
        tags=[
            'sonarcloud troubleshooting',
            'sonarqube troubleshooting',
            'common sonar issues',
            'quality gate fixes',
        ],
        aliases=[
            'common sonarcloud issues',
            'common sonarqube issues',
            'how to fix sonar issues',
            'sonar troubleshooting guide',
        ],
        time_window='generic guidance, not tied to one live snapshot',
        aggregation='best-practice troubleshooting and remediation guidance for common SonarCloud and SonarQube issue patterns',
        source_endpoints=['/api/sonarcloud', '/api/sonarcloud/issues'],
        source_files=[
            'services/sonarcloud_service.py',
            'collectors/sonarcloud_collector.py',
            'templates/sonarcloud.html',
            'static/js/sonarcloud.js',
            'sonarcloud/routes.py',
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
        _build_vm_cpu_document(context),
        _build_vm_ram_document(context),
        _build_vm_network_traffic_document(context),
        _build_vm_disk_space_document(context),
        _build_deployment_resource_counts_document(context),
        _build_deployment_pods_by_namespace_document(context),
        _build_deployment_replicasets_by_namespace_document(context),
        _build_deployment_pods_by_phase_document(context),
        _build_deployment_frequency_document(context),
        _build_deployment_latest_image_artifact_document(context),
        _build_deployment_namespace_cpu_document(context),
        _build_deployment_namespace_memory_document(context),
        _build_deployment_namespace_network_document(context),
        _build_deployment_namespace_disk_document(context),
        _build_github_page_overview_document(context),
        _build_github_repository_document(context),
        _build_github_latest_branch_commits_document(context),
        _build_github_open_pull_requests_document(context),
        _build_github_merged_pull_requests_document(context),
        _build_github_failed_pipeline_commit_document(context),
        _build_github_fix_commit_document(context),
        _build_github_time_to_fix_document(context),
        _build_github_most_changed_files_document(context),
        _build_github_code_churn_document(context),
        _build_finops_page_overview_document(context),
        _build_finops_total_cost_document(context),
        _build_finops_average_daily_cost_document(context),
        _build_finops_highest_day_document(context),
        _build_finops_vs_previous_month_document(context),
        _build_finops_daily_total_cost_chart_document(context),
        _build_ecoops_page_overview_document(context),
        _build_ecoops_aks_co2_hour_document(context),
        _build_ecoops_vm_co2_hour_document(context),
        _build_ecoops_combined_last_hour_document(context),
        _build_ecoops_latest_hour_chart_document(context),
        _build_sonarcloud_page_overview_document(context),
        _build_sonarcloud_quality_gate_document(context),
        _build_sonarcloud_bugs_document(context),
        _build_sonarcloud_vulnerabilities_document(context),
        _build_sonarcloud_code_smells_document(context),
        _build_sonarcloud_security_hotspots_document(context),
        _build_sonarcloud_duplication_document(context),
        _build_sonarcloud_ncloc_document(context),
        _build_sonarcloud_quality_gate_conditions_document(context),
        _build_sonarcloud_common_issues_document(context),
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
    document_summary = sanitize_dashboard_kpi_summary(document_row.summary)
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

    summary = sanitize_dashboard_kpi_summary(row.summary)
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
