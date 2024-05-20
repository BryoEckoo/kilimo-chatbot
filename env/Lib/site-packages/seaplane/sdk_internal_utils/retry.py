import time
from typing import Any, Callable

from seaplane.gen.carrier import ApiException
from seaplane.logs import log


def retry(
    op: Callable[[], Any], verify: Callable[[], Any], max_retries: int = 3, delay: int = 10
) -> Any:
    """
    Performs a given operation, and retries it up to `max_retries` times if it fails.
    This function also takes an optional `verify` function, which is called when the
    operation times out, to check for the case where the operation actually succeeded.
    Returns the result of whichever operation finally succeeds (op or verify).
    """
    last_err = None

    for _ in range(1, max_retries + 1):
        try:
            return op()
        except ApiException as e:
            last_err = e

            # Any error except timeout
            if e.status != 500:
                raise

            log.logger.debug("retrying...")
            # Generous backoff, then attempt to verify the operation
            # in case it just took a long time but succeeded
            time.sleep(delay)

            if not verify:  # type: ignore
                continue

            try:
                return verify()
            except Exception:
                # Failure to verify, so retry the whole operation
                pass

    raise ApiException from last_err
