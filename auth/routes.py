from flask import render_template, request, redirect, url_for, session
from auth import auth_bp
from services.user_account_service import (
    authenticate_user,
    find_user,
    get_active_session_user,
    logout_user,
    normalize_role,
    register_user,
)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role     = request.form.get('role', '')

        error = None
        if not username or not password:
            error = 'All fields are required.'
        elif normalize_role(role) not in ('developer', 'tester'):
            error = 'Please select a role.'
        elif find_user(username):
            error = f'Username "{username}" is already taken.'

        if error:
            return render_template(
                'auth/register.html',
                error=error,
                username=username,
                role=role
            )

        try:
            register_user(username, password, role)
        except ValueError as exc:
            return render_template(
                'auth/register.html',
                error=str(exc),
                username=username,
                role=role
            )

        session['flash'] = 'Account requested! Waiting for admin approval.'
        return redirect(url_for('auth.login'))

    return render_template(
        'auth/register.html',
        error=None,
        username='',
        role=''
    )


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('username'):
        current_user = get_active_session_user(session.get('username'))
        if current_user:
            session['role'] = normalize_role(current_user.role)
            return redirect(url_for('overview.dashboard'))
        session.clear()

    flash = session.pop('flash', None)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user, error = authenticate_user(username, password)
        if error:
            return render_template(
                'auth/login.html',
                error=error,
                flash=None
            )

        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = normalize_role(user.role)

        return redirect(url_for('overview.dashboard'))

    return render_template(
        'auth/login.html',
        error=None,
        flash=flash
    )


@auth_bp.route('/logout')
def logout():
    logout_user(session.get('username'))
    session.clear()
    return redirect(url_for('auth.login'))
