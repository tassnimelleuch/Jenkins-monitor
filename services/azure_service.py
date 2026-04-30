from collectors import azure_collectors as azure


def get_connection_status():
    result = azure.check_connection()

    return {
        'connected': result.get('connected', False),
        'label': 'Connected' if result.get('connected') else 'Disconnected',
        'details': {
            'cluster_name': result.get('cluster_name'),
            'location': result.get('location'),
            'provisioning_state': result.get('provisioning_state'),
            'power_state': result.get('power_state'),
            'message': result.get('message'),
        }
    }
