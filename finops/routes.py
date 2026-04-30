from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session

from config import Config
from extensions import cache
from collectors.azure_cost_collector import AzureCostProvider
from services.finops_service import FinOpsService
from services.finops_cache import get_cached_daily_cost_chart, get_cached_resource_group_costs
from services.parallel_executor import parallel_execute
from services.access_service import role_required

finops_bp = Blueprint("finops", __name__)


@finops_bp.route("/finops")
@role_required("admin", "dev", "qa")
def finops_dashboard():
    return render_template(
        "finops.html",
        username=session.get("username"),
        role=session.get("role"),
    )


def _get_year_month() -> tuple[int, int]:
    now = datetime.utcnow()
    year = request.args.get("year", default=now.year, type=int)
    month = request.args.get("month", default=now.month, type=int)
    if month < 1 or month > 12:
        raise ValueError("Invalid month. Expected 1-12.")
    if year < 2000 or year > 2100:
        raise ValueError("Invalid year.")
    return year, month


def _make_service():
    subscription_id = Config.AZURE_SUBSCRIPTION_ID
    if not subscription_id:
        return None, None
    provider = AzureCostProvider(subscription_id=subscription_id)
    return FinOpsService(provider), subscription_id


def _delete_finops_keys():
    """
    Deletes all finops-related keys from Redis.
    Works with any flask-caching Redis backend version.
    Returns list of deleted key names.
    """
    deleted = []

    # Get the raw redis client — try every known attribute name
    redis_client = None
    backend = getattr(cache, "cache", None)
    for attr in ("_write_client", "_client", "client", "_cache"):
        redis_client = getattr(backend, attr, None)
        if redis_client is not None:
            break

    if redis_client is None:
        raise RuntimeError(
            "Cannot access Redis client from flask-caching backend. "
            f"Available attrs: {[a for a in dir(backend) if not a.startswith('__')]}"
        )

    # Scan and delete all keys that contain "cost" (covers daily + rg keys)
    patterns = [
        "flask_cache_daily_cost_chart:*",
        "flask_cache_rg_costs:*",
    ]
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=200)
            for key in keys:
                redis_client.delete(key)
                deleted.append(key.decode() if isinstance(key, bytes) else key)
            if cursor == 0:
                break

    return deleted


@finops_bp.route("/api/finops/daily-cost")
def daily_cost():
    service, subscription_id = _make_service()
    if not service:
        return jsonify({"error": "Missing AZURE_SUBSCRIPTION_ID in environment."}), 400

    try:
        year, month = _get_year_month()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    mode = request.args.get("mode", default="actual").lower()
    only = request.args.get("only", default="all").lower()

    if mode not in ("actual", "forecast"):
        return jsonify({"error": "Invalid mode. Use actual or forecast."}), 400

    if only not in ("all", "aks", "vm", "subscription"):
        return jsonify({"error": "Invalid only filter. Use all, aks, vm, or subscription."}), 400

    try:
        payload = get_cached_daily_cost_chart(
            service, year=year, month=month, mode=mode, only=only
        )
        return jsonify(payload)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": f"Azure cost query failed ({type(exc).__name__}): {exc}"}), 502


@finops_bp.route("/api/finops/resource-groups")
def resource_group_costs():
    service, subscription_id = _make_service()
    if not service:
        return jsonify({"error": "Missing AZURE_SUBSCRIPTION_ID in environment."}), 400

    try:
        year, month = _get_year_month()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cost_type = request.args.get("cost_type", default="ActualCost")
    if cost_type not in ("ActualCost", "AmortizedCost"):
        return jsonify({"error": "Invalid cost_type. Use ActualCost or AmortizedCost."}), 400

    try:
        payload = get_cached_resource_group_costs(
            service, year=year, month=month, cost_type=cost_type
        )
        return jsonify(payload)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": f"Azure cost query failed ({type(exc).__name__}): {exc}"}), 502


@finops_bp.route("/api/finops/cache/refresh", methods=["POST"])
@role_required("admin")
def refresh_cache():
    """
    Clears all finops Redis cache keys and optionally prefetches fresh data.

    Body (JSON, all optional):
        year        int   defaults to current year
        month       int   defaults to current month
        prefetch    bool  if true, re-fetches from Azure after clearing (default: false)
    """
    now = datetime.utcnow()
    body = request.get_json(silent=True) or {}
    year = int(body.get("year", now.year))
    month = int(body.get("month", now.month))
    prefetch = bool(body.get("prefetch", False))

    try:
        deleted_keys = _delete_finops_keys()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    result = {
        "cleared": len(deleted_keys),
        "keys_deleted": deleted_keys,
        "prefetched": False,
        "prefetch_error": None,
    }

    if prefetch:
        try:
            service, _ = _make_service()
            if service:
                tasks = {
                    "daily_all": lambda: get_cached_daily_cost_chart(
                        service, year, month, "actual", "all"
                    ),
                    "daily_aks": lambda: get_cached_daily_cost_chart(
                        service, year, month, "actual", "aks"
                    ),
                    "daily_vm": lambda: get_cached_daily_cost_chart(
                        service, year, month, "actual", "vm"
                    ),
                    "rg_actual": lambda: get_cached_resource_group_costs(
                        service, year, month, "ActualCost"
                    ),
                }
                parallel_execute(tasks, max_workers=3, timeout=120)
                result["prefetched"] = True
        except Exception as exc:
            result["prefetch_error"] = f"{type(exc).__name__}: {exc}"

    return jsonify(result), 200


@finops_bp.route("/api/finops/cache/keys")
@role_required("admin")
def list_cache_keys():
    """
    Lists all current finops keys in Redis with their TTL.
    Useful for debugging what is and isn't cached.
    """
    backend = getattr(cache, "cache", None)
    redis_client = None
    for attr in ("_write_client", "_client", "client", "_cache"):
        redis_client = getattr(backend, attr, None)
        if redis_client is not None:
            break

    if redis_client is None:
        return jsonify({"error": "Cannot access Redis client"}), 500

    keys_info = []
    for pattern in ("flask_cache_daily_cost_chart:*", "flask_cache_rg_costs:*"):
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=200)
            for key in keys:
                name = key.decode() if isinstance(key, bytes) else key
                ttl = redis_client.ttl(key)
                keys_info.append({"key": name, "ttl_seconds": ttl})
            if cursor == 0:
                break

    keys_info.sort(key=lambda x: x["key"])
    return jsonify({"count": len(keys_info), "keys": keys_info}), 200