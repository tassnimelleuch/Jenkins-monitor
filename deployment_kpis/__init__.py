from flask import Blueprint

deployment_kpis_bp = Blueprint('deployment_kpis', __name__)

from deployment_kpis import routes
