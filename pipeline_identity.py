def normalize_branch_name(branch_name, default=None):
    normalized = (branch_name or '').strip().strip('/')
    return normalized or default


def configured_branch_name(config, default=None):
    if config is None:
        return default
    return normalize_branch_name(config.get('JENKINS_BRANCH'), default=default)


def normalize_job_path(job_path):
    raw_job = (job_path or '').strip().strip('/')
    if not raw_job:
        return ''

    normalized = raw_job.replace('/job/', '/')
    if normalized.startswith('job/'):
        normalized = normalized[4:]

    return '/'.join(part for part in normalized.split('/') if part)


def job_path_segments(job_path):
    normalized = normalize_job_path(job_path)
    if not normalized:
        return []
    return [segment for segment in normalized.split('/') if segment]


def pipeline_job_path(job_path, branch_name=None):
    parts = job_path_segments(job_path)
    branch = normalize_branch_name(branch_name)
    if branch and len(parts) > 1 and parts[-1] == branch:
        parts = parts[:-1]
    return '/'.join(parts)


def configured_pipeline_job_path(config, default_branch=None):
    if config is None:
        return ''
    return pipeline_job_path(
        config.get('JENKINS_JOB'),
        branch_name=configured_branch_name(config, default=default_branch),
    )


def pipeline_name(job_path, branch_name=None, default='Jenkins Pipeline'):
    resolved_job_path = pipeline_job_path(job_path, branch_name=branch_name)
    if resolved_job_path:
        return resolved_job_path.split('/')[-1]

    normalized = normalize_job_path(job_path)
    if normalized:
        return normalized.split('/')[-1]

    return default
