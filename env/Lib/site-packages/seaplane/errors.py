class SeaplaneError(Exception):
    """Seaplane Generic error"""

    pass


class HTTPError(SeaplaneError):
    """HTTP Errors exception

    Attributes
        ----------
        status : int
            HTTP Status code.
        message: str
            Error message.
    """

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"status={self.status}, message='{self.message}'"
