from providers.kubernetes import get_cluster_snapshot


def get_deployment_kpis():
    try:
        data = get_cluster_snapshot()
        return {
            "connected": True,
            "data": data
        }
    except Exception as e:
        return {
            "connected": False,
            "message": str(e)
        }
