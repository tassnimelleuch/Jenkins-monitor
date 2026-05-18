from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from flask import current_app
from pipeline_identity import (
    configured_pipeline_job_path,
    normalize_job_path,
    pipeline_name as resolve_pipeline_name,
)
from sqlalchemy.orm import selectinload

from extensions import db
from finops_models import FinOpsBuildDocument, FinOpsDailyCost
from pipeline_storage_models import PipelineBranch, PipelineMainBuild
from services.pipeline_storage_service import build_tests_duration_points


def _utcnow():
    return datetime.now(timezone.utc)


def _to_float(value) -> float:
    return round(float(value or 0.0), 4)

def _resolve_subscription_id(subscription_id=None):
    return str(subscription_id or current_app.config.get('AZURE_SUBSCRIPTION_ID') or '').strip()


def _resolve_pipeline_context(pipeline_job_path=None):
    resolved_job_path = normalize_job_path(pipeline_job_path) or configured_pipeline_job_path(
        current_app.config,
        default_branch='main',
    )
    primary_branch = (
        PipelineBranch.query
        .order_by(PipelineBranch.is_primary.desc(), PipelineBranch.name.asc())
        .first()
    )
    return {
        'job_path': resolved_job_path,
        'name': resolve_pipeline_name(resolved_job_path),
        'branch_name': primary_branch.name if primary_branch is not None else 'main',
    }


def _normalize_date_input(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _month_bounds(year: int, month: int):
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _day_bounds(target_date: date):
    start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _extract_build_date(build_row):
    if build_row.started_at is not None:
        started_at = build_row.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return started_at.date()

    if build_row.timestamp_ms:
        return datetime.fromtimestamp(
            int(build_row.timestamp_ms) / 1000,
            tz=timezone.utc,
        ).date()

    return None


def _build_duration_ms(build_row):
    duration_ms = int(build_row.duration_ms or 0)
    if duration_ms > 0:
        return duration_ms
    return int(build_row.duration_seconds or 0) * 1000


def _load_cost_date_candidates(subscription_id, start_date=None, end_date=None):
    query = FinOpsDailyCost.query.filter_by(subscription_id=subscription_id)
    if start_date is not None:
        query = query.filter(FinOpsDailyCost.usage_date >= start_date)
    if end_date is not None:
        query = query.filter(FinOpsDailyCost.usage_date <= end_date)
    return {row.usage_date for row in query.all() if row.usage_date is not None}


def _load_build_date_candidates(branch_name, start_date=None, end_date=None):
    if not branch_name:
        return set()

    query = PipelineMainBuild.query.filter_by(branch_name=branch_name)

    dates = set()
    for row in query.all():
        build_date = _extract_build_date(row)
        if build_date is None:
            continue
        if start_date is not None and build_date < start_date:
            continue
        if end_date is not None and build_date > end_date:
            continue
        dates.add(build_date)
    return dates


def _load_month_cost_rows(subscription_id, year, month):
    month_start, month_end = _month_bounds(year, month)
    return (
        FinOpsDailyCost.query
        .filter(
            FinOpsDailyCost.subscription_id == subscription_id,
            FinOpsDailyCost.usage_date >= month_start,
            FinOpsDailyCost.usage_date <= month_end,
        )
        .order_by(FinOpsDailyCost.usage_date.asc())
        .all()
    )


def _load_month_build_rows(branch_name, year, month):
    if not branch_name:
        return []

    month_start, month_end = _month_bounds(year, month)
    start_dt, _ = _day_bounds(month_start)
    _, next_day_dt = _day_bounds(month_end)

    return (
        PipelineMainBuild.query
        .options(
            selectinload(PipelineMainBuild.stages),
            selectinload(PipelineMainBuild.branch),
        )
        .filter(
            PipelineMainBuild.branch_name == branch_name,
            PipelineMainBuild.started_at >= start_dt,
            PipelineMainBuild.started_at < next_day_dt,
        )
        .order_by(PipelineMainBuild.started_at.asc(), PipelineMainBuild.build_number.asc())
        .all()
    )


def _group_builds_by_date(build_rows):
    grouped = defaultdict(list)
    for row in build_rows:
        build_date = _extract_build_date(row)
        if build_date is None:
            continue
        grouped[build_date].append(row)
    return grouped


def _currency_code(cost_row):
    return (cost_row.currency_code if cost_row is not None else None) or 'USD'


def _format_currency(value, currency_code):
    return f'{currency_code} {value:.2f}'


def _format_duration(duration_ms):
    total_seconds = max(int(duration_ms or 0) // 1000, 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f'{hours}h {minutes}m'
    if minutes > 0:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'


def _build_branch_breakdown(build_rows):
    branch_totals = defaultdict(lambda: {'build_count': 0, 'total_duration_ms': 0})
    for row in build_rows:
        branch_name = (
            row.branch_name
            or (row.branch.name if row.branch is not None else None)
            or 'unknown'
        )
        branch_totals[branch_name]['build_count'] += 1
        branch_totals[branch_name]['total_duration_ms'] += _build_duration_ms(row)

    items = [
        {
            'branch_name': branch_name,
            'build_count': values['build_count'],
            'total_duration_ms': values['total_duration_ms'],
        }
        for branch_name, values in branch_totals.items()
    ]
    items.sort(key=lambda item: (-item['build_count'], -item['total_duration_ms'], item['branch_name']))
    return items


def _build_stage_totals(build_rows):
    stage_totals = defaultdict(int)
    deploy_stage_total_ms = 0

    for row in build_rows:
        for stage in row.stages or []:
            stage_name = (stage.stage_name or '').strip()
            if not stage_name:
                continue
            duration_ms = int(stage.duration_ms or 0)
            stage_totals[stage_name] += duration_ms
            if 'deploy' in stage_name.lower():
                deploy_stage_total_ms += duration_ms

    top_stages = [
        {
            'stage_name': stage_name,
            'total_duration_ms': total_duration_ms,
        }
        for stage_name, total_duration_ms in stage_totals.items()
    ]
    top_stages.sort(key=lambda item: (-item['total_duration_ms'], item['stage_name']))

    return {
        'deploy_stage_total_ms': deploy_stage_total_ms,
        'top_stages': top_stages[:5],
    }


def _build_longest_builds(build_rows):
    items = []
    for row in sorted(build_rows, key=lambda item: (-_build_duration_ms(item), item.build_number or 0))[:5]:
        items.append({
            'build_number': row.build_number,
            'branch_name': (
                row.branch_name
                or (row.branch.name if row.branch is not None else None)
                or 'unknown'
            ),
            'result': row.result,
            'duration_ms': _build_duration_ms(row),
        })
    return items


def _build_daily_build_metrics(build_rows):
    finished_rows = [row for row in build_rows if row.result is not None]
    build_count = len(build_rows)
    success_count = sum(1 for row in finished_rows if row.result == 'SUCCESS')
    failure_count = sum(1 for row in finished_rows if row.result == 'FAILURE')
    aborted_count = sum(1 for row in finished_rows if row.result == 'ABORTED')
    running_count = sum(1 for row in build_rows if row.result is None)
    total_duration_ms = sum(_build_duration_ms(row) for row in build_rows)
    avg_duration_ms = int(total_duration_ms / build_count) if build_count > 0 else 0

    test_points = build_tests_duration_points(build_rows, include_empty=True)
    unit_tests_total_ms = sum(int(item.get('unit_tests_ms') or 0) for item in test_points)
    pylint_total_ms = sum(int(item.get('pylint_ms') or 0) for item in test_points)
    sonarcloud_total_ms = sum(int(item.get('sonarcloud_ms') or 0) for item in test_points)
    test_related_total_ms = sum(int(item.get('total_duration_ms') or 0) for item in test_points)
    stage_totals = _build_stage_totals(build_rows)

    return {
        'build_count': build_count,
        'success_count': success_count,
        'failure_count': failure_count,
        'aborted_count': aborted_count,
        'running_count': running_count,
        'total_duration_ms': total_duration_ms,
        'avg_duration_ms': avg_duration_ms,
        'unit_tests_total_ms': unit_tests_total_ms,
        'pylint_total_ms': pylint_total_ms,
        'sonarcloud_total_ms': sonarcloud_total_ms,
        'test_related_total_ms': test_related_total_ms,
        'deploy_stage_total_ms': stage_totals['deploy_stage_total_ms'],
        'top_stages': stage_totals['top_stages'],
        'branch_breakdown': _build_branch_breakdown(build_rows),
        'longest_builds': _build_longest_builds(build_rows),
    }


def _build_month_build_baseline(builds_by_date):
    active_day_count = len(builds_by_date)
    if active_day_count == 0:
        return {
            'active_day_count': 0,
            'avg_build_count_active_day': 0.0,
            'avg_total_duration_ms_active_day': 0,
        }

    build_counts = []
    duration_totals = []
    for rows in builds_by_date.values():
        build_counts.append(len(rows))
        duration_totals.append(sum(_build_duration_ms(row) for row in rows))

    return {
        'active_day_count': active_day_count,
        'avg_build_count_active_day': round(sum(build_counts) / active_day_count, 2),
        'avg_total_duration_ms_active_day': int(sum(duration_totals) / active_day_count),
    }


def _build_month_cost_baseline(cost_rows):
    if not cost_rows:
        return {
            'day_count': 0,
            'avg_total_cost': 0.0,
        }

    totals = [_to_float(row.total_cost) for row in cost_rows]

    return {
        'day_count': len(cost_rows),
        'avg_total_cost': round(sum(totals) / len(totals), 4),
    }


def _cost_rank(target_date, cost_rows):
    if not cost_rows:
        return {'rank': None, 'day_count': 0}

    sorted_rows = sorted(cost_rows, key=lambda row: (-_to_float(row.total_cost), row.usage_date))
    for index, row in enumerate(sorted_rows, start=1):
        if row.usage_date == target_date:
            return {'rank': index, 'day_count': len(sorted_rows)}
    return {'rank': None, 'day_count': len(sorted_rows)}


def _build_signals(cost_row, build_metrics, cost_baseline, build_baseline):
    total_cost = _to_float(cost_row.total_cost) if cost_row is not None else 0.0

    avg_total_cost = cost_baseline['avg_total_cost']
    build_count = build_metrics['build_count']
    total_duration_ms = build_metrics['total_duration_ms']
    avg_build_count = build_baseline['avg_build_count_active_day']
    avg_duration_total = build_baseline['avg_total_duration_ms_active_day']

    cost_spike = avg_total_cost > 0 and total_cost > avg_total_cost
    high_build_activity = (
        build_count > 0
        and avg_build_count > 0
        and build_count >= (avg_build_count * 1.5)
    )
    long_build_activity = (
        total_duration_ms > 0
        and avg_duration_total > 0
        and total_duration_ms >= (avg_duration_total * 1.5)
    )
    build_pressure = high_build_activity or long_build_activity

    failure_pressure = (
        (build_metrics['failure_count'] + build_metrics['aborted_count']) >= 2
        and build_count > 0
        and (build_metrics['failure_count'] + build_metrics['aborted_count']) / build_count >= 0.3
    )

    if build_pressure:
        likely_driver = 'jenkins_build_activity'
    elif cost_spike:
        likely_driver = 'other_or_mixed_azure_usage'
    else:
        likely_driver = 'mixed_or_unclear'

    return {
        'cost_spike': cost_spike,
        'high_build_activity': high_build_activity,
        'long_build_activity': long_build_activity,
        'build_pressure': build_pressure,
        'failure_pressure': failure_pressure,
        'likely_driver': likely_driver,
    }


def _build_tags(signals, build_metrics, cost_row):
    tags = ['daily_finops_analysis']

    if cost_row is not None:
        tags.append('has_finops_cost')
    if build_metrics['build_count'] > 0:
        tags.append('has_build_activity')
    if signals['cost_spike']:
        tags.append('cost_spike')
    if signals['high_build_activity']:
        tags.append('high_build_activity')
    if signals['long_build_activity']:
        tags.append('long_build_duration')
    if signals['failure_pressure']:
        tags.append('failure_pressure')

    tags.append(f'likely_driver:{signals["likely_driver"]}')
    return tags


def _build_interpretation_lines(signals, cost_row, build_metrics, cost_baseline):
    avg_total_cost = cost_baseline['avg_total_cost']
    total_cost = _to_float(cost_row.total_cost) if cost_row is not None else 0.0
    lines = []

    if cost_row is None:
        lines.append('No stored FinOps daily cost row exists for this date, so the analysis depends only on Jenkins activity.')
    elif avg_total_cost > 0 and signals['cost_spike']:
        delta_pct = ((total_cost - avg_total_cost) / avg_total_cost) * 100
        lines.append(f'Total cost was {delta_pct:.1f}% above the month average.')
    elif cost_row is not None and avg_total_cost > 0:
        lines.append('Total cost was not above the month average.')

    if build_metrics['build_count'] == 0:
        lines.append('No Jenkins builds were stored for this date.')
    elif signals['build_pressure']:
        lines.append('Build activity was heavier than the normal active-day pattern for this month.')
    else:
        lines.append('Build activity stayed near the normal active-day pattern for this month.')

    if signals['likely_driver'] == 'jenkins_build_activity':
        lines.append('The stored evidence suggests Jenkins build activity is the strongest likely contributor.')
    elif signals['likely_driver'] == 'other_or_mixed_azure_usage':
        lines.append('Build pressure was not elevated, so the extra cost likely came from Azure usage outside the Jenkins activity pattern.')
    else:
        lines.append('The day looks mixed, so this should be treated as a correlation-based hint rather than a definitive cause.')

    if signals['failure_pressure']:
        lines.append('Multiple failures or aborts may have increased repeated build time.')

    lines.append('This analysis uses stored FinOps and Jenkins data only, so it supports retrieval and reasoning but not direct Azure billing causation proof.')
    return lines


def _document_title(target_date, pipeline_name):
    return f'{pipeline_name} FinOps analysis for {target_date.isoformat()}'


def _render_document(target_date, pipeline_name, pipeline_job_path, cost_row, month_cost_rows, day_build_rows, month_build_rows):
    currency_code = _currency_code(cost_row)
    build_metrics = _build_daily_build_metrics(day_build_rows)
    build_baseline = _build_month_build_baseline(_group_builds_by_date(month_build_rows))
    cost_baseline = _build_month_cost_baseline(month_cost_rows)
    cost_rank = _cost_rank(target_date, month_cost_rows)
    signals = _build_signals(cost_row, build_metrics, cost_baseline, build_baseline)
    tags = _build_tags(signals, build_metrics, cost_row)

    total_cost = _to_float(cost_row.total_cost) if cost_row is not None else 0.0

    lines = [
        f'Document kind: daily_finops_analysis',
        f'Document date: {target_date.isoformat()}',
        f'Pipeline: {pipeline_name}',
    ]
    if pipeline_job_path:
        lines.append(f'Pipeline job path: {pipeline_job_path}')

    lines.extend([
        '',
        'Cost evidence:',
        f'- Total cost: {_format_currency(total_cost, currency_code)}',
        f'- Month average total cost: {_format_currency(cost_baseline["avg_total_cost"], currency_code)}',
    ])
    if cost_rank['rank'] is not None:
        lines.append(f'- Cost rank in month: {cost_rank["rank"]} of {cost_rank["day_count"]}')

    lines.extend([
        '',
        'Build evidence:',
        f'- Total builds: {build_metrics["build_count"]}',
        f'- Successful builds: {build_metrics["success_count"]}',
        f'- Failed builds: {build_metrics["failure_count"]}',
        f'- Aborted builds: {build_metrics["aborted_count"]}',
        f'- Running builds: {build_metrics["running_count"]}',
        f'- Total build duration: {_format_duration(build_metrics["total_duration_ms"])}',
        f'- Average build duration: {_format_duration(build_metrics["avg_duration_ms"])}',
        f'- Test-related stage time: {_format_duration(build_metrics["test_related_total_ms"])}',
        f'- Deploy stage time: {_format_duration(build_metrics["deploy_stage_total_ms"])}',
    ])

    if build_baseline['active_day_count'] > 0:
        lines.extend([
            f'- Average builds per active day this month: {build_baseline["avg_build_count_active_day"]}',
            f'- Average total build time per active day this month: {_format_duration(build_baseline["avg_total_duration_ms_active_day"])}',
        ])

    if build_metrics['branch_breakdown']:
        lines.extend(['', 'Branch evidence:'])
        for item in build_metrics['branch_breakdown'][:5]:
            lines.append(
                f'- {item["branch_name"]}: {item["build_count"]} builds, {_format_duration(item["total_duration_ms"])} total'
            )

    if build_metrics['longest_builds']:
        lines.extend(['', 'Longest builds:'])
        for item in build_metrics['longest_builds']:
            lines.append(
                f'- Build #{item["build_number"]} on {item["branch_name"]}: {_format_duration(item["duration_ms"])} ({item["result"] or "RUNNING"})'
            )

    if build_metrics['top_stages']:
        lines.extend(['', 'Top stages by total time:'])
        for item in build_metrics['top_stages']:
            lines.append(
                f'- {item["stage_name"]}: {_format_duration(item["total_duration_ms"])}'
            )

    lines.extend(['', 'Interpretation:'])
    for item in _build_interpretation_lines(signals, cost_row, build_metrics, cost_baseline):
        lines.append(f'- {item}')

    summary = {
        'usage_date': target_date.isoformat(),
        'pipeline_name': pipeline_name,
        'pipeline_job_path': pipeline_job_path,
        'cost': {
            'available': cost_row is not None,
            'currency_code': currency_code,
            'total_cost': total_cost,
            'month_average_total_cost': cost_baseline['avg_total_cost'],
            'month_rank': cost_rank['rank'],
            'month_rank_day_count': cost_rank['day_count'],
        },
        'builds': build_metrics,
        'month_build_baseline': build_baseline,
        'signals': signals,
        'tags': tags,
    }

    return {
        'title': _document_title(target_date, pipeline_name),
        'content': '\n'.join(lines),
        'summary': summary,
        'tags': tags,
        'currency_code': currency_code,
    }


def _build_document_query(subscription_id, pipeline_job_path):
    return FinOpsBuildDocument.query.filter_by(
        subscription_id=subscription_id,
        pipeline_job_path=pipeline_job_path,
    )


def get_finops_build_document(target_date, subscription_id=None, pipeline_job_path=None):
    target_date = _normalize_date_input(target_date)
    subscription_id = _resolve_subscription_id(subscription_id)
    pipeline = _resolve_pipeline_context(pipeline_job_path)
    resolved_job_path = pipeline['job_path']

    return (
        _build_document_query(subscription_id, resolved_job_path)
        .filter_by(usage_date=target_date)
        .one_or_none()
    )


def list_finops_build_documents(subscription_id=None, pipeline_job_path=None, limit=30):
    subscription_id = _resolve_subscription_id(subscription_id)
    pipeline = _resolve_pipeline_context(pipeline_job_path)
    resolved_job_path = pipeline['job_path']

    return (
        _build_document_query(subscription_id, resolved_job_path)
        .order_by(FinOpsBuildDocument.usage_date.desc())
        .limit(max(int(limit or 30), 1))
        .all()
    )


def sync_finops_build_documents(*, subscription_id=None, pipeline_job_path=None, target_date=None, start_date=None, end_date=None):
    target_date = _normalize_date_input(target_date)
    start_date = _normalize_date_input(start_date)
    end_date = _normalize_date_input(end_date)

    if target_date is not None:
        start_date = target_date
        end_date = target_date

    if start_date is not None and end_date is None:
        end_date = start_date
    if end_date is not None and start_date is None:
        start_date = end_date
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError('start_date must be before or equal to end_date.')

    subscription_id = _resolve_subscription_id(subscription_id)
    pipeline = _resolve_pipeline_context(pipeline_job_path)
    resolved_job_path = pipeline['job_path']
    pipeline_name = pipeline['name']
    branch_name = pipeline['branch_name']

    cost_dates = _load_cost_date_candidates(
        subscription_id,
        start_date=start_date,
        end_date=end_date,
    )
    build_dates = _load_build_date_candidates(
        branch_name,
        start_date=start_date,
        end_date=end_date,
    )
    raw_candidate_dates = sorted(cost_dates | build_dates)
    first_build_date = min(build_dates) if build_dates else None
    if first_build_date is not None:
        candidate_dates = [
            item
            for item in raw_candidate_dates
            if item >= first_build_date
        ]
    else:
        candidate_dates = []
    skipped_noise_dates = len(raw_candidate_dates) - len(candidate_dates)

    existing_query = _build_document_query(subscription_id, resolved_job_path)
    if start_date is not None:
        existing_query = existing_query.filter(FinOpsBuildDocument.usage_date >= start_date)
    if end_date is not None:
        existing_query = existing_query.filter(FinOpsBuildDocument.usage_date <= end_date)
    existing_rows = {
        row.usage_date: row
        for row in existing_query.all()
    }

    grouped_dates = defaultdict(list)
    for item in candidate_dates:
        grouped_dates[(item.year, item.month)].append(item)

    created = 0
    updated = 0
    deleted = 0
    processed_dates = []
    now = _utcnow()

    for (year, month), month_dates in sorted(grouped_dates.items()):
        month_cost_rows = _load_month_cost_rows(subscription_id, year, month)
        cost_map = {row.usage_date: row for row in month_cost_rows}
        month_build_rows = _load_month_build_rows(branch_name, year, month)
        builds_by_date = _group_builds_by_date(month_build_rows)

        for usage_date in month_dates:
            rendered = _render_document(
                usage_date,
                pipeline_name,
                resolved_job_path,
                cost_map.get(usage_date),
                month_cost_rows,
                builds_by_date.get(usage_date, []),
                month_build_rows,
            )

            row = existing_rows.get(usage_date)
            if row is None:
                row = FinOpsBuildDocument(
                    subscription_id=subscription_id,
                    usage_date=usage_date,
                    pipeline_job_path=resolved_job_path,
                )
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.pipeline_name = pipeline_name
            row.currency_code = rendered['currency_code']
            row.title = rendered['title']
            row.content = rendered['content']
            row.summary = rendered['summary']
            row.source_system = 'finops_builds_rag'
            row.last_generated_at = now
            processed_dates.append(usage_date.isoformat())

    keep_dates = set(candidate_dates)
    for usage_date, row in existing_rows.items():
        if usage_date in keep_dates:
            continue
        db.session.delete(row)
        deleted += 1

    db.session.commit()

    return {
        'subscription_id': subscription_id,
        'pipeline_job_path': resolved_job_path,
        'pipeline_name': pipeline_name,
        'first_build_date': first_build_date.isoformat() if first_build_date is not None else None,
        'generated': len(candidate_dates),
        'created': created,
        'updated': updated,
        'deleted': deleted,
        'skipped_noise_dates': skipped_noise_dates,
        'dates': processed_dates,
    }
