import http.client
import logging
import time
import urllib3
import urllib3.exceptions


logger = logging.getLogger(__name__)


class Event:
    """Event is a single SSE event.

    This class handles parsing a message from a Reader.
    """

    __slots__ = ("event", "data", "id", "retry")

    def __init__(self):
        self.event: str | None = None
        self.data: str | None = None
        self.id: str | None = None
        self.retry: int | None = None

    @classmethod
    def from_message(cls, message, encoding):
        """Hydrate an event from message bytes."""
        event = cls()
        data = ""
        for byte_line in message.splitlines():
            line = byte_line.decode(encoding)
            field, sep, value = line.partition(":")
            if field == "" and sep == ":":
                # Comment, ignore
                continue
            value = value.lstrip()
            if field == "event" and sep:
                event.event = value.lstrip() or "message"
            if field == "id":
                event.id = value.lstrip() if sep else None
            if field == "data":
                if data:
                    data += "\n"
                data += value.lstrip()
                event.data = data
            if field == "retry":
                try:
                    event.retry = int(value.lstrip())
                except ValueError:
                    # Ignore malformed retry.
                    pass
            # Untyped events are always `message`.
            if not event.is_empty() and not event.event:
                event.event = "message"
        return event

    def is_empty(self):
        return not (bool(self.data or self.retry or self.event or self.id) or False)


class EventSource:
    """EventSource connects to a SSE EventSource (Server) and yields Events via a Reader.

    EventSource handles the connection, with retry and Last-Event-ID
    provided by the Reader.
    """

    def __init__(self, url, headers, encoding="utf-8", retry=5000):
        self.url = url
        self.headers = headers
        self.encoding = encoding
        self.retry = retry
        self.reader: Reader = None  # type: ignore

    def _connect_reader(self, last_event_id=None):
        pool = urllib3.PoolManager()
        if last_event_id:
            self.headers["Last-Event-ID"] = last_event_id
        response = pool.request(
            "GET", self.url, preload_content=False, headers=self.headers
        )
        self.reader = Reader(response, self.encoding, self.retry)

    def __iter__(self):
        logger.debug("connecting...")
        self._connect_reader(last_event_id=None)
        return self

    def __next__(self) -> Event:
        try:
            return next(self.reader)
        except (
            StopIteration,
            EOFError,
            http.client.IncompleteRead,
            http.client.HTTPException,
            urllib3.exceptions.ProtocolError,
        ) as e:
            logger.debug(e)
            time.sleep(self.reader.retry / 1000.0)
            logger.debug(
                "attempting to reconnect after %d milliseconds at last event %s"
                % (self.reader.retry, self.reader.last_event_id)
            )
            self._connect_reader(self.reader.last_event_id)
            return next(self)


class Reader:
    """Reader consumes any iterator that yields bytes, returning Event objects.

    Reader takes any iterable that yields bytes and yields Event
    objects. It handles correctly identifying event boundaries and
    maintains retry and Last-Event-ID.

    """

    def __init__(self, stream, encoding="utf-8", retry=5000):
        self._stream = stream
        self._encoding = encoding
        self._retry = retry
        self._last_event_id = None
        self._messages = self._read_messages()

    def _read_messages(self):
        buf = b""
        for chunk in self._stream:
            buf += chunk
            # Yield all events.
            pos = 0
            while pos != -1 and buf:
                # Search buf for the first empty line (b'\r\r', b'\n\n', b'\r\n\r\n')
                for delim in (b"\r\r", b"\n\n", b"\r\n\r\n"):
                    pos = buf.find(delim)
                    if pos != -1:
                        # Found, yield.
                        yield buf[0:pos]
                        offset = pos + len(delim)
                        buf = buf[offset:]
        if buf:
            yield buf

    def __next__(self) -> Event:
        while True:
            message = next(self._messages)
            event = Event.from_message(message, self._encoding)
            if event.is_empty():
                continue
            else:
                if event.id:
                    self._last_event_id = event.id
                if event.retry:
                    self._retry = event.retry
            return event

    def __iter__(self):
        return self

    @property
    def last_event_id(self):
        return self._last_event_id

    @property
    def retry(self) -> int:
        return self._retry
