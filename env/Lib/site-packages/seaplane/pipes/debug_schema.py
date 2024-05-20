import json
import os
from typing import Any, Dict

from seaplane.config import config
from seaplane.pipes import App


def build_debug_schema(app: App) -> Dict[str, Any]:
    """
    Constructs a JSON-friendly / simple type structure describing
    some parts of the application for use by associated tooling.
    """
    # This is a legacy debug schema, and its structure shouldn't be
    # relied on.

    app_desc: Dict[str, Any] = {
        "id": app.name,
        "tasks": [],
    }

    for dag in app.dag_registry.values():
        for task in dag.flow_registry.values():
            task_desc = {
                "id": task.instance_name,
                "name": task.work.__name__,
                "replicas": task.replicas,
            }

            app_desc["tasks"].append(task_desc)

    schema: Dict[str, Any] = {"apps": {app.name: app_desc}}

    schema["carrier_endpoint"] = config.carrier_endpoint
    schema["identity_endpoint"] = config.identify_endpoint

    return schema


# Just for mockability.
def write_debug_schema(app: App) -> None:
    schema = build_debug_schema(app)
    with open(os.path.join("build", "schema.json"), "w") as file:
        json.dump(schema, file, indent=2)
