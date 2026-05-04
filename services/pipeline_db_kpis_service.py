"""
Calculate pipeline KPIs from PostgreSQL database.
Uses historical data for stable metrics while keeping real-time API calls for running builds.
"""
from sqlalchemy import desc
from pipeline_storage_models import PipelineBuildDuration, PipelineStageDuration


def get_kpis_from_database():
    """
    Calculate KPIs from persistent database records.
    More stable than API-only approach, includes historical data.
    """
    # Get all builds from database
    all_builds = PipelineBuildDuration.query.order_by(
        desc(PipelineBuildDuration.build_number)
    ).all()

    if not all_builds:
        return {
            'connected': True,
            'source': 'database',
            'total_builds': 0,
            'successful': 0,
            'failed': 0,
            'aborted': 0,
            'pending': 0,
            'success_rate': 0,
            'avg_duration_ms': 0,
            'failure_rate_by_stage': {},
            'builds_by_branch': {},
        }

    # Basic build counts
    successful = sum(1 for b in all_builds if b.result == 'SUCCESS')
    failed = sum(1 for b in all_builds if b.result == 'FAILURE')
    aborted = sum(1 for b in all_builds if b.result == 'ABORTED')
    pending = sum(1 for b in all_builds if b.result is None)

    total_builds = len(all_builds)
    success_rate = round((successful / total_builds) * 100, 1) if total_builds > 0 else 0

    # Duration analytics
    finished_builds = [b for b in all_builds if b.result is not None]
    durations = [b.duration_ms for b in finished_builds if b.duration_ms > 0]
    avg_duration_ms = round(sum(durations) / len(durations)) if durations else 0

    # Stage failure analysis
    all_stages = PipelineStageDuration.query.all()
    stage_stats = {}
    for stage in all_stages:
        stage_name = stage.stage_name
        if stage_name not in stage_stats:
            stage_stats[stage_name] = {
                'total': 0,
                'failed': 0,
            }
        stage_stats[stage_name]['total'] += 1
        if stage.status == 'FAILED':
            stage_stats[stage_name]['failed'] += 1

    failure_rate_by_stage = {}
    for stage_name, stats in stage_stats.items():
        if stats['total'] > 0:
            failure_rate_by_stage[stage_name] = round(
                (stats['failed'] / stats['total']) * 100, 1
            )

    # Build statistics by branch
    builds_by_branch = {}
    for build in all_builds:
        branch = build.branch or 'unknown'
        if branch not in builds_by_branch:
            builds_by_branch[branch] = {
                'total': 0,
                'successful': 0,
                'failed': 0,
                'builds': []
            }

        builds_by_branch[branch]['total'] += 1
        builds_by_branch[branch]['builds'].append(build.build_number)
        if build.result == 'SUCCESS':
            builds_by_branch[branch]['successful'] += 1
        elif build.result == 'FAILURE':
            builds_by_branch[branch]['failed'] += 1

    # Calculate deployment frequency (Deploy to AKS + Wait for AKS Rollout both SUCCESS)
    successful_deployments = 0
    for build in finished_builds:
        stage_statuses = {s.stage_name: s.status for s in build.stages}
        deploy_ok = stage_statuses.get('Deploy to AKS') == 'SUCCESS'
        rollout_ok = stage_statuses.get('Wait for AKS Rollout') == 'SUCCESS'
        if deploy_ok and rollout_ok:
            successful_deployments += 1

    deployment_rate = round(
        (successful_deployments / len(finished_builds)) * 100, 1
    ) if finished_builds else 0

    return {
        'connected': True,
        'source': 'database',
        'last_build_number': all_builds[0].build_number if all_builds else None,
        'total_builds': total_builds,
        'successful': successful,
        'failed': failed,
        'aborted': aborted,
        'pending': pending,
        'success_rate': success_rate,
        'avg_duration_ms': avg_duration_ms,
        'avg_duration_seconds': round(avg_duration_ms / 1000),
        'failure_rate_by_stage': failure_rate_by_stage,
        'deployment_frequency': {
            'successful': successful_deployments,
            'total': len(finished_builds),
            'rate': deployment_rate,
        },
        'builds_by_branch': builds_by_branch,
    }


def get_recent_builds_from_database(limit=20):
    """Get recent builds and their stages from database."""
    builds = PipelineBuildDuration.query.order_by(
        desc(PipelineBuildDuration.build_number)
    ).limit(limit).all()

    result = []
    for build in builds:
        build_dict = {
            'number': build.build_number,
            'branch': build.branch or 'unknown',
            'result': build.result,
            'duration_ms': build.duration_ms,
            'duration_seconds': build.duration_seconds,
            'started_at': build.started_at.isoformat() if build.started_at else None,
            'stages': [
                {
                    'name': s.stage_name,
                    'status': s.status,
                    'duration_ms': s.duration_ms,
                    'start_time': int(s.started_at.timestamp() * 1000) if s.started_at else None,
                }
                for s in build.stages
            ]
        }
        result.append(build_dict)

    return result


def get_build_statistics_by_branch():
    """Get detailed statistics per branch."""
    builds = PipelineBuildDuration.query.all()

    branch_stats = {}
    for build in builds:
        branch = build.branch or 'unknown'
        if branch not in branch_stats:
            branch_stats[branch] = {
                'branch': branch,
                'total': 0,
                'successful': 0,
                'failed': 0,
                'aborted': 0,
                'avg_duration_ms': 0,
                'success_rate': 0,
            }

        branch_stats[branch]['total'] += 1
        if build.result == 'SUCCESS':
            branch_stats[branch]['successful'] += 1
        elif build.result == 'FAILURE':
            branch_stats[branch]['failed'] += 1
        elif build.result == 'ABORTED':
            branch_stats[branch]['aborted'] += 1

    # Calculate averages
    for branch, stats in branch_stats.items():
        branch_builds = [b for b in builds if (b.branch or 'unknown') == branch]
        durations = [b.duration_ms for b in branch_builds if b.duration_ms > 0]
        if durations:
            stats['avg_duration_ms'] = round(sum(durations) / len(durations))
        if stats['total'] > 0:
            stats['success_rate'] = round((stats['successful'] / stats['total']) * 100, 1)

    return list(branch_stats.values())
