from importlib.metadata import version
from typing import Dict, Optional

SDK_HTTP_ERROR_CODE = 0


def headers(api_key: Optional[str], content_type: str = "application/json") -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Authorization": f"Bearer {api_key}",
        "X-Seaplane-Sdk-Version": version("seaplane"),
        "User-Agent": f"SeaplaneSDK/{version('seaplane')}",
    }
