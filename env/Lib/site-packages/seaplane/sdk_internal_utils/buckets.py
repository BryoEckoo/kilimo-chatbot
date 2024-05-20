from typing import cast

from seaplane.gen.carrier import ApiException

from seaplane.errors import SeaplaneError
from seaplane.logs import log
from seaplane.object import object_store


def create_bucket_if_needed(app_id: str, bucket_name: str) -> str:
    """
    Returns the notification stream associated with a given bucket,
    after either creating the bucket with a default notification subject name,
    or reading the notification subject name from an existing bucket.

    Will throw an exception if the bucket cannot be created, or if the
    bucket already exists but is not configured to send notifications.
    """
    try:
        bkt = object_store.get_bucket(bucket_name)
        if "notify" not in bkt:
            raise SeaplaneError(
                f"bucket {bucket_name} already exists, but is not configured for notifications."
                " The seaplane project can create a new correctly configured bucket for you,"
                " but if you want to create one yourself, make sure it has an associated"
                " notify subject for your task to listen to."
            )
        ret = cast(str, bkt["notify"])
        log.debug(f"using existing bucket {bucket_name} with subject {ret}")
        return ret
    except ApiException as e:
        if e.status != 404:
            raise

    log.info(f"creating bucket {bucket_name} with default attributes")
    notify = f"{bucket_name}-evts.updates"
    bucket_def = {
        "description": f"Automatically created for application {app_id}",
        "replicas": 3,
        "max_bytes": -1,
        "notify": notify,
    }
    object_store.create_bucket(bucket_name, bucket_def)

    return notify
