import logging
import os
import traceback
from typing import List

import seaplane.run_load_dotenv as denv


def _format_exception(e: Exception) -> List[str]:
    return traceback.format_exception(type(e), e, e.__traceback__)


class SeaLogger:
    """
    Seaplane Logger container module, which uses Python logger.
    """

    PREFIX = "[Seaplane] "
    FORMAT = "[Seaplane] %(message)s"

    CRITICAL = logging.CRITICAL
    FATAL = logging.FATAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    WARN = logging.WARN
    INFO = logging.INFO
    DEBUG = logging.DEBUG
    NOTSET = logging.NOTSET

    def __init__(self) -> None:
        logging.basicConfig(format=self.FORMAT)
        self.logger = logging.getLogger("Seaplane")

        level = os.environ.get("SEAPLANE_LOGLEVEL", "INFO")
        self.logger.setLevel(level=level)

        self.logger.debug(f"loaded environment {denv.root_env} ({denv.loaded})")

    def debug(self, message: str) -> None:
        self.logger.debug(message)

    def info(self, message: str) -> None:
        self.logger.info(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

    def level(self, level: int) -> None:
        """Change logging level.

        Seaplane uses Python logging module for internal logs.
        Python logging levels can be used directly with Seaplane Python SDK or
        use the already defined in seaplane.log module.

        Parameters
        ----------
        level : int
            Logging Level from Python logging module,
            like DEBUG, INFO, WARNING, ERROR, CRITICAL
        """
        self.logger.setLevel(level)

    def disable(self) -> None:
        """Disable the Seaplane logging for the SDK."""
        self.logger.setLevel(self.CRITICAL)

    def enable(self, level: int = WARNING) -> None:
        """Enable the Seaplane logging for the SDK,
        indicating the log level if needed.

        Parameters
        ----------
        level : int
            Logging Level from Python logging module,
            like DEBUG, INFO, WARNING, ERROR, CRITICAL
        """
        self.logger.setLevel(level)


log = SeaLogger()
