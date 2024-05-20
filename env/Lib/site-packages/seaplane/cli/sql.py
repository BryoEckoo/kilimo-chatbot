import os
import urllib3
import json
import shutil
import click
import tabulate

from seaplane.cli import context


@click.group()
def sql():
    """Seaplane SQL (database access)"""


@sql.command()
def list_databases():
    """list all databases"""
    config = context.read()
    seaplane_jwt = config.current_context.options.get("jwt")
    sql_url = config.current_context.options.get("sql-url")
    token = "Bearer {}".format(seaplane_jwt)

    http = urllib3.PoolManager()
    sql_endpoint = f"https://{sql_url}/v1/databases"
    resp = http.request("GET", sql_endpoint, headers={"Authorization": token})

    if resp.status == 200:
        databases_resp = json.loads(resp.data.decode("utf-8"))
        table_data = [(d["database"],) for d in databases_resp["databases"]]
        click.echo(tabulate.tabulate(table_data, headers=("name",)))
    elif resp.status == 401:
        click.echo(
            click.style(
                "Insufficient permissions to perform that action",
                fg="red",
            )
        )
    else:
        click.echo(
            click.style(
                f"Internal server error ({resp.status}) encountered while listing databases",
                fg="red",
            )
        )


@sql.command()
@click.argument("database_name")
def shell(database_name):
    """connect to the Seaplane SQL database"""

    # Coherence check: ensure psql is available before going any further
    psql_path = shutil.which("psql")
    if psql_path is None:
        click.echo(
            click.style(
                "psql not found in PATH, please install postgresql",
                fg="red",
            )
        )
        return

    # Get JWT to auth to DB
    config = context.read()
    seaplane_jwt = config.current_context.options.get("jwt")
    sql_url = config.current_context.options.get("sql-url") or context.DEFAULT_SQL_URL

    # Replace current process with a psql shell
    os.execve(
        psql_path,
        [
            "psql",
            f"postgres://{database_name}:{seaplane_jwt}@{sql_url}/{database_name}",
        ],
        os.environ,
    )


@sql.command()
@click.argument("database_name")
def uri(database_name):
    """get the connection URI for the Seaplane SQL database"""

    # Get JWT to auth to DB
    config = context.read()
    seaplane_jwt = config.current_context.options.get("jwt")
    sql_url = config.current_context.options.get("sql-url") or context.DEFAULT_SQL_URL

    click.echo(f"postgres://{database_name}:{seaplane_jwt}@{sql_url}/{database_name}")
