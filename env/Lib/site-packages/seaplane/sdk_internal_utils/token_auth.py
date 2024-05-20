from functools import wraps
from importlib.metadata import version
from typing import Callable, Concatenate, ParamSpec, TypeVar, Union

import requests
from seaplane.gen.carrier import ApiClient, ApiException, __version__

from seaplane.config import config
from seaplane.errors import HTTPError
from seaplane.logs import log
from seaplane.sdk_internal_utils.http import SDK_HTTP_ERROR_CODE
from seaplane.sdk_internal_utils.token_api import TokenAPI

_R = TypeVar("_R")
_P = ParamSpec("_P")
_SelfType = TypeVar("_SelfType")


# (self, token, *args, **kwargs) -> ret ==>  (self, *args, **kwargs) -> ret
#
# The type annotation is complicated because of PEP-612 only allowing us to put
# arguments at the front of wrapped functions, combined with methods always
# taking `self` as the first argument. We double-wrap using an explicit
# `_SelfType` so the token is passed as the first non-`self` parameter.
def method_with_token(
    func: Callable[Concatenate[_SelfType, str, _P], _R]
) -> Callable[Concatenate[_SelfType, _P], _R]:
    """
    A decorator around class methods that need JWTs.

    The JWT will be passed in as the first parameter.

    The JWTs will be pulled from the global `config` wrapper. The wrapped
    function will be retried if the token needs to be refreshed.
    """

    @wraps(func)
    def _inner(this: _SelfType, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        if config.debug_endpoint is not None:
            return func(this, "", *args, **kwargs)
        token_api = config._token_api
        try:
            return func(this, token_api.get_token(), *args, **kwargs)
        except ApiException as e:
            _renew_if_failed(token_api, e)
            return func(this, token_api.get_token(), *args, **kwargs)
        except HTTPError as e:
            _renew_if_failed(token_api, e)
            return func(this, token_api.get_token(), *args, **kwargs)
        except requests.exceptions.RequestException as err:
            _renew_if_failed(token_api, _map_request_exception(err))
            return func(this, token_api.get_token(), *args, **kwargs)

    return _inner


def with_token(func: Callable[Concatenate[str, _P], _R]) -> Callable[_P, _R]:
    """
    A decorator around bare functions that need JWTs.

    The JWT will be passed in as the first parameter.

    The JWTs will be pulled from the global `config` wrapper. The wrapped
    function will be retried if the token needs to be refreshed.
    """

    @wraps(func)
    def _inner(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        token_api = config._token_api
        try:
            return func(token_api.get_token(), *args, **kwargs)
        except ApiException as e:
            _renew_if_failed(token_api, e)
            return func(token_api.get_token(), *args, **kwargs)
        except HTTPError as e:
            _renew_if_failed(token_api, e)
            return func(token_api.get_token(), *args, **kwargs)
        except requests.exceptions.RequestException as err:
            _renew_if_failed(token_api, _map_request_exception(err))
            return func(token_api.get_token(), *args, **kwargs)

    return _inner


def _renew_if_failed(token_api: TokenAPI, exc: Union[ApiException, HTTPError]) -> None:
    """
    Attempts to renew a token if the given error calls for it.
    """
    if not token_api.auto_renew:
        raise exc

    if isinstance(exc, ApiException) and exc.status != 401:
        raise exc

    if isinstance(exc, HTTPError) and "401" not in exc.message:
        raise exc

    log.info("Auto-Renew, renewing the token...")
    token_api.renew_token()


def _map_request_exception(err: requests.exceptions.RequestException) -> HTTPError:
    """
    Maps a raw `requests` exception into a Seaplane `HTTPError`.
    """
    log.error(f"Request exception: {str(err)}")
    status_code: int = SDK_HTTP_ERROR_CODE
    if err.response:
        status_code = err.response.status_code
    return HTTPError(status_code, str(err))


def get_pdk_client(access_token: str) -> ApiClient:
    """
    Constructs a Seaplane PDK ApiClient from the given access token.
    """

    pdk_config = config.get_platform_configuration()
    pdk_config.access_token = access_token
    client = ApiClient(pdk_config)  # type: ignore
    client.set_default_header("X-Seaplane-Sdk-Version", version("seaplane"))  # type: ignore
    client.set_default_header("X-Seaplane-Pdk-Version", __version__)  # type: ignore
    client.set_default_header("User-Agent", f"SeaplaneSDK/{version('seaplane')}")  # type: ignore
    return client
