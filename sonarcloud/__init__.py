from flask import Blueprint

sonarcloud_bp = Blueprint('sonarcloud', __name__)

from sonarcloud import routes
