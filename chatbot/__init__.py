from flask import Blueprint

chatbot_bp = Blueprint('chatbot', __name__)

from chatbot import routes
