from flask import Blueprint

user_management_bp = Blueprint('user_management', __name__)

from user_management import routes