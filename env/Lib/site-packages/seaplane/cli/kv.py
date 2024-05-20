import sys

import click
import tabulate
import yaml

import seaplane.gen.carrier
from seaplane.cli import util


@click.group()
def kv():
    """Seaplane KV Store"""


@kv.command(name="list-stores")
def list_stores():
    """list stores"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            resp = api_instance.list_stores()
            table = []
            for name in sorted(resp):
                table.append((name,))
            print(tabulate.tabulate(table, headers=("name",)))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling KeyValueApi->get_stores: %s\n" % e.reason,
                    fg="red",
                )
            )


@kv.command(name="create-store")
@click.argument("store_name")
@click.option(
    "--store-options", help="Store options JSON/YAML, @ to load a file, @- for stdin"
)
def create_store(store_name, store_options):
    """create a KV store"""
    configuration = util.api_config()
    if not configuration:
        return
    options = {}
    if store_options:
        options = yaml.safe_load(util.read_or_return_string(store_options))
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            _ = api_instance.create_store(store_name, options)
            click.echo(click.style("Created {}".format(store_name), fg="green"))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling KeyValueApi->create_store: %s\n" % e.reason,
                    fg="red",
                )
            )
            click.echo(e)


@kv.command(name="delete-store")
@click.argument("store_name")
@click.confirmation_option(prompt="Are you sure you want to delete store?")
def delete_store(store_name):
    """delete a KV store"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            _ = api_instance.delete_store(store_name)
            click.echo(click.style("Deleted {}".format(store_name), fg="green"))
        except seaplane.gen.carrier.ApiException as e:
            click.echo(
                click.style(
                    "Exception when calling KeyValueApi->delete_store: %s\n" % e.reason,
                    fg="red",
                )
            )


@kv.command(name="set")
@click.argument("store_name")
@click.argument("key")
@click.argument("value")
@click.option(
    "--version-id",
    help="Unique version ID, succeed only if it matches the current version ID",
)
@click.option(
    "--create-exclusive/--no-create-exclusive",
    help="Create only if it does not exist",
    type=bool,
    default=False,
)
def set_key(store_name, key, value, version_id, create_exclusive):
    """set a key value"""
    configuration = util.api_config()
    if not configuration:
        return
    if create_exclusive and version_id is not None:
        raise click.UsageError(
            "--create-exclusive and --version cannot be used at the same time"
        )
    if_not_match = "*" if create_exclusive else None
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            _ = api_instance.put_key(
                key,
                store_name,
                if_match=version_id,
                if_not_match=if_not_match,
                body=util.read_or_return_string(value).encode("utf-8"),
            )
        except seaplane.gen.carrier.ApiException as e:
            if e.status == 412:
                if version_id is None:
                    print("key already exists")
                else:
                    print("version ID ({}) does not match".format(version_id))
            else:
                print("Exception when calling KeyValueApi->put_key: %s\n" % e)


@kv.command(name="get")
@click.argument("store_name")
@click.argument("key")
@click.option(
    "--version-id",
    help="Unique version ID, succeed only if it matches the current revision",
)
def get_key(store_name, key, version_id):
    """get a key value"""
    configuration = util.api_config()
    if not configuration:
        return
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            resp = api_instance.get_key_with_http_info(
                key, store_name, if_match=version_id
            )
            print(resp.data)
            print("revision: {}".format(resp.headers["ETag"]), file=sys.stderr)  # type: ignore
        except seaplane.gen.carrier.ApiException as e:
            if e.status == 412:
                print("version ({}) does not match".format(version_id))
            else:
                print("Exception when calling KeyValueApi->get_key: %s\n" % e)


# TODO(ian): Purge is broken, seemingly because enum strings in query
# params are broken in codegen.
@kv.command(name="del")
@click.argument("store_name")
@click.argument("key")
@click.option(
    "--version-id",
    help="Unique version ID, succeed only if it matches the current revision",
)
@click.option(
    "--purge/--no-purge", help="Delete and purge all values", type=bool, default=False
)
def del_key(store_name, key, version_id, purge):
    """delete a key"""
    configuration = util.api_config()
    if not configuration:
        return
    purge_value = "true" if purge else "false"
    with seaplane.gen.carrier.ApiClient(configuration) as api_client:
        api_instance = seaplane.gen.carrier.KeyValueApi(api_client)
        try:
            _ = api_instance.delete_key(key, store_name, version_id, purge_value)
            click.echo(click.style("Deleted {}".format(key), fg="green"))
        except seaplane.gen.carrier.ApiException as e:
            if e.status == 412:
                print("version ({}) does not match".format(version_id))
            else:
                print("Exception when calling KeyValueApi->del_key: %s\n" % e)
