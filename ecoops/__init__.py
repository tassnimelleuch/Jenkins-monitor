from flask import Blueprint

ecoops_bp = Blueprint("ecoops", __name__)

from ecoops import routes
