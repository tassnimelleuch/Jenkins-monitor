import logging
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError
from azure.mgmt.containerservice import ContainerServiceClient
from flask import current_app

logger = logging.getLogger(__name__)


def _get_credential():
    return DefaultAzureCredential()


def _get_subscription_id():
    return current_app.config['AZURE_SUBSCRIPTION_ID']


def _get_resource_group():
    return current_app.config['AKS_RESOURCE_GROUP']


def _get_cluster_name():
    return current_app.config['AKS_CLUSTER_NAME']


def check_connection():
    try:
        credential = _get_credential()
        client = ContainerServiceClient(
            credential,
            _get_subscription_id()
        )

        cluster = client.managed_clusters.get(
            _get_resource_group(),
            _get_cluster_name()
        )

        provisioning_state = getattr(cluster, 'provisioning_state', None)
        power_state = getattr(cluster, 'power_state', None)
        power_code = getattr(power_state, 'code', None) if power_state else None

        connected = True
        if provisioning_state and str(provisioning_state).lower() != 'succeeded':
            connected = False
        if power_code and str(power_code).lower() != 'running':
            connected = False

        return {
            'connected': connected,
            'cluster_name': cluster.name,
            'location': cluster.location,
            'provisioning_state': provisioning_state,
            'power_state': power_code,
        }

    except AzureError as e:
        logger.warning(f'[Azure] Connection error: {e}')
        return {
            'connected': False,
            'message': str(e)
        }
    except Exception as e:
        logger.warning(f'[Azure] Unexpected connection error: {e}')
        return {
            'connected': False,
            'message': str(e)
        }
