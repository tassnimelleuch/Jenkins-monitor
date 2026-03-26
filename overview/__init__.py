from flask import Blueprint

overview_bp = Blueprint('overview', __name__)

from overview import routes