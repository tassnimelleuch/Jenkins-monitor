from flask import jsonify, render_template, session, request
from github import github_bp
from services.access_service import role_required
from services.github_service import get_github_summary
from providers.github import create_tag
from flask import current_app
import logging

logger = logging.getLogger(__name__)


@github_bp.route('/github')
@role_required('admin', 'dev', 'qa')
def dashboard():
    return render_template(
        'github.html',
        username=session.get('username'),
        role=session.get('role')
    )


@github_bp.route('/api/github')
@role_required('admin', 'dev', 'qa')
def github_api():
    return jsonify(get_github_summary())


@github_bp.route('/api/github/tag', methods=['POST'])
@role_required('admin', 'dev', 'qa')
def create_commit_tag():
    """Create a tag on a commit in GitHub."""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    sha = data.get('sha')
    tag_name = data.get('tag_name')
    message = data.get('message', '')  # Optional message
    
    if not sha or not tag_name:
        return jsonify({'error': 'Missing required fields: sha and tag_name'}), 400
    
    # Get owner and repo from config
    owner = current_app.config.get('GITHUB_OWNER')
    repo = current_app.config.get('GITHUB_REPO')
    
    if not owner or not repo:
        logger.error('[GitHub] Tag creation failed: GITHUB_OWNER or GITHUB_REPO not configured')
        return jsonify({'error': 'GitHub repository not configured'}), 500
    
    token = current_app.config.get('GITHUB_TOKEN')
    if not token:
        logger.error('[GitHub] Tag creation failed: GITHUB_TOKEN not configured')
        return jsonify({'error': 'GitHub token not configured. Please configure GITHUB_TOKEN in settings.'}), 500
    
    logger.info(f'[GitHub] Attempting to create tag "{tag_name}" on {owner}/{repo} commit {sha[:7]}')
    
    # Create the tag
    result = create_tag(owner, repo, tag_name, sha, message if message else None)
    
    if 'error' in result:
        logger.error(f'[GitHub] Tag creation failed: {result.get("error")}')
        return jsonify(result), 400
    
    logger.info(f'[GitHub] Successfully created tag "{tag_name}"')
    return jsonify(result), 201
