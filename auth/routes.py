from flask import render_template, request, redirect, url_for, session
from auth import auth_bp
from models import users, find_user


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role     = request.form.get('role', '')

        error = None
        if not username or not password:
            error = 'All fields are required.'
        elif role not in ('developer', 'qa'):
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

        users.append({
            'username': username,
            'password': password,
            'role':     role,
            'status':   'pending'
        })

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
        return redirect(url_for('overview.dashboard'))

    flash = session.pop('flash', None)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = find_user(username)

        if not user or user['password'] != password:
            return render_template(
                'auth/login.html',
                error='Invalid username or password.',
                flash=None
            )

        if user['status'] == 'pending':
            return render_template(
                'auth/login.html',
                error='Your account is awaiting admin approval.',
                flash=None
            )

        if user['status'] == 'rejected':
            return render_template(
                'auth/login.html',
                error='Your registration was rejected.',
                flash=None
            )

        session['username'] = user['username']
        session['role']     = user['role']

        return redirect(url_for('overview.dashboard'))

    return render_template(
        'auth/login.html',
        error=None,
        flash=flash
    )


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))