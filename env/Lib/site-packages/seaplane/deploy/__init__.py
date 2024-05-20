import json
import os
import shutil
import threading
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

from importlib import resources
from python_on_whales import DockerClient
import yaml

from seaplane.config import config
from seaplane.logs import log
from seaplane.pipes import _DEFAULT_CPU, _DEFAULT_MEM, App, Flow
from seaplane.pipes.debug_schema import write_debug_schema

from .utils import (
    add_secrets,
    build_debug_image,
    create_consumer_debug,
    create_flow,
    create_store,
    create_store_debug,
    create_stream,
    create_stream_debug,
    delete_flow,
    delete_store,
    delete_store_debug,
    delete_stream,
    delete_stream_debug,
    has_store,
    set_key,
    set_key_debug,
    upload_project,
)

"""
Tools for taking a fully constructed Dag / Task complex and deploying it
into the Seaplane infrastructure.

Call `deploy(app, project_directory_name)` to push your application to Seaplane.
"""


SP_UTIL_STORES = ["_SP_REQUEST_", "_SP_RESPONSE_", "_SP_COLLECT_"]
SP_UTIL_STREAMS = [
    "_SEAPLANE_ENDPOINT",
    "_SEAPLANE_LOGGING",
    "_SEAPLANE_AI_RESULTS",
]
SP_APP_STORE = "_SP_APP_STORE"


class ProcessorInfo:
    """Information about the docker container to run applications"""

    def __init__(self, runner_image: str, runner_args: List[str]):
        self.runner_image = runner_image
        self.runner_args = runner_args


def flow_into_config(flow: Flow, processor_info: ProcessorInfo) -> Dict[str, Any]:
    """Produces JSON.dump-able flow configuration suitable for running the given task"""

    ret: Dict[str, Any] = {
        "processor": {
            "docker": {
                "image": processor_info.runner_image,
                "args": processor_info.runner_args,
            },
        },
        "output": {
            "switch": {
                "cases": [
                    {
                        "check": 'meta("_seaplane_drop") == "True"',
                        "output": {"drop": {}},
                    },
                    {
                        "check": 'meta("_seaplane_drop") != "True"',
                        "output": {
                            "carrier": {
                                "subject": flow.subject.subject_string,
                            }
                        },
                    },
                ]
            }
        },
        "replicas": flow.replicas,
    }

    if len(flow.subscriptions) == 0:
        log.logger.warning(
            f"task {flow.instance_name} does not appear to consume any input, it may not run"
        )

    broker: List[Dict[str, Any]] = []
    for ix, src in enumerate(sorted(flow.subscriptions, key=lambda s: s.filter)):
        # this durable scheme means we're committed to destroying
        # the consumers associated with these flows on redeploy.
        # Future fancy hot-swap / live-update schemes may need
        # to use different durable names

        # using the index of the broker arm is fine for the durable
        # name as carrier generates a durable from this value and
        # the flow name for all pull consumers (which this is)
        durable = str(ix)
        broker.append(
            {
                "carrier": {
                    "ack_wait": f"{flow.ack_wait_secs}s",
                    "bind": True,
                    "deliver": src.deliver,
                    "durable": durable,
                    "stream": src.stream(),
                    "subject": src.filter,
                },
            }
        )

    ret["input"] = {"broker": broker}

    requests: Dict[str, Any] = {}
    if flow.cpu != _DEFAULT_CPU:
        requests["cpu"] = flow.cpu
    if flow.mem != _DEFAULT_MEM:
        requests["memory"] = flow.mem
    if requests:
        ret["processor"]["resources"] = {"requests": requests}

    return ret


def flow_into_debug(flow: Flow, processor_info: ProcessorInfo) -> Dict[str, Any]:
    flow_volume = flow.instance_name
    flow_label = f"{flow.instance_name}".replace("-", "_")
    ret: Dict[str, Any] = {
        "service": {
            flow.instance_name: {
                "image": processor_info.runner_image,
                # "args": processor_info.runner_args,
                "volumes": [f"{flow_volume}:/data"],
                "environment": [
                    f"INSTANCE_NAME={flow.instance_name}",
                    "SEAPLANE_INPUT_FIFO=/data/input.fifo",
                    "SEAPLANE_OUTPUT_FIFO=/data/output.fifo",
                ],
            }
        },
        "volume": {flow_volume: {"external": True}},
        "carrier-io-service": {
            f"carrier-io-{flow.instance_name}": {
                "image": "drwsctt/carrier-io",
                # "image": "jeffail/benthos",
                "volumes": [
                    f"{flow_volume}:/data",
                    f"./flows/{flow.instance_name}:/local",
                ],
                "command": [
                    "-c",
                    "/local/streams_config.yaml",
                    "streams",
                    "--no-api",
                    "--prefix-stream-endpoints=false",
                    "/local/io.yaml",
                ],
            }
        },
        "io_config": {
            "pipeline": {
                "threads": 1,
                "processors": [
                    {
                        "label": "encode_message",
                        "mapping": """root.body = content()
root.meta = meta()
root = "\\xFF\\xC9\\x00\\x01" + root.format_msgpack()
meta = deleted()""",
                    },
                    {
                        "label": "flow_processor_io",
                        "subprocess": {
                            "name": "/stdpipe",
                            "args": [
                                "-input-pipe-path",
                                "/data/input.fifo",
                                "-output-pipe-path",
                                "/data/output.fifo",
                            ],
                            "max_buffer": 1048576,
                            "codec_send": "length_prefixed_uint32_be",
                            "codec_recv": "length_prefixed_uint32_be",
                        },
                    },
                    {
                        "label": "flow_processor_debatching",
                        "unarchive": {"format": "binary"},
                    },
                    {
                        "label": "decode_message",
                        "mapping": """root = content().slice(4).parse_msgpack()
meta = root.meta
root = root.body""",
                    },
                ],
            },
            "output": {
                "label": f"{flow_label}_flow_output",
                "switch": {
                    "cases": [
                        {
                            "check": 'meta("_seaplane_drop") == "True"',
                            "output": {"drop": {}},
                        },
                        {
                            "check": 'meta("_seaplane_drop") != "True"',
                            "output": {
                                "nats_jetstream": {
                                    "subject": flow.subject.subject_string,
                                    "urls": [
                                        "nats://nats1:4222,nats://nats2:4222,nats://nats3:4222,"
                                    ],
                                    "metadata": {"include_patterns": [".*"]},
                                },
                            },
                        },
                    ]
                },
            },
        },
    }

    broker_inputs: List[Dict[str, Any]] = []
    for _ix, src in enumerate(sorted(flow.subscriptions, key=lambda s: s.filter)):
        # durable = str(ix)
        broker_inputs.append(
            {
                "nats_jetstream": {
                    "urls": ["nats://nats1:4222,nats://nats2:4222,nats://nats3:4222,"],
                    "ack_wait": f"{flow.ack_wait_secs}s",
                    "max_ack_pending": 1024,
                    "bind": True,
                    # deliver new was troublesome, and we delete the streams anyway
                    # "deliver": src.deliver,
                    "deliver": "all",
                    # "durable": durable,
                    "durable": flow.instance_name,
                    "stream": src.stream(),
                    "subject": src.filter,
                },
            }
        )

    ret["io_config"]["input"] = {
        "label": f"{flow_label}_flow_input",
        "broker": {"inputs": broker_inputs},
    }

    return ret


def deploy(app: App, project_directory_name: str) -> None:
    """
    Runs a complete deploy of a given app.

    project_directory_name is the name of the directory, a peer to the
    pyproject.toml, that contains

    pyproject = toml.loads(open("pyproject.toml", "r").read())
    project_directory_name = pyproject["tool"]["poetry"]["name"]

    Will delete and recreate a "build" directory

    """
    shutil.rmtree("build/", ignore_errors=True)
    os.makedirs("build")

    project_url = upload_project(project_directory_name)
    processor_info = ProcessorInfo(config.runner_image, [project_url])

    for bucket in app.buckets:
        delete_stream(bucket.notify_subscription.stream())
        create_stream(bucket.notify_subscription.stream())

    # Delete all stores before continuing
    threads = []
    for base_store_name in SP_UTIL_STORES:
        store_name = base_store_name + app.name
        thread = threading.Thread(target=delete_store, args=(store_name,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    # Create stores
    create_threads = []
    for base_store_name in SP_UTIL_STORES:
        store_name = base_store_name + app.name
        thread = threading.Thread(target=create_store, args=(store_name,))
        create_threads.append(thread)
        thread.start()

    # All streams need to be created before any flows,
    # because we may create subscribers to flows before
    # we create publishers.
    threads = []
    for dag in app.dag_registry.values():
        thread = threading.Thread(target=delete_stream, args=(dag.name,))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()

    for dag in app.dag_registry.values():
        thread = threading.Thread(target=create_stream, args=(dag.name,))
        create_threads.append(thread)
        thread.start()

    # Join store and stream creates
    for thread in create_threads:
        thread.join()

    threads = []
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            thread = threading.Thread(target=delete_flow, args=(task.instance_name,))
            threads.append(thread)
            thread.start()

    for thread in threads:
        thread.join()

    threads = []
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            new_flow_config = flow_into_config(task, processor_info)
            thread = threading.Thread(
                target=create_flow,
                args=(
                    task.instance_name,
                    new_flow_config,
                ),
            )
            threads.append(thread)
            thread.start()

            secrets = {
                "INSTANCE_NAME": task.instance_name,
                "SEAPLANE_API_KEY": config.get_api_key(),
            }
            thread = threading.Thread(
                target=add_secrets,
                args=(
                    task.instance_name,
                    secrets,
                ),
            )
            threads.append(thread)
            thread.start()

    for thread in threads:
        thread.join()

    if not has_store(SP_APP_STORE):
        create_store(SP_APP_STORE)

    set_key(SP_APP_STORE, app.name, json.dumps(app.represention()))

    log.logger.info(
        "to write to the application endpoint,\n"
        f"post to https://{urlparse(config.carrier_endpoint).netloc}"
        f"/v1/endpoints/{app.input().endpoint}/request\n"
        "or run: seaplane request -d <data>"
    )

    write_debug_schema(app)


def destroy(app: App) -> None:
    # Destroy all flows before we destroy streams and stores
    threads = []
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            thread = threading.Thread(target=delete_flow, args=(task.instance_name,))
            threads.append(thread)
            thread.start()
    for thread in threads:
        thread.join()

    # Now destroy all streams and stores and then we're done
    threads = []
    for dag in app.dag_registry.values():
        thread = threading.Thread(target=delete_stream, args=(dag.name,))
        threads.append(thread)
        thread.start()

    for base_store_name in SP_UTIL_STORES:
        store_name = base_store_name + app.name
        thread = threading.Thread(target=delete_store, args=(store_name,))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()


async def deploy_debug(app: App, project_directory_name: str) -> None:
    shutil.rmtree("build/", ignore_errors=True)
    os.makedirs("build/docker")
    os.makedirs("build/resources")
    os.makedirs("build/config/streams")

    config.runner_image = build_debug_image(project_directory_name)

    # TODO: move this to debug_schema.py
    compose_dict: Dict[str, Any] = {
        "services": {},
        "volumes": {},
        "configs": {},
    }
    routes_str = "--routes=nats-route://nats1:6222,nats-route://nats2:6222,nats-route://nats3:6222"
    for i in range(1, 4):
        nats_node = f"nats{i}"
        nats_service = {
            nats_node: {
                "image": "docker.io/nats:2.10.9",
                "ports": [f"{4221+i}:4222", f"{8221+i}:8222"],
                "volumes": [f"{nats_node}:/data"],
                "command": [
                    f"--name={nats_node}",
                    "--cluster_name=c1",
                    f"--cluster=nats://{nats_node}:6222",
                    routes_str,  # because the line was too long
                    "--http_port=8222",
                    "--js",
                    "--sd=/data",
                ],
            }
        }
        compose_dict["services"].update(nats_service)
        nats_volume = {nats_node: {"external": True}}
        compose_dict["volumes"].update(nats_volume)

    benthos_service = {
        "endpoints": {
            "image": "jeffail/benthos",
            "volumes": [
                "./config:/config",
            ],
            "ports": [
                "4195:4195",
            ],
            "command": [
                "-c",
                "/config/config.yaml",
                "-r",
                "/config/resources.yaml",
                "streams",
                "--prefix-stream-endpoints=false",
                "/config/streams/*.yaml",
            ],
        }
    }
    compose_dict["services"].update(benthos_service)

    # Benthos can use this (and NOT nats kv) as a cache service
    redis_service = {"redis": {"image": "redis", "ports": ["6379:6379"]}}
    compose_dict["services"].update(redis_service)

    benthos_config = {
        "http": {
            "address": "0.0.0.0:4195",
            "enabled": True,
            "debug_endpoints": True,
        },
        "logger": {
            "level": "DEBUG",
        },
    }
    with open(os.path.join("build", "config", "config.yaml"), "w") as stream:
        yaml.dump(benthos_config, stream)

    benthos_resources_config = {
        "cache_resources": [{"label": "response", "redis": {"url": "redis://redis:6379"}}],
    }
    with open(os.path.join("build", "config", "resources.yaml"), "w") as stream:
        yaml.dump(benthos_resources_config, stream)

    benthos_request_config = {
        "input": {
            "http_server": {
                "path": "/endpoints/{endpoint}/request",
            },
        },
        "pipeline": {
            "threads": 1,
            "processors": [
                {"mapping": "meta _seaplane_request_id = uuid_v4()\n      root = content()"}
            ],
        },
        "output": {
            "broker": {
                "outputs": [
                    {
                        "nats_jetstream": {
                            "urls": ["nats://nats1:4222,nats://nats2:4222,nats://nats3:4222,"],
                            "subject": "_SEAPLANE_ENDPOINT.in."
                            + '${! meta("endpoint")}.${! meta("_seaplane_request_id") }',
                        },
                    },
                    {
                        "sync_response": {},
                        "processors": [
                            {"mapping": 'root.request_id = meta("_seaplane_request_id")'}
                        ],
                    },
                ],
            },
        },
    }
    with open(os.path.join("build", "config", "streams", "request.yaml"), "w") as stream:
        yaml.dump(benthos_request_config, stream)

    benthos_echo_config = {
        "input": {
            "nats_jetstream": {
                "urls": ["nats://nats1:4222,nats://nats2:4222,nats://nats3:4222,"],
                "subject": "_SEAPLANE_ENDPOINT.out.>",
                "stream": "_SEAPLANE_ENDPOINT",
                "deliver": "all",
                "durable": "echo",
            },
        },
        "output": {
            "cache": {
                "target": "response",
                "key": '${! meta("_seaplane_request_id")}',
            }
        },
    }
    with open(os.path.join("build", "config", "streams", "echo.yaml"), "w") as stream:
        yaml.dump(benthos_echo_config, stream)

    benthos_response_config = {
        "input": {
            "http_server": {
                "path": "/endpoints/{endpoint}/response/{request_id}/archive",
                "allowed_verbs": ["GET"],
            },
        },
        "pipeline": {
            "processors": [
                {
                    "cache": {
                        "resource": "response",
                        "operator": "get",
                        "key": '${! meta("request_id") }',
                    },
                }
            ],
        },
        "output": {"sync_response": {}},
    }
    with open(os.path.join("build", "config", "streams", "response.yaml"), "w") as stream:
        yaml.dump(benthos_response_config, stream)

    benthos_ai_results_config = {
        "input": {
            "http_server": {
                "path": "/ai_results/{division}/{id}",
            },
        },
        "output": {
            "nats_jetstream": {
                "urls": ["nats://nats1:4222,nats://nats2:4222,nats://nats3:4222,"],
                "subject": "_SEAPLANE_AI_RESULTS." + '${! meta("division")}.${! meta("id") }',
            },
        },
    }
    with open(os.path.join("build", "config", "streams", "ai_results.yaml"), "w") as stream:
        yaml.dump(benthos_ai_results_config, stream)

    substation_replicate_config = {
        "input": {
            "http_server": {
                "path": "/predictions",
                "allowed_verbs": ["POST"],
                "timeout": "5s",
                "sync_response": {"headers": {"Content-Type": "application/json"}},
            },
        },
        "pipeline": {
            "processors": [
                {
                    "bloblang": """root.version = meta("X-Version")
root.input = this
let claims = {
    "jti" : uuid_v4(),
    "iat" : timestamp_unix(),
    "nbf" : timestamp_unix(),
    "exp" : timestamp_unix() + 86400,
    "iss" : "substation.cplane.cloud",
    "aud" : "replicate.webhook",
    "sub" : "tnt-0ovc0mp47pu05000427at1dn00",
}
# let token = <TOKEN>
root.webhook_events_filter = ["completed"]
root.webhook = """
                    + f'"http://{os.getenv("DEBUG_MODE_PUBLIC_IP")}:9000/hooks/replicate"'
                    + ' + "?division=" + meta("X-Division")'
                    # + f'"http://{os.getenv("DEBUG_MODE_PUBLIC_IP")}:9000/hooks/replicate/"'
                    # + ' + meta("X-Division")'
                },
                {
                    "http": {
                        "url": "https://api.replicate.com/v1/predictions",
                        "verb": "POST",
                        "headers": {
                            "Accept": "application/json",
                            "Authorization": f'Token {os.getenv("REPLICATE_TOKEN")}',
                        },
                        "timeout": "5s",
                        "retries": 0,
                        "dump_request_log_level": "TRACE",
                    },
                },
                {
                    "log": {
                        "level": "INFO",
                        "message": "proxied a replicate predictions request",
                        "fields_mapping": """root.model_version = meta("X-Version")
root.division = meta("X-Division")
root.replicate_response = this
""",
                    },
                },
            ]
        },
        "output": {"sync_response": {}},
    }
    with open(
        os.path.join("build", "config", "streams", "substation_replicate.yaml"), "w"
    ) as stream:
        yaml.dump(substation_replicate_config, stream, default_style="|")

    qdrant_service = {
        "qdrant": {
            "image": "qdrant/qdrant",
            "volumes": ["./qdrant_data:/qdrant_data"],
            "configs": [
                {
                    "source": "qdrant_config",
                    "target": "qdrant/config/development.yaml",
                },
            ],
            "ports": ["6333:6333"],
        }
    }
    compose_dict["services"].update(qdrant_service)

    qdrant_config = {
        "qdrant_config": {
            "content": """
                log_level: INFO
                host: 127.0.0.1
                enable_tls: false"
            """
        },
    }
    compose_dict["configs"].update(qdrant_config)

    webhook_service = {
        "webhook": {
            "image": "ghcr.io/linuxserver-labs/webhook",
            "volumes": ["./resources/webhook-replicate.sh:/var/scripts/webhook-replicate.sh"],
            "configs": [
                {
                    "source": "webhook_config",
                    "target": "/config/hooks/hooks.json",
                }
            ],
            "environment": ["EXTRA_PARAM=-debug -logfile /tmp/webhook.log"],
            "ports": ["9000:9000"],
        }
    }

    compose_dict["services"].update(webhook_service)

    webhook_config = {
        "webhook_config": {
            "content": """
[
    {
        "id": "replicate",
        "execute-command": "/var/scripts/webhook-replicate.sh",
        "command-working-directory": "/tmp",
        "pass-arguments-to-command": [
            {
                "source": "url",
                "name": "division",
                "envname": "DIVISION"
            },
            {
                "source": "payload",
                "name": "id",
            },
            {
                "source": "entire-payload",
            },
        ],
    }
]
"""
        }
    }
    compose_dict["configs"].update(webhook_config)

    webhook_replicate_script = (
        resources.files(__package__)
        .joinpath("resources")
        .joinpath("webhook-replicate.sh")
        .read_text()
    )
    with open("build/resources/webhook-replicate.sh", "w") as file:
        file.write(webhook_replicate_script)
    os.chmod("build/resources/webhook-replicate.sh", 0o755)

    flow_streams_config = {
        "logger": {
            "level": "DEBUG",
        },
    }

    # add tasks to yaml now, start them later using "tasks" profile
    stream_configs: Dict[str, Any] = {}  # use this later to create consumers
    task_volumes: List[str] = []
    project_url = "foo://bar"  # TODO: fix this, or don't
    processor_info = ProcessorInfo(config.runner_image, [project_url])
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            new_flow_config = flow_into_debug(task, processor_info)
            compose_dict["services"].update(new_flow_config["service"])
            compose_dict["services"].update(new_flow_config["carrier-io-service"])
            compose_dict["volumes"].update(new_flow_config["volume"])
            task_volumes.extend(new_flow_config["volume"].keys())

            os.makedirs(f"build/flows/{task.instance_name}")
            # flow streams config
            with open(
                os.path.join("build", "flows", task.instance_name, "streams_config.yaml"),
                "w",
            ) as stream:
                yaml.dump(flow_streams_config, stream)

            # flow io config
            with open(
                os.path.join("build", "flows", task.instance_name, "io.yaml"),
                "w",
            ) as stream:
                yaml.dump(new_flow_config["io_config"], stream, default_style="|")
            stream_configs.update({task.instance_name: new_flow_config["io_config"]["input"]})

            # TODO: add carrier-io stream configs for logging, metrics, etc.

    with open(os.path.join("build", "debug-compose.yaml"), "w") as stream:
        yaml.dump(compose_dict, stream)

    docker = DockerClient(compose_files=["./build/debug-compose.yaml"])

    volumes = ["nats1", "nats2", "nats3", "qdrant_data"]
    volumes.extend(task_volumes)
    for volume in volumes:
        docker.volume.create(volume)

    services = ["nats1", "nats2", "nats3", "qdrant", "redis", "webhook"]
    docker.compose.up(wait=True, services=services, remove_orphans=True)
    time.sleep(10)  # TODO: we could instead try/catch the connect timeouts

    # Create app bucket notification streams
    for bucket in app.buckets:
        await delete_stream_debug(bucket.notify_subscription.stream())
        await create_stream_debug(bucket.notify_subscription.stream())

    # Create Seaplane internal streams
    for stream_name in SP_UTIL_STREAMS:
        await delete_stream_debug(stream_name)
        await create_stream_debug(stream_name)

    # Create Seaplane internal stores
    for base_store_name in SP_UTIL_STORES:
        store_name = base_store_name + app.name
        await delete_store_debug(store_name)
        await create_store_debug(store_name)

    for store_name in [f"_SP_ENDPOINT_{app.name}", SP_APP_STORE]:
        await delete_store_debug(store_name)
        await create_store_debug(store_name)

    # Create app streams
    for dag in app.dag_registry.values():
        await delete_stream_debug(dag.name)
        await create_stream_debug(dag.name)

    # services = ["endpoints", "webhook"]
    services.append("endpoints")
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            services.append(task.instance_name)
            services.append(f"carrier-io-{task.instance_name}")

            # Create consumers
            for input_config in stream_configs[task.instance_name]["broker"]["inputs"]:
                if input_config.get("nats_jetstream"):
                    await create_consumer_debug(input_config["nats_jetstream"])

    await set_key_debug(SP_APP_STORE, app.name, json.dumps(app.represention()))

    basic_schema = {
        "request_url": f"{config.carrier_endpoint}/endpoints/{app.name}/request",
        "response_url": f"{config.carrier_endpoint}/endpoints/{app.name}",
    }
    with open("build/schema.json", "w") as file:
        file.write(json.dumps(basic_schema))

    # docker.compose.up(wait=True, services=services)
    # docker.compose.up(services=services)
    docker.compose.up()


async def destroy_debug(app: App) -> None:
    docker = DockerClient(compose_files=["./build/debug-compose.yaml"])

    # take down all services
    docker.compose.down(volumes=True)

    # delete all volumes
    volumes = ["nats1", "nats2", "nats3", "qdrant_data"]
    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            volumes.append(task.instance_name)

    for volume in volumes:
        if docker.volume.exists(volume):
            docker.volume.remove(volume)
