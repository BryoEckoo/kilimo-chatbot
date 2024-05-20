from importlib.metadata import version
import os
from typing import List, Optional

import seaplane.gen.carrier
import seaplane.config

from seaplane.logs import log
import seaplane.run_load_dotenv  # noqa: F401
from seaplane.sdk_internal_utils.token_api import TokenAPI

_SEAPLANE_IDENTIFY_API_ENDPOINT = "https://flightdeck.cplane.cloud/v1"
_SEAPLANE_GLOBAL_SQL_API_ENDPOINT = "https://sql.cplane.cloud/v1"
_SEAPLANE_SQL_DATABASE_ENDPOINT = "sql.cplane.cloud"
_SEAPLANE_CARRIER_API_ENDPOINT = "https://carrier.cplane.cloud/v1"
_SEAPLANE_SUBSTATION_EMBED_API_ENDPOINT = "https://embed.substation.cplane.cloud/v1/embed"
_SEAPLANE_VECTOR_DB_API_ENDPOINT = "https://vector-new.cplane.cloud"


def _default_runner_image() -> str:
    sdk_version = version("seaplane")
    return f"us-central1-docker.pkg.dev/artifacts-356722/sdk-apps/apps-executor:{sdk_version}"


class Configuration:
    """
    Seaplane SDK Configuration.

    Everytime the configuration is changed,
    It'll clear local configurations to the default Auth module.
    """

    def __init__(self) -> None:
        self.region: Optional[str] = None
        self.identify_endpoint = _SEAPLANE_IDENTIFY_API_ENDPOINT
        self.global_sql_endpoint = _SEAPLANE_GLOBAL_SQL_API_ENDPOINT
        self.sql_database_endpoint = _SEAPLANE_SQL_DATABASE_ENDPOINT
        self.carrier_endpoint = _SEAPLANE_CARRIER_API_ENDPOINT
        self.vector_db_endpoint = _SEAPLANE_VECTOR_DB_API_ENDPOINT
        self.substation_embed_endpoint = _SEAPLANE_SUBSTATION_EMBED_API_ENDPOINT
        self.debug_endpoint: Optional[str] = None
        self.debug_nats_endpoints: str | List[str] = []
        self.runner_image = os.environ.get("SEAPLANE_RUNNER_IMAGE", _default_runner_image())
        self.name_prefix = os.environ.get("SEAPLANE_NAME_PREFIX", "")

        api_key = os.environ.get("SEAPLANE_API_KEY")
        self._token_api = TokenAPI(url=f"{self.identify_endpoint}/token", api_key=api_key)

    def set_api_key(self, api_key: str) -> None:
        """Set the Seaplane API Key.

        The API Key is needed for the Seaplane Python SDK usage.

        Parameters
        ----------
        api_key : str
            Seaplane API Key.
        """
        self._token_api.set_api_key(api_key)

    def get_api_key(self) -> str:
        ret = self._token_api.api_key
        if ret is None:
            raise RuntimeError(
                "missing Seaplane API key. Set SEAPLANE_API_KEY in your environment"
            )

        return ret

    def log_level(self, level: int) -> None:
        """Change logging level.
        Seaplane uses Python logging module for internal logs.
        Python logging levels can be used directly with Seaplane Python SDK or
        use the already defined in seaplane.log module.
            $ from seaplane import sea, log
            $ sea.config.log_level(log.INFO)
        Parameters
        ----------
        level : int
            Logging Level from Python logging module,
            like DEBUG, INFO, WARNING, ERROR, CRITICAL
        """
        log.level(level)

        if level == log.DEBUG:
            log.debug("Seaplane debug activated")
            log.debug(f"Identify endpoint: {self.identify_endpoint}")
            log.debug(f"Carrier endpoint: {self.carrier_endpoint}")
            log.debug(f"Vector endpoint: {self.vector_db_endpoint}")

    def staging_mode(self) -> None:
        """
        This function is for internal use by Seaplane, only.
        In general, using this will cause your applications to fail
        to deploy and run.
        """
        self.global_sql_endpoint = "https://sql.staging.cplane.dev/v1"
        self.sql_database_endpoint = "sql.staging.cplane.dev"
        self.carrier_endpoint = "https://carrier.staging.cplane.dev/v1"
        self.identify_endpoint = "https://flightdeck.staging.cplane.dev/v1"
        self.vector_db_endpoint = "https://vector-new.staging.cplane.dev"
        self._token_api.set_url(url=f"{self.identify_endpoint}/token")

    def debug_mode(self) -> None:
        self.debug_endpoint = "localhost"
        self.debug_nats_endpoints = ["nats://nats1:4222", "nats://nats2:4222", "nats://nats3:4222"]
        self.vector_db_endpoint = "http://qdrant:6333"
        self.carrier_endpoint = "http://localhost:4195"
        self.runner_image = "apps-executor-dev"

    def set_region(self, region: str) -> None:
        self.region = region.lower()

    def get_platform_configuration(self) -> seaplane.gen.carrier.Configuration:
        configuration = seaplane.gen.carrier.Configuration()  # type: ignore
        configuration.host = self.carrier_endpoint
        return configuration

    def get_access_token(self) -> str | None:
        return self._token_api.access_token


config = Configuration()
