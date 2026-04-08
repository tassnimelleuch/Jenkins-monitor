from flask import Blueprint

github_bp = Blueprint('github', __name__)

from github import routes
