from flask import Blueprint

alerts_bp = Blueprint('alerts', __name__)

from alerts import routes
