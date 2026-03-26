
users = [
    {
        'username': 'admin',
        'password': 'admin',
        'role':     'admin',
        'status':   'approved'  # admin is always approved
    }
]

def find_user(username):
    for user in users: 
        if user['username'] == username:
            return user
    return None


def get_pending_count():
    """How many users are waiting for approval — used for the navbar badge."""
    return sum(1 for u in users if u['status'] == 'pending')

