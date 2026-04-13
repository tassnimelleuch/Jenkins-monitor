from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from config import Config
from providers.azure_cost_provider import AzureCostProvider
from services.finops_service import FinOpsService

finops_bp = Blueprint("finops", __name__)


@finops_bp.route("/finops")
def finops_dashboard():
    return render_template("finops.html")


def _get_year_month() -> tuple[int, int]:
    now = datetime.utcnow()
    year = request.args.get("year", default=now.year, type=int)
    month = request.args.get("month", default=now.month, type=int)
    if month < 1 or month > 12:
        raise ValueError("Invalid month. Expected 1-12.")
    if year < 2000 or year > 2100:
        raise ValueError("Invalid year.")
    return year, month


@finops_bp.route("/api/finops/daily-cost")
def daily_cost():
    subscription_id = Config.AZURE_SUBSCRIPTION_ID
    if not subscription_id:
        return jsonify({"error": "Missing AZURE_SUBSCRIPTION_ID in environment."}), 400

    try:
        year, month = _get_year_month()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    mode = request.args.get("mode", default="actual").lower()
    only = request.args.get("only", default="all").lower()

    if mode not in ("actual", "forecast"):
        return jsonify({"error": "Invalid mode. Use actual or forecast."}), 400
    if only not in ("all", "aks", "vm"):
        return jsonify({"error": "Invalid only filter. Use all, aks, or vm."}), 400

    try:
        provider = AzureCostProvider(subscription_id=subscription_id)
        service = FinOpsService(provider)
        payload = service.get_daily_cost_chart(year=year, month=month, mode=mode, only=only)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": f"Azure cost query failed: {exc}"}), 502
