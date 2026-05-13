from flask import jsonify, render_template, session, request
from github import github_bp
from services.access_service import role_required
from services.github_service import get_github_summary, is_tag_branch_allowed
from collectors.github_collector import create_tag, get_branches
from flask import current_app
import logging

logger = logging.getLogger(__name__)


@github_bp.route('/github')
@role_required('admin', 'developer')
def dashboard():
    return render_template(
        'github.html',
        username=session.get('username'),
        role=session.get('role')
    )


@github_bp.route('/api/github')
@role_required('admin', 'developer')
def github_api():
    return jsonify(get_github_summary())


@github_bp.route('/api/github/tag', methods=['POST'])
@role_required('admin', 'developer')
def create_commit_tag():
    """Create a tag on a commit in GitHub."""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    sha = data.get('sha')
    branch_name = (data.get('branch_name') or '').strip()
    tag_name = data.get('tag_name')
    message = data.get('message', '')  
    
    if not sha or not branch_name or not tag_name:
        return jsonify({'error': 'Missing required fields: sha, branch_name and tag_name'}), 400
    
    owner = current_app.config.get('GITHUB_OWNER')
    repo = current_app.config.get('GITHUB_REPO')
    
    if not owner or not repo:
        logger.error('[GitHub] Tag creation failed: GITHUB_OWNER or GITHUB_REPO not configured')
        return jsonify({'error': 'GitHub repository not configured'}), 500
    
    token = current_app.config.get('GITHUB_TOKEN')
    if not token:
        logger.error('[GitHub] Tag creation failed: GITHUB_TOKEN not configured')
        return jsonify({'error': 'GitHub token not configured. Please configure GITHUB_TOKEN in settings.'}), 500

    branches = get_branches(owner, repo, per_page=100) or []
    branch_head = next(
        (branch for branch in branches if branch.get('name') == branch_name),
        None,
    )
    if not branch_head or (branch_head.get('commit') or {}).get('sha') != sha:
        logger.warning(
            f'[GitHub] Tag creation blocked for {branch_name} because commit {sha[:7]} is not the branch head'
        )
        return jsonify({
            'error': 'Tags can only be created for the latest commit on the selected branch.'
        }), 400

    if not is_tag_branch_allowed(branch_name):
        logger.warning(f'[GitHub] Tag creation blocked for ineligible branch {branch_name}')
        return jsonify({
            'error': 'Tags can only be created from the main branch or branches starting with release/.'
        }), 400
    
    logger.info(
        f'[GitHub] Attempting to create tag "{tag_name}" on {owner}/{repo} branch {branch_name} commit {sha[:7]}'
    )
    
    result = create_tag(owner, repo, tag_name, sha, message if message else None)
    
    if 'error' in result:
        logger.error(f'[GitHub] Tag creation failed: {result.get("error")}')
        return jsonify(result), 400
    
    logger.info(f'[GitHub] Successfully created tag "{tag_name}"')
    return jsonify(result), 201
