import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)


def _get_prometheus_url():
    return current_app.config['PROMETHEUS_URL']


def query(promql: str) -> float | None:
    """Run an instant PromQL query, return the scalar value or None."""
    try:
        url = f"{_get_prometheus_url()}/api/v1/query"
        resp = requests.get(url, params={"query": promql}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
        return None
    except Exception as e:
        logger.warning("Prometheus query failed [%s]: %s", promql, e)
        return None


def query_range(promql: str, start: str, end: str, step: str = "60s") -> list:
    """Run a range query, return list of [timestamp, value] pairs."""
    try:
        url = f"{_get_prometheus_url()}/api/v1/query_range"
        resp = requests.get(url, params={
            "query": promql, "start": start, "end": end, "step": step
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return [[float(ts), float(v)] for ts, v in result[0]["values"]]
        return []
    except Exception as e:
        logger.warning("Prometheus range query failed [%s]: %s", promql, e)
        return []


def query_range_series(promql: str, start: str, end: str, step: str = "60s", label: str = "namespace") -> dict:
    """Run a range query and return a {label: [[ts, value], ...]} dict."""
    try:
        url = f"{_get_prometheus_url()}/api/v1/query_range"
        resp = requests.get(url, params={
            "query": promql, "start": start, "end": end, "step": step
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        series = {}
        for r in result:
            key = r.get("metric", {}).get(label, "unknown")
            values = r.get("values", [])
            series[key] = [[float(ts), float(v)] for ts, v in values]
        return series
    except Exception as e:
        logger.warning("Prometheus series range query failed [%s]: %s", promql, e)
        return {}
