import click
import jwt
import tabulate

from seaplane.cli import context
from seaplane.cli import util


@click.group()
def auth():
    """Seaplane Auth (JWTs/Keys)"""


@auth.command()
def refresh():
    """refresh JWT"""
    util.refresh_jwt()


@auth.command()
def details():
    """show details about the current JWT (if any)"""
    config = context.read()
    seaplane_jwt = config.current_context.options.get("jwt")
    if not seaplane_jwt:
        click.echo(
            click.style("Error: ", fg="red")
            + "no JWT for this context (try auth refresh)"
        )
        return
    decoded = jwt.decode(jwt=seaplane_jwt, options={"verify_signature": False})
    click.echo(tabulate.tabulate(decoded.items(), headers=("claim", "value")))


@auth.command(name="jwt")
def jwt_dump():
    """echo the current JWT"""
    config = context.read()
    seaplane_jwt = config.current_context.options.get("jwt")
    print(seaplane_jwt)
