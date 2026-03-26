def get_sidebar_items(role):
    items = [
        {'label': 'Overview', 'endpoint': 'overview.dashboard'},
        {'label': 'Pipeline KPIs', 'endpoint': 'pipeline_kpis.pipeline_kpis'},
    ]

    if role == 'admin':
        items.append({'label': 'Manage Users', 'endpoint': 'user_management.manage_users'})

    return items