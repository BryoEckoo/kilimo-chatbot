"""process module hooks up to input/output FIFOs.

The default FIFOs are input.fifo (read from) and output.fifo (write
to). These can be overridden with `SEAPLANE_INPUT_FIFO` and
`SEAPLANE_OUTPUT_FIFO` environment variables respectively.

Usage::

    from seaplane.carrier import processor

    # Reverse all messages and write out "unbatched" (batch of size 1).
    while True:
        msg = processor.read()  # Read an entire message.
        msg.body = msg.body[::-1]  # Reverse bytes
        processor.write(msg)  # Write to current batch
        processor.flush()  # Flush the batch

"""
from __future__ import annotations

from io import BufferedReader, BufferedWriter
import os
import logging
import threading
import time

import msgpack


MAGIC_NUMBER = b"\xFF\xC9"
CURRENT_VERSION = 1


class _Msg:
    """Msg is approximately a benthos message.

    Msg provides the equivalent properties
      `rawBytes []byte`
      `metadata map[string]any`
    from Benthos.
    """

    def __init__(self, body: bytes | None = None, meta: dict | None = None):
        self.body: bytes = body or b""
        self.meta: dict[str, str] = meta or {}

    @classmethod
    def decode(cls, data: bytes) -> _Msg | bytes:
        """decode data from a frame into a Msg object.

        For backwards compatibility (warning: flaky), the absence of a
        magic number will short circuit to returning a string.

        With the two byte magic number, the prefix:

        b'\\xFF\\xC9\\x00\\x01

        Indicates a Seaplane processor message, version 1. Data after
        these 4 bytes is the encoded message, according to the
        version.
        """
        version = 0
        offset = 0
        # Check for Seaplane magic number.
        if data[0:2] == MAGIC_NUMBER:
            version = int.from_bytes(data[2:4], byteorder="big")
            offset = 4
        else:
            # Short circuit and return a string.
            return data
        message_data = data[offset:]
        try:
            codec = _CODECS[version]
        except KeyError:
            raise MissingVersionException("Unknown codec version {}".format(version))
        return codec.decode(message_data)

    def encode(self, version: int = CURRENT_VERSION) -> bytes:
        """encode this Msg into raw data for a frame."""
        header = MAGIC_NUMBER + int.to_bytes(version, length=2, byteorder="big")
        try:
            codec = _CODECS[version]
        except KeyError:
            raise MissingVersionException("Unknown codec version {}".format(version))
        data = codec.encode(self)
        return header + data

    def new(self, data=None):
        return _Msg(data or self.body, self.meta)

    def __eq__(self, other: _Msg) -> bool:
        return self.body == other.body and self.meta == other.meta


class ProcessorIO:
    """ProcessorIO handles IO to a stdpipe with a framed transport.

    ProcessorIO provides three thread-safe methods, `read`, `write` and `flush`.

    `read` reads a single message from `input_fifo`.

    `write` appends a message to be written out, to a `messages` buffer.

    `flush` flushes the messages buffer to the `output_fifo`.
    """

    def __init__(self, input_fifo_path: str, output_fifo_path: str):
        self._input_fifo_path = input_fifo_path
        self._output_fifo_path = output_fifo_path
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._messages = []

    def read(self):
        """
        `read` takes a single message, the frame format is a 4 byte header
        (unsigned 32 bit int, in big-endian order) indicating the frame size,
        followed by the frame itself.

        Example: ```b'\\x00\\x00\\x00\\x05hello'```
        """
        with self._read_lock:
            # Read frame header.
            header = self._input_fifo.read(4)
            frame_size = int.from_bytes(header, "big", signed=False)
            data = self._input_fifo.read(frame_size)
            msg = _Msg.decode(data)
            return msg

    def write(self, msg: _Msg | bytes):
        """
        `write` appends a message to the message buffer.

        Once all the messages are written, they need to be `flush`ed.
        """
        # Allow passing in raw strings.
        # TODO(ian): Remove once this usage pattern is gone.
        if isinstance(msg, bytes):
            self._messages.append(msg)
        else:
            data = msg.encode()
            self._messages.append(data)

    def flush(self):
        """
        `flush` flushes the messages buffer to the output_fifo, using a format:
        - 4 byte header (unsigned 32 bit int) with the frame size
        - 4 byte header (unsigned 32 bit int) with the number of messages
        - all the messages in the frame format:
          - 4 byte header (unsigned 32 bit int) with the message size
          - message bytes

        Example:

        2 messages: `hello` and `world` are encoded as follows:

        ```
        frame_size = b'\\x00\\x00\\x00\\x16' # 22 bytes that follow
        number_of_messages = b'\\x00\\x00\\x00\\x02' # 2 messages
        message1 = b'\\x00\\x00\\x00\\x05hello'
        message2 = b'\\x00\\x00\\x00\\x05world'
        ```

        Which results in this message being flushed to fifo_output:

        ```
        b'\\x00\\x00\\x00\\x16\\x00\\x00\\x00\\x02\\x00\\x00\\x00\\x05hello\\x00\\x00\\x00\\x05world'
        ```

        """
        with self._write_lock:
            output = bytearray()
            num_msgs = len(self._messages).to_bytes(4, "big")
            output.extend(num_msgs)
            for msg in self._messages:
                msg_len = len(msg).to_bytes(4, "big")
                output.extend(msg_len + msg)
            frame_size = len(output).to_bytes(4, "big")
            self._output_fifo.write(frame_size + output)
            self._output_fifo.flush()
            self._messages.clear()

    def start(self):
        logging.info("opening input {}".format(self._input_fifo_path))
        logging.info("opening output {}".format(self._output_fifo_path))
        for _ in range(10):
            try:
                self._input_fifo: BufferedReader = open(self._input_fifo_path, "rb")
                self._output_fifo: BufferedWriter = open(self._output_fifo_path, "wb")
            except FileNotFoundError as exc:
                logging.error(exc)
                time.sleep(2)
            else:
                return
        raise FileNotFoundError("input/output")


class MissingVersionException(KeyError):
    """Indicates the codec version is unsupported/not found."""


class _Codec_1:
    """Internal codec for encode/decode version 1"""

    @staticmethod
    def encode(msg: _Msg) -> bytes:
        data = msgpack.packb(
            {
                "meta": msg.meta,
                "body": msg.body,
            }
        )
        return data

    @staticmethod
    def decode(data: bytes) -> _Msg:
        msg_obj = msgpack.unpackb(data)
        msg = _Msg(msg_obj["body"], msg_obj["meta"])
        return msg


_CODECS = {
    1: _Codec_1,
}

_default_processor_io = ProcessorIO(
    os.getenv("SEAPLANE_INPUT_FIFO", "input.fifo"),
    os.getenv("SEAPLANE_OUTPUT_FIFO", "output.fifo"),
)

# Provide a default start.
start = _default_processor_io.start

# Provide a default read.
read = _default_processor_io.read

# Provide a default write.
write = _default_processor_io.write

# Provide a default flush.
flush = _default_processor_io.flush
