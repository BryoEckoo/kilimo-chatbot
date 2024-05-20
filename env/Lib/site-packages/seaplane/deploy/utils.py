import hashlib
from importlib import resources
from importlib.metadata import version
import json
import nats
from nats.js.api import ConsumerConfig, KeyValueConfig, StreamConfig
from nats.js.errors import NotFoundError
import os
from python_on_whales import docker
from typing import Any, Dict, List, Optional
import zipfile

import requests
from seaplane.gen.carrier import ApiException, CarrierStreamOptions, FlowApi, StreamApi
from seaplane.gen.carrier.models.flow import Flow

from seaplane.config import config
from seaplane.errors import SeaplaneError
from seaplane.kv import KeyValueStorageAPI
from seaplane.logs import log
from seaplane.object import ObjectStorageAPI
from seaplane.sdk_internal_utils.http import headers
from seaplane.sdk_internal_utils.retry import retry
from seaplane.sdk_internal_utils.token_auth import get_pdk_client, with_token


def _file_md5(path: str) -> str:
    """
    Gets the MD5 hash of a file by path.
    """

    hasher = hashlib.md5()
    block_size = 4194304  # 4 MB
    with open(path, "rb") as fh:
        while True:
            buffer = fh.read(block_size)
            if not buffer:
                break
            hasher.update(buffer)
    return hasher.hexdigest()


@with_token
def create_stream(token: str, name: str) -> None:
    log.logger.debug(f"creating stream: {name}")
    stream_options: Dict[str, Any] = {}
    if config.region is not None:
        stream_options["allow_locations"] = [f"iata/{config.region}"]
    carrier_stream_options = CarrierStreamOptions.from_dict(stream_options)
    api = StreamApi(get_pdk_client(token))  # type: ignore

    def op() -> None:
        api.create_stream(
            stream_name=name,
            carrier_stream_options=carrier_stream_options,
        )

    def verify() -> Any:
        return api.get_stream(stream_name=name)

    try:
        retry(op, verify)
    except Exception:
        raise SeaplaneError(f"Failed creating stream: {name}")


@with_token
def delete_stream(token: str, name: str) -> None:
    log.logger.debug(f"deleting stream: {name}")
    api = StreamApi(get_pdk_client(token))  # type: ignore

    def op() -> None:
        try:
            api.delete_stream_with_http_info(stream_name=name)
        except ApiException as e:
            if e.status == 404:
                return None

    def verify() -> Any:
        try:
            api.get_stream_with_http_info(stream_name=name)
            return False
        except ApiException as e:
            return e.status == 404

    try:
        retry(op, verify)
    except Exception:
        raise SeaplaneError(f"Failed deleting stream: {name}")


@with_token
def add_secrets(token: str, name: str, secrets: Dict[str, str]) -> None:
    log.logger.debug(f"adding secrets: {name}")
    url = f"{config.carrier_endpoint}/flow/{name}/secrets"
    if config.region is not None:
        url += f"?region={config.region}"

    flow_secrets = {}
    for secret_key, secret_value in secrets.items():
        flow_secrets[secret_key] = {"destination": "all", "value": secret_value}

    resp = requests.put(
        url,
        json=flow_secrets,
        headers=headers(token),
    )
    resp.raise_for_status()


@with_token
def create_flow(token: str, name: str, workload: Dict[str, Any]) -> None:
    log.logger.debug(f"creating flow: {name}")
    api = FlowApi(get_pdk_client(token))  # type: ignore
    flow = Flow.from_dict(workload)

    def op() -> None:
        if config.region is not None:
            api.create_flow(
                flow_name=name,
                region=config.region,
                flow=flow,
            )
        else:
            api.create_flow(
                flow_name=name,
                flow=flow,
            )

    def verify() -> Any:
        if config.region is not None:
            return api.get_flow(flow_name=name, region=config.region)
        else:
            return api.get_flow(flow_name=name)

    try:
        retry(op, verify)
    except Exception:
        raise SeaplaneError(f"Failed creating flow: {name}")


@with_token
def delete_flow(token: str, name: str) -> None:
    log.debug(f"deleting flow: {name}")
    api = FlowApi(get_pdk_client(token))  # type: ignore

    def op() -> None:
        try:
            if config.region is not None:
                api.delete_flow_with_http_info(flow_name=name, region=config.region)
            else:
                api.delete_flow_with_http_info(flow_name=name)
        except ApiException as e:
            if e.status == 404:
                return None

    def verify() -> Any:
        try:
            if config.region is not None:
                api.get_flow_with_http_info(flow_name=name, region=config.region)
            else:
                api.get_flow_with_http_info(flow_name=name)
            return False
        except ApiException as e:
            return e.status == 404

    try:
        retry(op, verify)
    except Exception:
        raise SeaplaneError(f"Failed deleting flow: {name}")


# Note: These two functionts use KeyValueStorageAPI() for better exception handling.
def create_store(name: str, store_config: Optional[Dict[str, Any]] = None) -> None:
    """
    Creates a KV store.
    """
    log.logger.debug(f"creating store: {name}")
    if store_config is None:
        store_config = {}
    if config.region is not None:
        store_config["allow_locations"] = [f"iata/{config.region}"]
    kv = KeyValueStorageAPI()
    kv.create_store(name, body=store_config)


def delete_store(name: str) -> None:
    """
    Deletes a KV store.
    """
    log.logger.debug(f"deleting store: {name}")
    kv = KeyValueStorageAPI()
    try:
        kv.delete_store(name)
    except ApiException as e:
        if e.status != 404:
            raise


def has_store(store_name: str) -> bool:
    """
    Lists all stores in KV
    """
    kv = KeyValueStorageAPI()
    log.logger.debug("listing stores")
    store_names = kv.list_stores()
    return store_name in store_names


def set_key(store_name: str, key: str, value: str) -> None:
    """
    Set a key value in a given store
    """
    kv = KeyValueStorageAPI()
    log.logger.debug(
        f"setting key {key} in store {store_name} with value\n{json.dumps(value, indent=2)}"
    )
    kv.set_key(store_name, key, value.encode("utf-8"))


def zip_current_directory(directory_name: str) -> str:
    current_directory = os.getcwd()
    zip_filename = os.path.join(current_directory, "build", "project_for_deploy.zip")

    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write("pyproject.toml", os.path.relpath("pyproject.toml", current_directory))

        env_file = os.environ.get("SEAPLANE_ENV_FILE", ".env")
        if os.path.exists(env_file):
            zipf.write(env_file, os.path.relpath(".env", current_directory))

        source_root = os.path.join(current_directory, directory_name)
        log.logger.debug(f"zipping directory {source_root}")
        for root, _, files in os.walk(source_root):
            for file in files:
                if "__pycache__" in root:
                    continue

                file_path = os.path.join(root, file)
                log.logger.debug(f"- zipping {file_path}")
                zipf.write(file_path, os.path.relpath(file_path, current_directory))

    return zip_filename


def upload_project(project_directory_name: str) -> str:
    """
    Zips the project directory and pushes it into the Seaplane object store,
    returning a URL that our executor image can use to refer back to the
    project when executing.

    project_directory_name should be the basename (no slashes) of the project
    directory, a peer to the pyproject.toml file for this Seaplane application.
    """
    # Step 1: Make sure we have a bucket to dump our project into
    default_bucket_name: str = "seaplane-internal-flows"
    default_bucket_config = {
        "description": "Seaplane bucket used for flow images. Should not be modified directly.",
        "replicas": 3,
        "max_bytes": -1,  # unlimited
    }

    obj = ObjectStorageAPI()
    obj._allow_internal = True
    if default_bucket_name not in obj.list_buckets():
        obj.create_bucket(default_bucket_name, default_bucket_config)

    # Step 2: Build the zip file
    project_file = zip_current_directory(project_directory_name)
    remote_path = project_directory_name + "." + _file_md5(project_file) + ".zip"

    # Step 3: Upload & return
    obj.upload_file(default_bucket_name, remote_path, project_file)

    obj_url = obj.file_url(default_bucket_name, remote_path)
    log.info(f"uploaded project {project_directory_name} => {obj_url}")

    return obj_url


def build_debug_image(project_directory_name: str) -> str:
    image_tag = f"{project_directory_name}-executor"

    # Write a Dockerfile to build the project
    dockerfile = (
        resources.files(__package__).joinpath("resources").joinpath("Dockerfile").read_text()
    )
    dockerfile = dockerfile.replace("<VERSION>", version("seaplane"))
    dockerfile = dockerfile.replace("<PROJECT>", project_directory_name)
    with open("Dockerfile", "w") as file:
        file.write(dockerfile)

    # Write entrypoint.sh for the docker image
    entrypoint = (
        resources.files(__package__).joinpath("resources").joinpath("entrypoint.sh").read_text()
    )
    entrypoint = entrypoint.replace("<PROJECT>", project_directory_name)
    with open("entrypoint.sh", "w") as file:
        file.write(entrypoint)

    # Write setup.sh to pre-cache the SDK and dependencies
    setup = resources.files(__package__).joinpath("resources").joinpath("setup.sh").read_text()
    setup = setup.replace("<VERSION>", version("seaplane"))
    setup = setup.replace("<PROJECT>", project_directory_name)
    with open("setup.sh", "w") as file:
        file.write(setup)

    docker.build(context_path="./", tags=[image_tag])

    return image_tag


def debug_nats_endpoints() -> List[str]:
    return [
        f"nats://{config.debug_endpoint}:4222",
        f"nats://{config.debug_endpoint}:4223",
        f"nats://{config.debug_endpoint}:4224",
    ]


async def create_stream_debug(name: str) -> None:
    log.logger.debug(f"creating stream: {name}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()

    stream_config = StreamConfig(
        num_replicas=3, allow_direct=True, max_msgs=1000000, max_bytes=95 * 1024 * 1024
    )
    await js.add_stream(
        name=name,
        subjects=[f"{name}.>"],
        config=stream_config,
    )
    await nc.close()


async def delete_stream_debug(name: str) -> None:
    log.logger.debug(f"deleting stream: {name}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()
    try:
        await js.delete_stream(name)
    except NotFoundError:
        pass
    await nc.close()


async def create_consumer_debug(stream_config: Dict[str, Any]) -> None:
    log.logger.debug(f"creating consumer: {stream_config['durable']}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()

    ack_wait = int(stream_config["ack_wait"].replace("s", ""))

    consumer_config = ConsumerConfig(
        name=stream_config["durable"],
        durable_name=stream_config["durable"],
        ack_wait=ack_wait,
        max_ack_pending=stream_config["max_ack_pending"],
        deliver_policy=stream_config["deliver"],
        filter_subject=stream_config["subject"],
    )
    await js.add_consumer(stream_config["stream"], config=consumer_config)
    await nc.close()


async def create_store_debug(name: str, replicas: int = 3) -> None:
    log.logger.debug(f"creating store: {name}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()

    store_config = KeyValueConfig(
        bucket=name, history=16, max_bytes=2 * 1024 * 1024, replicas=replicas
    )
    await js.create_key_value(store_config)
    await nc.close()


async def delete_store_debug(name: str) -> None:
    log.logger.debug(f"deleting store: {name}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()

    try:
        await js.delete_key_value(bucket=name)
    except NotFoundError:
        pass
    await nc.close()


async def set_key_debug(store_name: str, key: str, value: str) -> None:
    log.logger.debug(f"setting key {key} in store {store_name}")
    nc = await nats.connect(debug_nats_endpoints())
    js = nc.jetstream()

    kv = await js.key_value(store_name)
    await kv.put(key, value.encode("utf-8"))
