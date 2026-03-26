from flask import Blueprint

pipeline_kpis_bp = Blueprint('pipeline_kpis', __name__)

from pipeline_kpis import routes