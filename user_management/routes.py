from flask import render_template, redirect, url_for, session
from user_management import user_management_bp
from services.access_service import role_required
from services.user_account_service import get_user_groups, set_user_status


@user_management_bp.route('/admin/users')
@role_required('admin')
def manage_users():
    groups = get_user_groups()
    return render_template(
        '/manage_users.html',
        pending=groups['pending'],
        approved=groups['approved'],
        rejected=groups['rejected'],
        username=session.get('username'),
        role=session.get('role')
    )


@user_management_bp.route('/admin/approve/<username>', methods=['POST'])
@role_required('admin')
def approve_user(username):
    set_user_status(username, 'approved')
    return redirect(url_for('user_management.manage_users'))


@user_management_bp.route('/admin/reject/<username>', methods=['POST'])
@role_required('admin')
def reject_user(username):
    set_user_status(username, 'rejected')
    return redirect(url_for('user_management.manage_users'))
