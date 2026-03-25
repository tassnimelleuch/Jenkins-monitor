users = [    {
        'username': 'admin',
        'password': 'admin',
        'role':     'admin',
        'status':   'approved'  # admin is always approved
    }]
def find_user(username):
    for user in users : 
        if user['username'] == username:
            return user
    return None

def get_pending_count():
    return len([u for u in users if u['status'] == 'pending'])