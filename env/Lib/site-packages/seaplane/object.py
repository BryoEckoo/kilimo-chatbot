from typing import Any, Dict, List, Optional, TypedDict, cast

import requests

from seaplane.config import config
from seaplane.errors import HTTPError, SeaplaneError
from seaplane.gen.carrier import ApiException, Bucket, ObjectApi
from seaplane.sdk_internal_utils.http import headers
from seaplane.sdk_internal_utils.retry import retry
from seaplane.sdk_internal_utils.token_auth import get_pdk_client, method_with_token

SP_BUCKETS = ["seaplane-internal-flows"]


class ListObjectsMetadata(TypedDict):
    """
    Dictionary wrapping the metadata returned by the `list_objects()` endpoint.
    """

    name: str
    digest: str
    mod_time: int
    size: int


class ObjectStorageAPI:
    """
    Class for handling Object Storage API calls.
    """

    _allow_internal: bool
    """
    If set, allows the wrapper to manipulate Seaplane-internal buckets.

    Should not be set in customer code!
    """

    def __init__(self) -> None:
        self._allow_internal = False

    def get_object_api(self, access_token: str) -> ObjectApi:
        return ObjectApi(get_pdk_client(access_token))  # type: ignore

    @method_with_token
    def list_buckets(self, token: str) -> List[str]:
        """
        Returns a list of buckets
        """
        api = self.get_object_api(token)
        list = []
        resp = api.list_buckets()
        for name, _ in sorted(resp.items()):
            if self._allow_internal or name not in SP_BUCKETS:
                list.append(name)

        return list

    @method_with_token
    def get_bucket(self, token: str, bucket_name: str) -> Dict[str, Any]:
        """
        Get metadata about a bucket
        """
        api = self.get_object_api(token)
        resp = api.get_bucket(bucket_name=bucket_name)
        return {k: cast(str, v) for (k, v) in resp.to_dict().items()}  # type: ignore

    @method_with_token
    def create_bucket(
        self,
        token: str,
        bucket_name: str,
        body: Optional[Dict[str, Any]] = None,
        if_not_exists: Optional[bool] = False,
    ) -> None:
        """
        Create a new bucket

        Optional body argument can be used to configure the bucket with description, notify, etc.
        """

        if if_not_exists:
            bucket_list = self.list_buckets()
            if bucket_name in bucket_list:
                return

        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot create bucket with Seaplane-internal name `{bucket_name}`"
            )

        if not body:
            body = {}
        bucket_config = Bucket.from_dict(body)
        api = self.get_object_api(token)

        def op() -> None:
            return api.create_bucket(
                bucket_name=bucket_name,
                bucket=bucket_config,
            )

        def verify() -> Bucket:
            return api.get_bucket(
                bucket_name=bucket_name,
            )

        try:
            retry(op, verify)
        except Exception:
            raise SeaplaneError(f"Failed creating bucket: {bucket_name}")

    @method_with_token
    def delete_bucket(self, token: str, bucket_name: str) -> None:
        """
        Delete a bucket
        """
        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot delete bucket with Seaplane-internal name `{bucket_name}`"
            )
        api = self.get_object_api(token)

        def op() -> None:
            try:
                api.delete_bucket_with_http_info(bucket_name=bucket_name)
            except ApiException as e:
                if e.status == 404:
                    return None

        def verify() -> Any:
            try:
                api.get_bucket_with_http_info(bucket_name=bucket_name)
                return False
            except ApiException as e:
                return e.status == 404

        try:
            retry(op, verify)
        except Exception:
            raise SeaplaneError(f"Failed deleting bucket: {bucket_name}")

    def exists(self, bucket: str, object: str) -> bool:
        """
        Returns True if object exists in bucket
        """
        try:
            objs = self.list_objects(bucket, "")
            for obj in objs:
                if obj["name"] == object:
                    return True
            return False
        except HTTPError as e:
            if e.status == 404:
                return False
            raise e

    @method_with_token
    def list_objects(
        self, token: str, bucket_name: str, path_prefix: str = "/"
    ) -> List[ListObjectsMetadata]:
        """
        List objects in a bucket, (optional) matching a path prefix

        Returns a list of dicts with name, digest, mod_time, and bytes for each object
        """
        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot list objects in bucket with Seaplane-internal name `{bucket_name}`"
            )
        api = self.get_object_api(token)

        resp = api.list_objects(
            bucket_name=bucket_name,
            path=path_prefix,
        )

        table = [
            ListObjectsMetadata(
                name=x.name,
                digest=x.digest,
                mod_time=x.mod_time,
                size=x.size,
            )
            for x in resp
        ]

        return table

    @method_with_token
    def download(self, token: str, bucket_name: str, path: str) -> bytes:
        """
        Downloads an object

        Returns the object in bytes
        """
        url = f"{config.carrier_endpoint}/object/{bucket_name}/store"

        params: Dict[str, Any] = {}
        params["path"] = path
        resp = requests.get(
            url,
            params=params,
            headers=headers(token, "application/octet-stream"),
        )
        return resp.content

    def file_url(self, bucket_name: str, path: str) -> str:
        """
        Builds a URL usable to download the object stored at the given bucket & path.
        """
        return f"{config.carrier_endpoint}/object/{bucket_name}/store?path={path}"

    @method_with_token
    def upload(self, token: str, bucket_name: str, object_path: str, object: bytes) -> None:
        """
        Create a new object with name `object_path` from the given object data (bytes)
        """
        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot upload to bucket with Seaplane-internal name `{bucket_name}`"
            )
        api = self.get_object_api(token)

        # Timeouts are expected here. Wait, confirm failure, then retry.
        # A timeout doesn't necessarily mean the operation failed
        def op() -> None:
            return api.create_object(
                bucket_name=bucket_name,
                path=object_path,
                body=object,
            )

        def verify() -> bytearray:
            return api.get_object(
                bucket_name=bucket_name,
                path=object_path,
            )

        try:
            retry(op, verify)
        except Exception:
            raise SeaplaneError(f"Failed uploading file: {object_path}")

    def upload_file(self, bucket_name: str, object_path: str, file_path: str) -> None:
        """
        Upload a local file (`file_path`) to a new object (`object_path`)
        """
        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot upload to bucket with Seaplane-internal name `{bucket_name}`"
            )
        with open(file_path, "rb") as file:
            file_data = file.read()

        self.upload(bucket_name, object_path, file_data)

    @method_with_token
    def delete(self, token: str, bucket_name: str, path: str) -> None:
        """
        Delete an object
        """
        if not self._allow_internal and bucket_name in SP_BUCKETS:
            raise SeaplaneError(
                f"Cannot delete from bucket with Seaplane-internal name `{bucket_name}`"
            )
        api = self.get_object_api(token)

        def op() -> None:
            try:
                api.delete_object_with_http_info(
                    bucket_name=bucket_name,
                    path=path,
                )
            except ApiException as e:
                if e.status == 404:
                    return None

        def verify() -> Any:
            try:
                api.get_object_with_http_info(
                    bucket_name=bucket_name,
                    path=path,
                )
                return False
            except ApiException as e:
                return e.status == 404

        try:
            retry(op, verify)
        except Exception:
            raise SeaplaneError(f"Failed deleting object: {path}")


object_store = ObjectStorageAPI()
