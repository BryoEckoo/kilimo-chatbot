import json
from typing import Any, Dict, List

import requests
from tabulate import tabulate

from seaplane.config import config
from seaplane.errors import SeaplaneError
from seaplane.kv import KeyValueStorageAPI
from seaplane.logs import log
from seaplane.pipes import App
from seaplane.sdk_internal_utils.http import headers
from seaplane.sdk_internal_utils.token_auth import with_token

ENDPOINTS_STREAM = "_SEAPLANE_ENDPOINT"
SUBSTATION_RESULTS_STREAM = "_SEAPLANE_AI_RESULTS"
SP_APP_STORE = "_SP_APP_STORE"


@with_token
def get_messages(token: str, subject: str) -> str:
    """
    Returns a string with the number of messages in stream subject
      OR "Stream not found" if it does not exist
      OR "0" if the stream exists but subject does not
    """
    stream_name = subject.split(".")[0]
    url = f"{config.carrier_endpoint}/stream/{stream_name}"

    resp = requests.get(
        url,
        headers=headers(token),
    )
    if resp.status_code == 404:
        return "N/A"

    messages = 0
    stream_info = json.loads(resp.content)
    if stream_info.get("details") is not None:
        for key in stream_info["details"]["subjects"].keys():
            if key[0 : len(subject)] == subject:
                messages += stream_info["details"]["subjects"][key]

    return str(messages)


@with_token
def get_status(token: str, flow_name: str) -> str:
    """
    Returns the status of a flow
      OR "N/A" if status not available
    """
    url = f"{config.carrier_endpoint}/flow/{flow_name}/status"
    if config.region is not None:
        url += f"?region={config.region}"
    resp = requests.get(
        url,
        headers=headers(token),
    )
    if resp.status_code == 200:
        status = json.loads(resp.content)
        replicas = 1
        dead = 0
        output: List[str] = []
        for alloc in status.keys():
            if status[alloc] != "dead":
                output.append(f"{replicas}: {status[alloc]}")
                # We could instead show the alloc id, but it gets busy...
                # output.append(f"{alloc}: {status[alloc]}")
                replicas += 1
            else:
                dead += 1
        if dead > 0:
            output.append(f"{dead} completed task(s)")
        return "\n".join(output)
    return "N/A"


def sort_nodes(app_rep: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Returns a list of nodes sorted by their edges from IN to OUT
    """
    nodes = app_rep["graph"]["nodes"]
    edges = app_rep["graph"]["edges"]

    in_nodes = [node for node in nodes if node["id"] == "in"]
    sorted_nodes = in_nodes[:]
    for sorted_node in sorted_nodes:
        target_edges = [edge for edge in edges if edge["source"] == sorted_node["id"]]
        for target_edge in target_edges:
            target_nodes = [node for node in nodes if node["id"] == target_edge["target"]]
            for target_node in target_nodes:
                if target_node not in sorted_nodes:
                    sorted_nodes.append(target_node)
            target_edge_source_edges = [
                edge
                for edge in edges
                if (edge["target"] == target_edge["target"] and edge != target_edge)
            ]
            for target_edge_source_edge in target_edge_source_edges:
                target_edge_source_nodes = [
                    node for node in nodes if node["id"] == target_edge_source_edge["source"]
                ]
                for target_edge_source_node in target_edge_source_nodes:
                    if (
                        target_edge_source_node.get("substation_id")
                        and "subscription" in target_edge_source_node["id"]
                        and target_edge_source_node not in sorted_nodes
                    ):
                        sorted_nodes.append(target_edge_source_node)

    return sorted_nodes


def status(app: App) -> None:
    """
    Prints the status of resources associated with an app
    """
    config.log_level(log.CRITICAL)
    kv = KeyValueStorageAPI()
    try:
        app_rep = json.loads(kv.get_key(SP_APP_STORE, app.name))
    except Exception:
        raise SeaplaneError(
            "Cannot load app representation. Try moving to the dir where you deploy your app."
        )

    table_headers = ["Task Name", "Status", "Messages In", "Messages Out"]
    table: List[Any] = []

    edges = app_rep["graph"]["edges"]
    sorted_nodes = sort_nodes(app_rep)
    for node in sorted_nodes:
        if node["id"] in ("in", "out") or (
            node.get("substation_id") and "subscription" in node["id"]
        ):
            continue
        node_name = node["name"]
        node_status = get_status(node_name)
        outputs = [edge["target"] for edge in edges if edge["source"] == node["id"]]
        inputs = [edge["source"] for edge in edges if edge["target"] == node["id"]]
        in_list: List[Any] = []
        for input in inputs:
            if input == "in":
                endpoint = f"{ENDPOINTS_STREAM}.in.{app.name}"
                in_list.insert(0, f"IN: {get_messages(endpoint)}")
                continue

            stream, subject = input.split(".")[0:2]
            if subject.split("-")[-1] == "subscription":
                full_subject = f"{SUBSTATION_RESULTS_STREAM}.{app.name}"
                in_list.append(f"SUBSTATION: {get_messages(full_subject)}")
            else:
                full_subject = f"{stream}.{subject}"
                in_list.append(f"{subject}: {get_messages(full_subject)}")
        messages_in = "\n".join(in_list)

        out_list = []
        for output in outputs:
            if output == "out":
                endpoint = f"{ENDPOINTS_STREAM}.out.{app.name}"
                out_list.append(f"OUT: {get_messages(endpoint)}")
                continue

            stream, subject = node["id"].split(".")[0:2]
            full_subject = f"{stream}.{subject}"
            out_list.insert(0, f"{subject}: {get_messages(full_subject)}")
        messages_out = "\n".join(out_list)

        table.append([node_name, node_status, messages_in, messages_out])

    print("\n")
    # "fancy_grid" handles newlines better than "outline"
    print(tabulate(table, table_headers, tablefmt="fancy_grid"))
