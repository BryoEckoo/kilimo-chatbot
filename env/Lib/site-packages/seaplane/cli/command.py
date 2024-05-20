import click
import click.shell_completion
import tabulate

from seaplane.cli import auth
from seaplane.cli import context as seaplane_context
from seaplane.cli import endpoints
from seaplane.cli import flows
from seaplane.cli import kv
from seaplane.cli import objects
from seaplane.cli import sql
from seaplane.cli import streams


class ContextNameType(click.ParamType):
    """Implement completion for contexts."""

    name = "context"

    def shell_complete(self, ctx, param, incomplete):
        try:
            config = seaplane_context.read()
        except IOError:
            config = seaplane_context.SeaplaneConfig()
        return [
            click.shell_completion.CompletionItem(name)
            for name in config.contexts.keys()
            if name.startswith(incomplete)
        ]


@click.group()
@click.pass_context
def cli(ctx):
    pass


@cli.group()
def config():
    """Seaplane Configuration"""


@config.command()
@click.pass_context
def init(ctx):
    """create and initialize"""
    context_name = click.prompt(
        "Enter a name for this context (will be set to current)",
        "default",
        show_default=True,
    )
    ctx.invoke(create_context, context=context_name)


@config.command(name="list-contexts")
def list_contexts():
    """list configuration contexts"""
    config = seaplane_context.read()
    table = []
    for name, value in sorted(config.contexts.items()):
        table.append(
            (
                name,
                value.api_key.name,
                value.api_key.issuer_url or "<default>",
                value.options.get("carrier-api-url", "<default>"),
                value.options.get("sql-url", "<default>"),
            )
        )
    print(
        tabulate.tabulate(
            table, headers=("name", "key", "issuer", "carrier-url", "sql-url")
        )
    )


@config.command(name="use-context")
@click.argument("context", type=ContextNameType())
def use_context(context):
    """switch to a context"""
    config = seaplane_context.read()
    try:
        config.current_context = config.contexts[context]
    except KeyError:
        click.echo(click.style('context "{}" does not exist'.format(context), fg="red"))
        return
    click.echo(click.style("switched to {}".format(context), fg="green"))
    seaplane_context.write(config)


@config.command(name="create-context")
@click.argument("context")
def create_context(context):
    """create a new context (and switch to it)"""
    try:
        config = seaplane_context.read()
    except IOError:
        config = context.SeaplaneConfig()

    key_value = click.prompt("Enter your Seaplane API Key", None, hide_input=True)
    key_name = click.prompt("Enter a name for this API Key", None)

    key = config.keys[key_name] = context.Key(key_name, key_value)
    context_obj = config.contexts[context] = context.Context(context, key)
    config.current_context = context_obj

    additional_props = click.prompt(
        "Add advanced properties [y/N]",
        default="N",
        show_default=False,
        prompt_suffix="?",
    )
    if additional_props.startswith("y") or additional_props.startswith("Y"):
        issuer_url = click.prompt("Key Issuer URL [enter to skip]", default="")
        if issuer_url:
            config.current_context.api_key.issuer_url = issuer_url
        carrier_api_url = click.prompt("Carrier API URL [enter to skip]", default="")
        if carrier_api_url:
            config.current_context.options["carrier-api-url"] = carrier_api_url
    seaplane_context.write(config)


@config.command(name="current-context")
def current_context():
    """get the name of the current context"""
    try:
        config = seaplane_context.read()
    except IOError:
        config = seaplane_context.SeaplaneConfig()
    click.echo(config.current_context.name)


# Add groups.
cli.add_command(auth.auth)
cli.add_command(endpoints.endpoints)
cli.add_command(flows.flow)
cli.add_command(kv.kv)
cli.add_command(objects.object_group)
cli.add_command(sql.sql)
cli.add_command(streams.stream)
