from flask import redirect, url_for

from services.access_service import role_required
from settings import settings_bp


@settings_bp.route('/settings', methods=['GET', 'POST'])
@role_required('admin', 'developer', 'tester')
def settings_page():
    return redirect(url_for('overview.dashboard'))
