from flask import redirect, render_template, request, session, url_for

from services.access_service import role_required
from services.user_account_service import (
    DATE_FORMATS,
    TIME_FORMATS,
    get_time_zone_choices,
    get_user_preferences,
    update_user_preferences,
)
from settings import settings_bp


TIME_FORMAT_LABELS = {
    '12h': '12-hour (AM/PM)',
    '24h': '24-hour',
}
DATE_FORMAT_LABELS = {
    'dd/mm/yyyy': 'DD/MM/YYYY',
    'mm/dd/yyyy': 'MM/DD/YYYY',
    'yyyy-mm-dd': 'YYYY-MM-DD',
}


def _preference_options():
    return {
        'time_formats': [(value, TIME_FORMAT_LABELS[value]) for value in TIME_FORMATS],
        'date_formats': [(value, DATE_FORMAT_LABELS[value]) for value in DATE_FORMATS],
        'timezones': get_time_zone_choices(),
    }


@settings_bp.route('/settings', methods=['GET', 'POST'])
@role_required('admin', 'developer', 'tester')
def settings_page():
    username = session.get('username')
    preferences = get_user_preferences(username)
    error = None

    if request.method == 'POST':
        form_preferences = {
            'time_format': request.form.get('time_format', ''),
            'date_format': request.form.get('date_format', ''),
            'timezone': request.form.get('timezone', ''),
            'show_seconds': request.form.get('show_seconds') == 'on',
        }
        try:
            update_user_preferences(
                username=username,
                time_format=form_preferences['time_format'],
                date_format=form_preferences['date_format'],
                timezone_value=form_preferences['timezone'],
                show_seconds=form_preferences['show_seconds'],
            )
            return redirect(url_for('settings.settings_page', saved='1'))
        except ValueError as exc:
            error = str(exc)
            preferences = form_preferences

    return render_template(
        'settings.html',
        username=session.get('username'),
        role=session.get('role'),
        preferences=preferences,
        saved=request.args.get('saved') == '1',
        error=error,
        options=_preference_options(),
    )
