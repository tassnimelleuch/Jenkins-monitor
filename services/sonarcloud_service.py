"""
SonarCloud service layer.
Fetches metrics, quality gate status, and issue details from SonarCloud,
using parallel execution where possible to minimise latency.
"""

import logging
from urllib.parse import urlencode
from flask import current_app

from collectors.sonarcloud_collector import (
    get_measures,
    get_quality_gate_status,
    search_issues,
)
from services.parallel_executor import parallel_execute

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METRIC_KEYS = [
    "vulnerabilities",
    "code_smells",
    "duplicated_lines_density",
    "security_hotspots",
    "ncloc",
]

BUG_SEVERITY_MAP = {
    "low":    ["MINOR", "INFO"],
    "medium": ["MAJOR"],
    "high":   ["CRITICAL", "BLOCKER"],
}

SEVERITY_ORDER = {
    "BLOCKER":  0,
    "CRITICAL": 1,
    "MAJOR":    2,
    "MINOR":    3,
    "INFO":     4,
}

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _to_int(val):
    """Safely cast a value to int, returning None on failure."""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val):
    """Safely cast a value to a 2-decimal float, returning None on failure."""
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return None


def _measures_map(raw):
    """
    Convert raw SonarCloud measures response to a {metric: value} dict.
    Logs a warning when the response is malformed so failures are visible.
    """
    if not raw or "component" not in raw:
        logger.warning("Unexpected measures response shape: %s", raw)
        return {}
    measures = raw.get("component", {}).get("measures") or []
    return {m.get("metric"): m.get("value") for m in measures}


def _get_project_key():
    """Return the configured project key or None."""
    return current_app.config.get("SONARCLOUD_PROJECT_KEY")


def _get_ui_base_url():
    """Return the SonarCloud UI base URL derived from the configured API URL."""
    base_url = str(
        current_app.config.get("SONARCLOUD_BASE_URL", "https://sonarcloud.io/api")
        or "https://sonarcloud.io/api"
    ).rstrip("/")
    return base_url[:-4] if base_url.endswith("/api") else base_url


def _build_project_overview_url(project_key):
    """Return the SonarCloud project overview URL for the given project."""
    return f"{_get_ui_base_url()}/project/overview?{urlencode({'id': project_key})}"


def _build_project_issues_url(
    project_key,
    *,
    issue_type=None,
    severity=None,
    issue_key=None,
    branch=None,
    pull_request=None,
):
    """Return a SonarCloud issues page URL, optionally focused on a single issue."""
    params = {
        "id": project_key,
        "resolved": "false",
    }
    if issue_type:
        params["types"] = issue_type
    if severity:
        params["severities"] = severity
    if branch:
        params["branch"] = branch
    if pull_request:
        params["pullRequest"] = pull_request
    if issue_key:
        params["issues"] = issue_key
        params["open"] = issue_key
    return f"{_get_ui_base_url()}/project/issues?{urlencode(params)}"


def _not_configured_response(extra=None):
    """Standard response when SonarCloud is not configured."""
    payload = {
        "connected": False,
        "message": "SonarCloud is not configured. Set SONARCLOUD_PROJECT_KEY.",
    }
    if extra:
        payload.update(extra)
    return payload


def _run_in_app_context(app, func):
    """Run a callable within a Flask app context (for thread safety)."""
    with app.app_context():
        return func()


def _bug_count_for_severities(project_key, severities):
    """
    Return the total bug count across all given severity levels.
    Each severity requires a separate API call; we only request 1 result
    per call because only the paging metadata (total) is needed.
    """
    total = 0
    for severity in severities:
        try:
            data = search_issues(
                project_key=project_key,
                issue_type="BUG",
                severity=severity,
                page=1,
                page_size=1,
            )
            if data:
                total += data.get("paging", {}).get("total", 0)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to fetch bug count for severity '%s': %s", severity, exc
            )
    return total


def _format_issue(issue, *, project_key=None, default_issue_type=None):
    """Extract the fields we care about from a raw SonarCloud issue dict."""
    issue_key = issue.get("key")
    issue_type = issue.get("type") or default_issue_type
    severity = issue.get("severity")
    branch = issue.get("branch")
    pull_request = issue.get("pullRequest")
    return {
        "key":                 issue_key,
        "type":                issue_type,
        "rule":                issue.get("rule"),
        "severity":            severity,
        "message":             issue.get("message"),
        "component":           issue.get("component"),
        "line":                issue.get("line"),
        "status":              issue.get("status"),
        "author":              issue.get("author"),
        "branch":              branch,
        "pull_request":        pull_request,
        "creation_date":       issue.get("creationDate"),
        "update_date":         issue.get("updateDate"),
        "tags":                issue.get("tags", []),
        "clean_code_attribute": issue.get("cleanCodeAttribute"),
        "impacts":             issue.get("impacts", []),
        "url": (
            _build_project_issues_url(
                project_key,
                issue_type=issue_type,
                severity=severity,
                issue_key=issue_key,
                branch=branch,
                pull_request=pull_request,
            )
            if project_key and issue_key
            else None
        ),
    }



def get_sonarcloud_summary():
    """
    Return a high-level dashboard summary:
      - quality gate status and failing conditions
      - key metrics (vulnerabilities, code smells, etc.)
      - bug counts broken down by low / medium / high severity

    All independent API calls are executed in parallel.
    """
    project_key = _get_project_key()
    if not project_key:
        return _not_configured_response()

    app = current_app._get_current_object()

    # ------------------------------------------------------------------
    # Phase 1: fetch measures + quality gate in parallel
    # ------------------------------------------------------------------
    phase1_tasks = {
        "measures": lambda: _run_in_app_context(
            app, lambda: get_measures(METRIC_KEYS, project_key=project_key)
        ),
        "gate": lambda: _run_in_app_context(
            app, lambda: get_quality_gate_status(project_key=project_key)
        ),
    }
    phase1 = parallel_execute(phase1_tasks, max_workers=2, timeout=30)

    measures_raw = phase1.get("measures")
    gate_raw     = phase1.get("gate")

    # Only fail if BOTH core calls failed. Otherwise return partial data.
    if measures_raw is None and gate_raw is None:
        logger.error(
            "SonarCloud phase-1 fetch failed — measures=FAILED gate=FAILED"
        )
        return {
            "connected": False,
            "message":   "Unable to fetch SonarCloud data. Check your token and project key.",
        }

    if measures_raw is None or gate_raw is None:
        logger.warning(
            "SonarCloud partial data — measures=%s gate=%s",
            "OK" if measures_raw is not None else "FAILED",
            "OK" if gate_raw     is not None else "FAILED",
        )

    # ------------------------------------------------------------------
    # Phase 2: fetch bug counts for all severity levels in parallel
    # FIX: use default-argument capture (lvl=, sevs=) to avoid the
    # classic Python closure-over-loop-variable bug.
    # ------------------------------------------------------------------
    bug_tasks = {
        level: (
            lambda lvl=level, sevs=severities: _run_in_app_context(
                app, lambda: _bug_count_for_severities(project_key, sevs)
            )
        )
        for level, severities in BUG_SEVERITY_MAP.items()
    }
    bugs_by_severity = parallel_execute(bug_tasks, max_workers=3, timeout=30)

    # Replace any failed (None) counts with 0 so the dashboard still renders.
    bugs_by_severity = {
        level: (count if count is not None else 0)
        for level, count in bugs_by_severity.items()
    }

    # ------------------------------------------------------------------
    # Assemble response
    # ------------------------------------------------------------------
    metrics = _measures_map(measures_raw)

    gate       = gate_raw.get("projectStatus", {}) if gate_raw else {}
    conditions = gate.get("conditions") or []
    failing    = [c for c in conditions if c.get("status") == "ERROR"]

    return {
        "connected":   True,
        "project_key": project_key,
        "links": {
            "project": _build_project_overview_url(project_key),
            "issues": _build_project_issues_url(project_key),
            "issue_types": {
                issue_type: _build_project_issues_url(project_key, issue_type=issue_type)
                for issue_type in (
                    "BUG",
                    "VULNERABILITY",
                    "CODE_SMELL",
                    "SECURITY_HOTSPOT",
                )
            },
        },
        "quality_gate": {
            "status":     gate.get("status"),
            "failed":     len(failing),
            "conditions": [
                {
                    "metric":    c.get("metricKey"),
                    "status":    c.get("status"),
                    "value":     c.get("actualValue"),
                    "threshold": c.get("errorThreshold"),
                }
                for c in conditions
            ],
        },
        "metrics": {
            "bugs":                    bugs_by_severity,
            "vulnerabilities":         _to_int(metrics.get("vulnerabilities")),
            "code_smells":             _to_int(metrics.get("code_smells")),
            "duplicated_lines_density": _to_float(metrics.get("duplicated_lines_density")),
            "security_hotspots":       _to_int(metrics.get("security_hotspots")),
            "ncloc":                   _to_int(metrics.get("ncloc")),
        },
    }


def get_bug_details(level=None, page=1, page_size=20):
    """
    Return paginated bug details for a given severity level
    ('low', 'medium', or 'high').

    FIX: uses the accumulated API total (not len(collected)) so the
    paging metadata is accurate even when bugs exceed page_size=100.
    FIX: deduplicates issues by key to avoid double-counting.
    """
    project_key = _get_project_key()
    if not project_key:
        return _not_configured_response({"issues": []})

    severities = BUG_SEVERITY_MAP.get(level, [])
    if not severities:
        return {
            "connected": True,
            "issues":    [],
            "paging":    {"pageIndex": page, "pageSize": page_size, "total": 0},
        }

    seen_keys  = set()
    collected  = []
    api_total  = 0  # FIX: track the real API total, not just what we fetched

    for severity in severities:
        try:
            data = search_issues(
                project_key=project_key,
                issue_type="BUG",
                severity=severity,
                page=1,
                page_size=100,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch bugs for severity '%s': %s", severity, exc)
            continue

        if not data:
            continue

        api_total += data.get("paging", {}).get("total", 0)

        for issue in data.get("issues", []):
            key = issue.get("key")
            # FIX: deduplicate — a key we have already seen is skipped
            if key in seen_keys:
                continue
            seen_keys.add(key)
            collected.append(
                _format_issue(
                    issue,
                    project_key=project_key,
                    default_issue_type="BUG",
                )
            )

    # Sort by severity (most critical first)
    collected.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 999))

    # Apply manual pagination over the collected slice
    start = (page - 1) * page_size
    end   = start + page_size

    return {
        "connected": True,
        "level":     level,
        "paging": {
            "pageIndex": page,
            "pageSize":  page_size,
            # FIX: report the real API total, not len(collected)
            "total":     api_total,
        },
        "issues": collected[start:end],
    }


def get_issue_details(issue_type=None, page=1, page_size=20, severity=None):
    """
    Return paginated issues filtered by type and/or severity.
    Sorting is applied client-side on top of whatever SonarCloud returns.
    """
    project_key = _get_project_key()
    if not project_key:
        return _not_configured_response({"issues": []})

    try:
        data = search_issues(
            project_key=project_key,
            issue_type=issue_type,
            severity=severity,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch issue details: %s", exc)
        data = None

    if not data:
        return {
            "connected": False,
            "message":   "Unable to fetch SonarCloud issues.",
            "issues":    [],
        }

    issues = [
        _format_issue(
            issue,
            project_key=project_key,
            default_issue_type=issue_type,
        )
        for issue in data.get("issues", [])
    ]
    issues.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 999))

    return {
        "connected":  True,
        "issue_type": issue_type,
        "severity":   severity,
        "paging":     data.get("paging", {
            "pageIndex": page,
            "pageSize":  page_size,
            "total":     len(issues),
        }),
        "issues": issues,
    }
