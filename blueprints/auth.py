from flask import Blueprint, render_template, request, redirect, url_for, session
from app.models import users, find_user 

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


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
            return render_template('auth/register.html',
                                   error=error, username=username, role=role)

        users.append({
            'username': username,
            'password': password,
            'role':     role,
            'status':   'pending'
        })

        session['flash'] = 'Account requested! Waiting for admin approval.'
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', error=None, username='', role='')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # If already logged in, go straight to dashboard
    if session.get('username'):
        return redirect(url_for('jenkins.dashboard'))

    flash = session.pop('flash', None)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = find_user(username)

        if not user or user['password'] != password:
            return render_template('auth/login.html',
                                   error='Invalid username or password.', flash=None)

        if user['status'] == 'pending':
            return render_template('auth/login.html',
                                   error='Your account is awaiting admin approval.', flash=None)

        if user['status'] == 'rejected':
            return render_template('auth/login.html',
                                   error='Your registration was rejected.', flash=None)

        # Log in — store in session
        session['username'] = user['username']
        session['role']     = user['role']

        # Everyone goes to dashboard — admin and users alike
        # Admin sees dashboard + has Manage Users in sidebar
        return redirect(url_for('jenkins.dashboard'))

    return render_template('auth/login.html', error=None, flash=flash)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/admin/users')
def admin_users():
    if session.get('role') != 'admin':
        return redirect(url_for('auth.login'))

    return render_template('auth/admin_users.html',
                           pending  = [u for u in users if u['status'] == 'pending'],
                           approved = [u for u in users if u['status'] == 'approved' and u['role'] != 'admin'],
                           rejected = [u for u in users if u['status'] == 'rejected'])


@auth_bp.route('/admin/approve/<username>', methods=['POST'])
def approve_user(username):
    if session.get('role') != 'admin':
        return redirect(url_for('auth.login'))
    user = find_user(username)
    if user:
        user['status'] = 'approved'
    return redirect(url_for('auth.admin_users'))


@auth_bp.route('/admin/reject/<username>', methods=['POST'])
def reject_user(username):
    if session.get('role') != 'admin':
        return redirect(url_for('auth.login'))
    user = find_user(username)
    if user:
        user['status'] = 'rejected'
    return redirect(url_for('auth.admin_users'))