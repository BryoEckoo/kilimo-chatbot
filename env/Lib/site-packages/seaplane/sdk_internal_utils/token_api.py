from typing import Dict, Optional, cast

import requests

from seaplane.errors import HTTPError
from seaplane.logs import log
from seaplane.sdk_internal_utils.http import SDK_HTTP_ERROR_CODE, headers


class TokenAPI:
    """
    Manage access token.

    Seaplane Python SDK manages the token by default,
    It can be managed manually as well.

    Any configuration change to the default `config` module,
    It'll reset `TokenAPI` local configurations, and renewed tokens.
    """

    def __init__(
        self,
        url: str,
        api_key: Optional[str],
        access_token: Optional[str] = None,
        auto_renew: bool = True,
        tenant: Optional[str] = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.access_token = access_token
        self.auto_renew = auto_renew
        self.tenant = tenant

    def __repr__(self) -> str:
        return f"TokenAPI(api_key={self.api_key}, access_token={self.access_token})"

    def set_url(self, url: str) -> None:
        if self.url == url:
            return
        self.url = url
        self.access_token = None
        self.tenant = None

    def set_api_key(self, key: str) -> None:
        if self.api_key == key:
            return
        self.api_key = key
        self.access_token = None
        self.tenant = None

    def renew_token(self) -> str:
        """Renew the token.

        Any configuration change to the default `config` module,
        It'll the renewed token, and It's needed to renew the token again.

        Returns
        -------
        str
            Returns the renewed token.
        """
        self.access_token = None

        payload = self._request_access_token()
        self.access_token = payload["token"]
        self.tenant = payload["tenant"]
        return self.access_token

    def get_token(self) -> str:
        """
        Returns the current access token, requesting a new one if needed.
        """
        if self.access_token is None:
            return self.renew_token()
        return self.access_token

    def get_tenant(self) -> str:
        """Request the current tenant

        Returns
        -------
        str
            Returns the tenant.
        """
        if not self.tenant:
            self.renew_token()
        # A `self.renew_token()` call will always set the tenant variable from
        # the newly-renewed token, so we can always return it here.
        return self.tenant or ""

    def _request_access_token(self) -> Dict[str, str]:
        try:
            log.info("Requesting access token...")
            if not self.api_key:
                log.error("API KEY not set, use seaplane.config.set_api_key")

            response = requests.post(self.url, json={}, headers=headers(self.api_key))

            if response.ok:
                payload = cast(Dict[str, str], response.json())
                return payload
            else:
                error_body = response.text
                log.error(
                    f"Bad Access token request code {response.status_code}, error {error_body}"
                )
                raise HTTPError(response.status_code, error_body)

        except requests.exceptions.RequestException as err:
            log.error(f"Request access token exception: {str(err)}")
            raise HTTPError(SDK_HTTP_ERROR_CODE, str(err))
