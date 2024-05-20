import inspect
import json
import os
import time
import types
from typing import Any, Dict, Iterable, Optional

from seaplane_framework.flow import processor

from seaplane.config import config
from seaplane.logs import log
from seaplane.object import object_store
from seaplane.pipes import ExecutionResult, Flow


class Result(ExecutionResult):
    """
    a Result is a package that allows work functions to return metadata along with
    their primary payload.

    Get one of these by calling Message.result(), like this:

    def with_fancy_result(message):
        ret = message.result("here's the body!")
        ret.address_tag("and-heres-the-address-tag")
        yield ret

    """

    def __init__(self, body: bytes, context: "Message"):
        self.body = body
        self.meta: Dict[str, str] = dict(context.meta)
        self.batch_hierarchy_override: Optional[str] = None
        self.description: Optional[Any] = None
        self.address_label: Optional[str] = None

    @property
    def output_id(self) -> Optional[str]:
        return self.meta.get("_seaplane_output_id")

    @output_id.setter
    def output_id(self, val: str) -> None:
        self.meta["_seaplane_output_id"] = val

    @output_id.deleter
    def output_id(self) -> None:
        del self.meta["_seaplane_output_id"]

    def drop(self) -> None:
        self.meta["_seaplane_drop"] = "True"

    def override_batch_hierarchy(self, batch_hierarchy: str) -> None:
        """
        Disables automatic batch numbering, instead using
        exactly the user-provided string.

        Cannot be safely undone for a single response.
        """
        self.batch_hierarchy_override = batch_hierarchy
        del self.meta["_seaplane_batch_hierarchy"]

    @property
    def batch_hierarchy(self) -> str:
        if self.batch_hierarchy_override:
            return self.batch_hierarchy_override
        else:
            return self.meta["_seaplane_batch_hierarchy"]


class Message:
    """
    TaskContext is what a Task receives when running on the Seaplane platform.

    It contains methods to read the metadata associated with inbound messages,
    and read common messages types.
    """

    def __init__(self, flow: Flow, body: bytes, meta: Dict[str, str]):
        self.flow = flow
        self.body = body
        self.meta = types.MappingProxyType(meta)
        self._object_data: Optional[bytes] = None

    @property
    def request_id(self) -> str:
        return self.meta["_seaplane_request_id"]

    @property
    def object_data(self) -> bytes:
        """
        Attempts to interpret the body of the context
        as an object store event, and to syncronously load
        the associated object and return it.

        May throw exceptions if the message isn't an object store
        notification, if the object store is unavailable, or if
        the object store would otherwise throw an exception (for example,
        if the notification was for a deleted object.)
        """
        if self._object_data is not None:
            return self._object_data

        try:
            msg = json.loads(self.body)
            bucket = msg["Bucket"]
            obj = msg["Object"]
        except (json.JSONDecodeError, KeyError):
            log.logger.error(
                "it doesn't look like this message was from the object store."
                " Make sure your tasks are configured to listen to object store messages"
                " when using context.object_data"
            )
            raise

        self._object_data = object_store.download(bucket, obj)

        return self._object_data

    def result(self, body: Any) -> Result:
        """
        returns a result that can be used to set metadata for outgoing messages,
        including address tags for conditional dispatch.

        "body" will be coerced into bytes, and `result` will throw a TypeError
        if this is impossible.
        """
        if isinstance(body, str):
            body = bytes(body, encoding="utf-8")

        body_bytes = bytes(body)
        return Result(body_bytes, self)


def execute(flow: Flow) -> None:
    """
    Execute the given function in an infinite loop, passing
    in new Seaplane messages and propegating results through the
    seaplane application. Never returns.

    Exceptions are logged but do not break the loop.
    """
    if int(os.getenv("DEBUG_MODE", 0)) == 1:
        config.debug_mode()
    processor.start()
    log.logger.info(f"{flow.instance_name} ready for processing")

    while True:
        try:
            message = processor.read()
            task_start_time = time.time()

            if isinstance(message, bytes):
                log.logger.error(
                    "message isn't properly packaged."
                    f" Got: {message.decode('utf-8', errors='backslashescape')}"
                )
                continue

            log.logger.debug(
                f"processing {message.body}:"
                f" ({message.meta.get('nats_subject')} /"
                f" {message.meta.get('_seaplane_address_tag')})"
            )

            if "_seaplane_output_id" not in message.meta:
                # This must be the first task in a smartpipe, so we have to get the
                # Endpoints API generated request ID from the incoming nats_subject.
                request_id = message.meta["nats_subject"].split(".")[
                    -1
                ]  # The Endpoints API always adds a request ID as the leaf

                # TODO what if this is an object update or some other weird business?
                # TODO (maybe object updates have something useful in their metadata?)
                message.meta["_seaplane_request_id"] = request_id
                message.meta["_seaplane_output_id"] = request_id

            if "_seaplane_batch_hierarchy" not in message.meta:
                message.meta["_seaplane_batch_hierarchy"] = ""

            # Zero out routing metadata from upstream.
            # Work functions can set this themselves.
            if "_seaplane_address_tag" in message.meta:
                del message.meta["_seaplane_address_tag"]

            task_context = Message(flow, message.body, message.meta.copy())

            result = flow.work(task_context)

            # This complex return protocol is an attempt at a welcoming initial UX
            #
            # First, we decide if the user is yielding a list, returning a single value,
            # or declining to return
            #   If the user returns nothing or yields nothing, treat it like a drop.
            #   If the user returns a generator, iterate over it.
            #   If the user returns a non-None, non-generator, wrap it in a list and
            #      iterate over the singleton
            #
            # Now, DWIM the values that are yielded from the user's iterator into results
            #   If the value is a Result, use it
            #     Otherwise, try and coerce that value into bytes, and use that as the
            #     body of a Result
            #   If we have an explicit batch hierarchy override, use it, otherwise
            #     If _seaplane_batch_hierarchy exists at all in the result, append to it
            #       Otherwise ignore it.

            if inspect.isgenerator(result):
                gen: Iterable[Any] = result
            else:
                gen = [result]

            batch_id = 1
            for output in gen:
                if output is None:
                    output = task_context.result(b"None")
                    output.meta["_seaplane_drop"] = "True"

                if not isinstance(output, Result):
                    try:
                        output = task_context.result(output)
                    except TypeError:
                        log.logger.error(f"can't encode result {repr(output)} as bytes")
                        continue

                use_meta = output.meta.copy()

                if output.batch_hierarchy_override:
                    use_meta["_seaplane_batch_hierarchy"] = output.batch_hierarchy_override
                elif "_seaplane_batch_hierarchy" in use_meta:
                    use_meta["_seaplane_batch_hierarchy"] += f".{batch_id}"
                    batch_id += 1

                if use_meta.get("_seaplane_drop"):
                    log.logger.info(
                        f'dropping output for "{use_meta["_seaplane_output_id"]}"'
                        " at user request"
                    )

                if "_seaplane_address_tag" in use_meta:
                    log.logger.debug(
                        "_seaplane_address_tag set directly by work function:"
                        f" {use_meta['_seaplane_address_tag']}"
                    )
                else:
                    addr = flow.calculate_address(output)
                    if addr is not None:
                        use_meta["_seaplane_address_tag"] = addr

                # This may be overriding the automatically provided `nats_subject` datum.
                if "nats_subject" in use_meta:
                    del use_meta["nats_subject"]

                if "_seaplane_drop" not in use_meta:
                    log.logger.debug(f"outputting {flow.subject.subject_string}")
                    log.logger.debug(json.dumps(use_meta, indent=2))

                task_end_time = time.time()
                wallclock = task_end_time - task_start_time
                log.logger.debug(f"{flow.instance_name} sending message after {wallclock} seconds")

                # Notice "task_start_time" doesn't change as we yield new messages.
                start_time_key = f"{flow.instance_name}_start_time"
                if start_time_key not in use_meta:
                    use_meta[start_time_key] = str(task_start_time)

                end_time_key = f"{flow.instance_name}_end_time"
                use_meta[end_time_key] = str(task_end_time)

                message_processing_time_key = f"{flow.instance_name}_message_processing_time"
                use_meta[message_processing_time_key] = str(wallclock)

                output_msg = processor._Msg(output.body, use_meta)
                processor.write(output_msg)

        except AssertionError:
            # Special case for ease in testing, and because we assume customer
            # code that fails assertions expects to crash.
            raise
        except Exception as e:
            log.logger.error(f"Error running Task {flow.instance_name}, sleeping", exc_info=e)

            # We should revisit this exception handling, but for now
            # we just try not to spam whatever went wrong.
            time.sleep(2)
        finally:
            log.logger.debug("flushing outputs")
            processor.flush()
