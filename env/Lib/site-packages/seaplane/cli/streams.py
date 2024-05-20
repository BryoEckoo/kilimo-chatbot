import pprint

import click
import yaml

import seaplane.gen.carrier
from seaplane.cli import util


@click.group()
def stream():
    """Seaplane Streams"""


@stream.command()
@click.argument("stream_name")
@click.option(
    "--stream-options", help="Stream options JSON/YAML, @ to load a file, @- for stdin"
)
def create(stream_name, stream_options):
    """create a stream"""
    configuration = util.api_config()
    if not configuration:
        return
    options = {}
    if stream_options:
        options = yaml.safe_load(util.read_or_return_string(stream_options))
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.StreamApi(api_client)
        try:
            _ = api_instance.create_stream(stream_name, options)
            click.echo(click.style("Created", fg="green"))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling StreamApi->create_stream: %s\n" % e,
                    fg="red",
                )
            )


@stream.command()
def list():
    """list all streams"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.StreamApi(api_client)
        try:
            resp = api_instance.list_streams()
            for stream in resp:
                print(stream)
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling StreamApi->list_streams: %s\n" % e, fg="red"
                )
            )


@stream.command()
@click.argument("stream_name")
def delete(stream_name):
    """delete a stream"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.StreamApi(api_client)
        try:
            _ = api_instance.delete_stream(stream_name)
            click.echo(click.style("Deleted", fg="green"))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling StreamApi->delete_stream: %s\n" % e,
                    fg="red",
                )
            )


@stream.command()
@click.argument("stream_name")
def details(stream_name):
    """show details (configuration) for a stream"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.StreamApi(api_client)
        try:
            resp = api_instance.get_stream(stream_name)
            pprint.pprint(util.map_nested_dicts(resp.to_dict()))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling StreamApi->list_streams: %s\n" % e, fg="red"
                )
            )
