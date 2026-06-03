from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request, session

from config import Config
from extensions import cache
from collectors.azure_cost_collector import AzureCostProvider
from services.finops_service import FinOpsService
from services.finops_storage_service import (
    get_finops_daily_cost_chart,
    refresh_finops_month,
)
from services.finops_build_documents_service import (
    get_finops_build_document,
    get_finops_build_document_chunks,
    list_finops_build_documents,
    list_finops_build_document_chunks,
    sync_finops_build_documents,
)
from services.finops_chroma_service import (
    get_finops_chroma_status,
    sync_finops_documents_to_chroma,
)
from services.access_service import admin_required

finops_bp = Blueprint("finops", __name__)


@finops_bp.route("/finops")
@admin_required
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


def _parse_iso_date(value, field_name):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}. Expected YYYY-MM-DD.") from exc


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
@admin_required
def daily_cost():
    service, subscription_id = _make_service()
    if not service:
        return jsonify({"error": "Missing AZURE_SUBSCRIPTION_ID in environment."}), 400

    try:
        year, month = _get_year_month()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        payload = get_finops_daily_cost_chart(
            subscription_id,
            year=year,
            month=month,
            service=service,
            serve_stored_first=True,
        )
        return jsonify(payload)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": f"Azure cost query failed ({type(exc).__name__}): {exc}"}), 502


@finops_bp.route("/api/finops/cache/refresh", methods=["POST"])
@admin_required
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
            subscription_id = Config.AZURE_SUBSCRIPTION_ID
            if not subscription_id:
                raise RuntimeError("Missing AZURE_SUBSCRIPTION_ID in environment.")

            result["sync"] = refresh_finops_month(
                subscription_id,
                year,
                month,
                force=True,
            )
            result["prefetched"] = True
        except Exception as exc:
            result["prefetch_error"] = f"{type(exc).__name__}: {exc}"

    return jsonify(result), 200


@finops_bp.route("/api/finops/cache/keys")
@admin_required
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


@finops_bp.route("/api/finops/build-documents", methods=["GET"])
@admin_required
def finops_build_documents():
    document_date = request.args.get("date")
    limit = request.args.get("limit", default=30, type=int)

    try:
        if document_date:
            target_date = _parse_iso_date(document_date, "date")
            row = get_finops_build_document(target_date)
            if row is None:
                return jsonify({"error": "No stored FinOps analysis document was found for that date."}), 404

            return jsonify({
                "document": {
                    "id": row.id,
                    "usage_date": row.usage_date.isoformat(),
                    "pipeline_name": row.pipeline_name,
                    "pipeline_job_path": row.pipeline_job_path,
                    "currency_code": row.currency_code,
                    "title": row.title,
                    "content": row.content,
                    "tags": (row.summary or {}).get("tags", []),
                    "summary": row.summary or {},
                    "last_generated_at": row.last_generated_at.isoformat() if row.last_generated_at else None,
                }
            })

        rows = list_finops_build_documents(limit=limit)
        return jsonify({
            "count": len(rows),
            "documents": [
                {
                    "id": row.id,
                    "usage_date": row.usage_date.isoformat(),
                    "pipeline_name": row.pipeline_name,
                    "pipeline_job_path": row.pipeline_job_path,
                    "currency_code": row.currency_code,
                    "title": row.title,
                    "tags": (row.summary or {}).get("tags", []),
                    "summary": row.summary or {},
                    "last_generated_at": row.last_generated_at.isoformat() if row.last_generated_at else None,
                }
                for row in rows
            ],
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@finops_bp.route("/api/finops/build-documents/chunks", methods=["GET"])
@admin_required
def finops_build_document_chunks():
    document_date = request.args.get("date")
    limit = request.args.get("limit", default=120, type=int)

    try:
        if document_date:
            target_date = _parse_iso_date(document_date, "date")
            rows = get_finops_build_document_chunks(target_date)
            if not rows:
                return jsonify({"error": "No stored FinOps analysis chunks were found for that date."}), 404

            return jsonify({
                "count": len(rows),
                "chunks": [
                    {
                        "id": row.id,
                        "document_id": row.document_id,
                        "usage_date": row.usage_date.isoformat(),
                        "pipeline_name": row.pipeline_name,
                        "pipeline_job_path": row.pipeline_job_path,
                        "currency_code": row.currency_code,
                        "chunk_index": row.chunk_index,
                        "chunk_count": row.chunk_count,
                        "title": row.title,
                        "content": row.content,
                        "summary": row.summary or {},
                        "last_generated_at": row.last_generated_at.isoformat() if row.last_generated_at else None,
                    }
                    for row in rows
                ],
            })

        rows = list_finops_build_document_chunks(limit=limit)
        return jsonify({
            "count": len(rows),
            "chunks": [
                {
                    "id": row.id,
                    "document_id": row.document_id,
                    "usage_date": row.usage_date.isoformat(),
                    "pipeline_name": row.pipeline_name,
                    "pipeline_job_path": row.pipeline_job_path,
                    "currency_code": row.currency_code,
                    "chunk_index": row.chunk_index,
                    "chunk_count": row.chunk_count,
                    "title": row.title,
                    "summary": row.summary or {},
                    "last_generated_at": row.last_generated_at.isoformat() if row.last_generated_at else None,
                }
                for row in rows
            ],
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@finops_bp.route("/api/finops/build-documents/refresh", methods=["POST"])
@admin_required
def refresh_finops_build_documents():
    body = request.get_json(silent=True) or {}

    try:
        result = sync_finops_build_documents(
            target_date=_parse_iso_date(body.get("date"), "date"),
            start_date=_parse_iso_date(body.get("start_date"), "start_date"),
            end_date=_parse_iso_date(body.get("end_date"), "end_date"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"FinOps analysis document sync failed ({type(exc).__name__}): {exc}"}), 500

    return jsonify(result), 200


@finops_bp.route("/api/finops/build-documents/chroma/status", methods=["GET"])
@admin_required
def finops_build_documents_chroma_status():
    status = get_finops_chroma_status()
    status_code = 200 if status.get('chromadb_installed') else 503
    return jsonify(status), status_code


@finops_bp.route("/api/finops/build-documents/chroma/sync", methods=["POST"])
@admin_required
def sync_finops_build_documents_chroma():
    body = request.get_json(silent=True) or {}

    try:
        result = sync_finops_documents_to_chroma(
            target_date=_parse_iso_date(body.get("date"), "date"),
            start_date=_parse_iso_date(body.get("start_date"), "start_date"),
            end_date=_parse_iso_date(body.get("end_date"), "end_date"),
            rebuild=bool(body.get("rebuild", False)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": f"Chroma sync failed ({type(exc).__name__}): {exc}"}), 500

    return jsonify(result), 200
