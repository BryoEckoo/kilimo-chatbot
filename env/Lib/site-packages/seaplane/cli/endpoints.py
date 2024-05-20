import click

import seaplane.gen.carrier
from seaplane.cli import util


@click.group(name="endpoints")
def endpoints():
    """Seaplane Endpoints"""


@endpoints.command(name="request")
@click.argument("endpoint")
@click.option(
    "--data", "-d", help="Request body, @ to load a file, @- for stdin", required=True
)
def request(endpoint, data):
    """send data to the request endpoint"""
    configuration = util.api_config()
    if not configuration:
        return
    body = util.read_or_return_string(data)
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.EndpointApi(api_client)
        try:
            resp = api_instance.submit_to_endpoint(endpoint, {}, body.encode("utf-8"))
            print(resp)
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling EndpointApi->submit_to_endpoint: %s\n"
                    % e.reason,
                    fg="red",
                )
            )
            click.echo(e.body)


@endpoints.command(name="response")
@click.argument("endpoint")
@click.argument("message_id")
def response(endpoint, message_id):
    """get data from the response endpoint"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.EndpointApi(api_client)
        try:
            resp = api_instance.get_from_endpoint(endpoint, message_id)
            print(resp)
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling EndpointApi->get_from_endpoint: %s\n"
                    % e.reason,
                    fg="red",
                )
            )
            click.echo(e.body)
