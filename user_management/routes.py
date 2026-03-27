from flask import render_template, redirect, url_for, session
from user_management import user_management_bp
from models import users, find_user
from services.access_service import admin_required


@user_management_bp.route('/admin/users')
@admin_required
def manage_users():
    return render_template(
        '/manage_users.html',
        pending=[u for u in users if u['status'] == 'pending'],
        approved=[u for u in users if u['status'] == 'approved' and u['role'] != 'admin'],
        rejected=[u for u in users if u['status'] == 'rejected'],
        username=session.get('username'),
        role=session.get('role')
    )


@user_management_bp.route('/admin/approve/<username>', methods=['POST'])
@admin_required
def approve_user(username):
    user = find_user(username)
    if user:
        user['status'] = 'approved'
    return redirect(url_for('user_management.manage_users'))


@user_management_bp.route('/admin/reject/<username>', methods=['POST'])
@admin_required
def reject_user(username):
    user = find_user(username)
    if user:
        user['status'] = 'rejected'
    return redirect(url_for('user_management.manage_users'))