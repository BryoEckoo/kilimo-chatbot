from __future__ import annotations

import os

import yaml


DEFAULT_KEY_ISSUER_URL = "https://flightdeck.cplane.cloud/identity/token"
DEFAULT_SQL_URL = "sql.cplane.cloud"


class SeaplaneConfig:
    """SeaplaneConfig is responsible for reading and writing all CLI configuration."""

    def __init__(self):
        self.keys: dict[str, Key] = {}
        self.contexts: dict[str, Context] = {}

    def parse_yaml(self, config_s) -> SeaplaneConfig:
        config = yaml.safe_load(config_s)

        # Add keys.
        for key in config.get("keys", []):
            self.keys[key["name"]] = Key(
                key["name"], key["value"], key.get("issuer-url", None)
            )

        # Add contexts
        for context in config["contexts"]:
            name = context["name"]
            options = context["context"]
            key = self.keys[options.pop("api-key")]
            self.contexts[name] = Context(name, key, **options)

        # Set current context.
        self.current_context: Context = self.contexts[config["current-context"]]

        return self

    def to_yaml(self, config_s):
        config = {}

        # Set current context if this one is current.
        assert self.current_context is not None
        config["current-context"] = self.current_context.name

        # Add contexts
        config["contexts"] = []
        for context in self.contexts.values():
            options = context.options
            options["api-key"] = context.api_key.name
            config["contexts"].append(
                {
                    "name": context.name,
                    "context": options,
                }
            )

        # Add keys
        config["keys"] = []
        for key in self.keys.values():
            key_dict = {
                "name": key.name,
                "value": key.value,
            }
            if key.issuer_url:
                key_dict["issuer-url"] = key.issuer_url
            config["keys"].append(key_dict)

        yaml.dump(config, config_s)


class Key:
    """Key is a seaplane tenant key (from flightdeck).

    These Keys are used to retrieve a JWT from,
    e.g. https://flightdeck.cplane.cloud/identity/token
    """

    def __init__(self, name: str, value: str, issuer_url: str | None = None):
        self._name = name
        self.value = value
        self.issuer_url = issuer_url

    @property
    def name(self):
        return self._name


class Context:
    """Context is a seaplane context (tenant, key, overrides, etc)."""

    def __init__(self, name: str, api_key: Key, **options: str | bytes):
        self._name = name
        self.api_key = api_key
        self.options = options

    @property
    def name(self):
        return self._name


def exists(path: str | None = None):
    """exists checks for the existence of a config file at `path`."""
    path = (
        path or os.getenv("SEAPLANE_CONFIG") or os.path.expanduser("~/.seaplane/config")
    )
    return os.path.exists(path)


def read(path: str | None = None):
    """read_config parses a config file and returns a `SeaplaneConfig` object."""
    path = (
        path or os.getenv("SEAPLANE_CONFIG") or os.path.expanduser("~/.seaplane/config")
    )
    with open(path, "r") as config_file:
        return SeaplaneConfig().parse_yaml(config_file)


def write(config: SeaplaneConfig, path: str | None = None):
    """write_config writes a config file"""
    path = (
        path or os.getenv("SEAPLANE_CONFIG") or os.path.expanduser("~/.seaplane/config")
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as config_file:
        config_file.truncate(0)
        config.to_yaml(config_file)
