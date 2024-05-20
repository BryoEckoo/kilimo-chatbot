from seaplane.config import Configuration
from seaplane.config import config as global_config
from seaplane.sdk_internal_utils.token_auth import method_with_token


class SQLAPI:
    """
    Class for accessing the SQL API
    """

    def __init__(self, config: Configuration) -> None:
        self.config = config

    def _connection_string(self, database_name: str) -> str:
        return f"postgres://{database_name}:{self.config.get_access_token()}@{self.config.sql_database_endpoint}/{database_name}"  # noqa: E501

    @method_with_token
    def connection_string(self, token: str, database_name: str) -> str:
        """
        Get a Seaplane SQL connection string for a given database
        """
        return self._connection_string(database_name)


sql = SQLAPI(global_config)
