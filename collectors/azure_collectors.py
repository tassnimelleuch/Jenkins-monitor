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

        def _norm(val):
            if val is None:
                return None
            raw = getattr(val, 'value', val)
            return str(raw).strip().lower()

        connected = True
        prov_norm = _norm(provisioning_state)
        power_norm = _norm(power_code)

        if prov_norm and 'succeeded' not in prov_norm:
            connected = False
        if power_norm and 'running' not in power_norm:
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
