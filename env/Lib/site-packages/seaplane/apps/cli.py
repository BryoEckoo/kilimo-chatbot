"""
command line interface
"""

from argparse import ArgumentParser
from importlib import resources
from importlib.metadata import version
import json
import os
import re
from typing import Any, Dict, cast

from cookiecutter.main import cookiecutter
import requests
import toml

import seaplane.run_load_dotenv as denv
from seaplane.sdk_internal_utils.http import headers
from seaplane.substation import Substation
from seaplane.vector import vector_store

PROJECT_TOML = "pyproject.toml"


class CliError(Exception):
    pass


def get_project() -> Dict[str, Any]:
    """
    throws an error if the project directory isn't structured in ways
    we expect it to be.
    """
    if not os.path.exists(PROJECT_TOML):
        raise CliError(
            f"{PROJECT_TOML} file missing, run seaplane init"
            " (or change directory to the root of your Seaplane project)"
        )

    project = toml.loads(open(PROJECT_TOML, "r").read())
    project_name = project["tool"]["poetry"]["name"]
    main = project["tool"]["seaplane"].get("main", None)

    if not os.path.exists(project_name):
        raise CliError(
            f"source file {project_name} directory missing, \
                the source code has to live under {project_name} directory."
        )

    if not project_name or not main:
        raise CliError(f"{PROJECT_TOML} not a valid Seaplane project.")

    return project


def cli_deploy(debug: bool) -> None:
    """
    Deploys an app
    """
    project = get_project()
    project_name = project["tool"]["poetry"]["name"]
    main_script = project["tool"]["seaplane"]["main"]
    deploy_cmd = f"poetry run python {project_name}/{main_script} deploy"
    if debug:
        deploy_cmd = deploy_cmd + "_debug"
    os.system(deploy_cmd)


def cli_destroy(debug: bool) -> None:
    """
    Destroys an app (deletes streams and flows)
    """
    project = get_project()
    project_name = project["tool"]["poetry"]["name"]
    main_script = project["tool"]["seaplane"]["main"]
    destroy_cmd = f"poetry run python {project_name}/{main_script} destroy"
    if debug:
        destroy_cmd = destroy_cmd + "_debug"
    os.system(destroy_cmd)


def cli_models(search: str, params: bool) -> None:
    """
    Lists the supported models in this SDK
    """
    keys = [key for key in Substation.models.keys() if search.lower() in key]
    # The "default" model was removed, but it might come back
    # if "default" in keys:
    #     keys[keys.index("default")] = Substation.models["default"]["model_name"][0]
    if params:
        for key in keys:
            params_list = ", ".join(Substation.models[key]["params"])
            print(f"{key} params:\n  {params_list}")
    else:
        models = "\n".join(keys)
        print(models)


def cli_status(watch: bool) -> None:
    """
    Provides status of an app's resources.
      If watch is enabled it must be in your path.
    """
    project = get_project()
    project_name = project["tool"]["poetry"]["name"]
    main_script = project["tool"]["seaplane"]["main"]
    if watch:
        os.system(f"watch poetry run python {project_name}/{main_script} status")
    else:
        os.system(f"poetry run python {project_name}/{main_script} status")


def get_token(fd_url: str) -> str:
    """
    Gets a token from Flightdeck
    """
    # Try to get the API key from .env
    api_key = os.environ.get("SEAPLANE_API_KEY", "None")
    if denv.loaded is False or api_key == "None":
        raise CliError("Set your API key in .env")

    response = requests.post(fd_url, json={}, headers=headers(api_key))
    if response.ok:
        payload = cast(Dict[str, str], response.json())
    else:
        error_body = response.text
        raise CliError(f"Bad Access token request code {response.status_code}, error {error_body}")

    return payload["token"]


def cli_request(data: str) -> None:
    """
    Performs an endpoint request, writes and prints the request_id
    """
    if data[0] == "@":
        try:
            data_bytes = open(data[1:], "rb").read()
        except Exception:
            raise CliError("Cannot load data file.")
    else:
        data_bytes = data.encode()

    # Load schema file
    try:
        schema_file = open(os.path.join("build", "schema.json"), "r")
        schema = json.load(schema_file)
    except Exception:
        raise CliError(
            "Cannot load build schema. Try moving to the directory where you deploy your app."
        )

    # TODO: better error handling
    # debug mode?
    if not schema.get("carrier_endpoint"):
        url = schema.get("request_url")
        resp = requests.post(
            url, headers={"Content-Type": "application/octet-stream"}, data=data_bytes
        )
        resp_content = json.loads(resp.content)
        request_id = resp_content["request_id"]

        # save the request_id to a file
        with open(".request_id", "a") as f:
            f.write(f"{request_id}\n")
        print(f"\nrequest_id: {request_id}")

        return

    # Get a token
    fd_url = schema["identity_endpoint"] + "/token"
    token = get_token(fd_url)

    # Make the request
    for app in schema["apps"]:
        app_id = schema["apps"][app]["id"]
    carrier_endpoint = schema["carrier_endpoint"]
    url = f"{carrier_endpoint}/endpoints/{app_id}/request"
    bearer = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
    resp = requests.post(url, headers=bearer, data=data_bytes)

    # Empty response handling
    if not resp.content:
        raise CliError(f"Error: empty response from endpoint (code={resp.status_code})")

    # Invalid response format handling
    try:
        resp_content = json.loads(resp.content)
    except json.decoder.JSONDecodeError:
        raise CliError(
            f"Error: invalid JSON response from endpoint (code={resp.status_code}): {resp.content!r}"  # noqa: E501
        )

    # Unexpected response code handling
    if resp.status_code < 200 or resp.status_code >= 300:
        raise CliError(
            f"Error: unexpected response code {resp.status_code} from endpoint: {resp.content!r}"
        )

    # Malformed response handling
    if "request_id" not in resp_content:
        raise CliError(
            f"Error: invalid response from endpoint, missing request_id (code={resp.status_code})"
        )

    request_id = resp_content["request_id"]

    # save the request_id to a file
    with open(".request_id", "a") as f:
        f.write(f"{request_id}\n")
    print(f"\nrequest_id: {request_id}")


def cli_response(request_id: str) -> None:
    """
    Gets an archive response from the endpoint
    """
    # TODO: Could (with arg?) show a list of the last 5-10 ids to choose
    if request_id == "":
        try:
            with open(".request_id", "r") as f:
                request_id = f.readlines()[-1].strip("\n")
        except Exception:
            print("No request_id provided as argument or in file")

    # Load schema file
    try:
        schema_file = open(os.path.join("build", "schema.json"), "r")
        schema = json.load(schema_file)
    except Exception:
        raise CliError(
            "Cannot load build schema. Try moving to the directory where you deploy your app."
        )

    # TODO: better error handling
    # debug mode?
    if not schema.get("carrier_endpoint"):
        base_url = schema.get("response_url")
        url = f"{base_url}/response/{request_id}/archive"
        resp = requests.get(url, headers={"accept": "application/octet-stream"})
        print(f"\nResponse archive for request_id {request_id}:")
        print(resp.content)
        return

    # Get a token
    fd_url = schema["identity_endpoint"] + "/token"
    token = get_token(fd_url)

    # Make the request
    for app in schema["apps"]:
        app_id = schema["apps"][app]["id"]
    carrier_endpoint = schema["carrier_endpoint"]
    url = f"{carrier_endpoint}/endpoints/{app_id}/response/{request_id}/archive"
    bearer = {"Authorization": f"Bearer {token}", "accept": "application/octet-stream"}
    resp = requests.get(url, headers=bearer, params={"pattern": ".>", "format": "json_array"})
    print(f"\nResponse archive for request_id {request_id}:")
    print(resp.content)


def cli_init(project_name: str, project_directory: str) -> None:
    app_template = resources.files(__package__).joinpath("resources").joinpath("app-template")

    if not project_name.isidentifier():
        suggestion = re.sub("[^A-Za-z0-9_]", "", project_name)
        raise CliError(f'can\'t make a new project named "{project_name}". Try "{suggestion}"')

    extra_context = {"project_slug": project_name, "seaplane_version": version("seaplane")}

    cookiecutter(
        str(app_template),
        output_dir=project_directory,
        no_input=True,  # Disable any interactive prompts
        extra_context=extra_context,
    )

    print(f"🛩️  {project_name} project generated successfully!")


def main() -> None:
    parser = ArgumentParser(prog="seaplane", description="Seaplane Apps command line interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy command")
    deploy_parser.add_argument(
        "--debug",
        default=False,
        action="store_true",
        help="Deploy in debug mode",
    )

    # Destroy command
    destroy_parser = subparsers.add_parser("destroy", help="Remove your App and associated tasks")
    destroy_parser.add_argument(
        "--debug",
        default=False,
        action="store_true",
        help="Destroy a debug mode deployment",
    )

    # Init command
    init_parser = subparsers.add_parser("init", help="Init command")
    init_parser.add_argument("app", help="Seaplane Apps name")

    # Models command
    models_parser = subparsers.add_parser("models", help="List supported models")
    models_parser.add_argument(
        "--search",
        "-s",
        type=str,
        default="",
        help="Model string to search",
    )
    models_parser.add_argument(
        "--params",
        "-p",
        default=False,
        action="store_true",
        help="Show available params for each model",
    )

    # Status command
    stat_parser = subparsers.add_parser("status", help="Status of your app and resources")
    stat_parser.add_argument(
        "--watch",
        "-w",
        default=False,
        action="store_true",
        help="Use UNIX watch command to refresh status every 2s",
    )

    # Endpoints commands
    req_parser = subparsers.add_parser("request", help="Send data to the request endpoint")
    req_parser.add_argument(
        "--data", "-d", type=str, help="Request body, @ to load a file, @- for stdin"
    )
    resp_parser = subparsers.add_parser("response", help="Get data from the response endpoint")
    resp_parser.add_argument("--request_id", "-r", type=str, help="Request ID")

    # Vector Backup command
    vectorbackup_parser = subparsers.add_parser(
        "vector-backup", help="Initialize Vector Store backup"
    )
    vectorbackup_parser.add_argument(
        "--index", "-i", type=str, help="Backup this vector store index"
    )
    vectorbackup_parser.add_argument(
        "--path", "-p", type=str, help="Backup vector store index to this directory"
    )

    # Vector Restore command
    vectorrestore_parser = subparsers.add_parser(
        "vector-restore", help="Initalize Vector Store restore"
    )
    vectorrestore_parser.add_argument(
        "--index", "-i", type=str, help="Restore this vector store index"
    )
    vectorrestore_parser.add_argument(
        "--path", "-p", type=str, help="Restore vector store index from backup in this directory"
    )

    # Version argument
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="%(prog)s {version}".format(version=version("seaplane")),
    )

    args = parser.parse_args()

    try:
        if args.command == "deploy":
            cli_deploy(args.debug)

        elif args.command == "destroy":
            cli_destroy(args.debug)

        elif args.command == "init":
            cli_init(args.app, ".")

        elif args.command == "models":
            cli_models(args.search, args.params)

        elif args.command == "status":
            cli_status(args.watch)

        elif args.command == "request":
            if args.data is None:
                raise CliError("Error: expected --data argument to be provided")

            cli_request(args.data)

        elif args.command == "response":
            request_id = ""
            if args.request_id:
                request_id = args.request_id
            cli_response(request_id)

        elif args.command == "vector-backup":
            if args.index and args.path:
                vector_store.backup(index_name=args.index, path=args.path)

        elif args.command == "vector-restore":
            if args.index and args.path:
                vector_store.restore(index_name=args.index, path=args.path)

        else:
            parser.print_help()

    except CliError as e:
        print(e)
        exit(1)

    exit(0)


if __name__ == "__main__":
    main()
