from __future__ import annotations

from flask import jsonify, render_template, session

from ecoops import ecoops_bp
from services.access_service import admin_required
from services.ecoops_service import get_ecoops_metrics


@ecoops_bp.route("/ecoops")
@admin_required
def ecoops_dashboard():
    return render_template(
        "ecoops.html",
        username=session.get("username"),
        role=session.get("role"),
    )


@ecoops_bp.route("/api/ecoops/live")
@admin_required
def ecoops_live():
    payload = get_ecoops_metrics()
    status_code = 200 if payload.get("connected") else 503
    return jsonify(payload), status_code
