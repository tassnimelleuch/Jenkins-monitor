import time
import requests
from azure.identity import DefaultAzureCredential


class AzureCostProvider:
    API_VERSION = "2025-03-01"
    ARM_SCOPE = "https://management.azure.com/.default"

    def __init__(self, subscription_id: str):
        self.subscription_id = subscription_id
        self.scope = f"/subscriptions/{subscription_id}"
        self.credential = DefaultAzureCredential()

    def _get_token(self) -> str:
        token = self.credential.get_token(self.ARM_SCOPE)
        return token.token

    def _post(self, path: str, payload: dict) -> dict:
        url = f"https://management.azure.com{path}?api-version={self.API_VERSION}"
        token = self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        last_exc = None
        for attempt in range(4):
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 429:
                retry_after = (
                    response.headers.get("x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after")
                    or response.headers.get("Retry-After")
                    or "30"
                )
                wait_s = int(retry_after) if str(retry_after).isdigit() else 2 ** attempt
                time.sleep(min(wait_s, 30))
                continue
            if response.status_code == 204:
                return {"properties": {"columns": [], "rows": []}}
            try:
                response.raise_for_status()
            except Exception as exc:
                last_exc = exc
                break
            return response.json()

        if last_exc:
            raise last_exc
        response.raise_for_status()
        return response.json()

    def query_usage(self, payload: dict) -> dict:
        path = f"{self.scope}/providers/Microsoft.CostManagement/query"
        return self._post(path, payload)

    def forecast_usage(self, payload: dict) -> dict:
        path = f"{self.scope}/providers/Microsoft.CostManagement/forecast"
        return self._post(path, payload)
