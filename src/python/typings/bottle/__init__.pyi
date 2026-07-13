from collections.abc import Callable, Iterable, Mapping, MutableMapping
from typing import Never, ParamSpec, Protocol, TypeVar, overload
from wsgiref.types import WSGIApplication

_P = ParamSpec("_P")
_R = TypeVar("_R")
RouteCallback = Callable[..., object]
RouteDecorator = Callable[[RouteCallback], RouteCallback]

class _ReadableBody(Protocol):
    def read(self, size: int = -1) -> bytes: ...

class HeaderDict(dict[str, str]): ...
class FormsDict(dict[str, str]): ...
class WSGIHeaderDict(dict[str, str]): ...

class BaseRequest:
    environ: MutableMapping[str, object]
    method: str
    path: str
    url: str
    params: FormsDict
    query: FormsDict
    forms: FormsDict
    cookies: FormsDict
    body: _ReadableBody
    content_type: str | None
    content_length: int
    json: object
    def bind(self, environ: MutableMapping[str, object] | None = None) -> None: ...
    @overload
    def get_header(self, name: str, default: str) -> str: ...
    @overload
    def get_header(self, name: str, default: None = None) -> str | None: ...
    @overload
    def get_cookie(self, key: str, default: str, secret: str | None = None) -> str: ...
    @overload
    def get_cookie(self, key: str, default: None = None, secret: str | None = None) -> str | None: ...

class BaseResponse:
    status: int | str
    status_code: int
    status_line: str
    body: object
    content_type: str
    content_length: int
    cache_control: str
    def set_header(self, name: str, value: str) -> None: ...
    def add_header(self, name: str, value: str) -> None: ...
    def get_header(self, name: str, default: str | None = None) -> str | None: ...
    def set_cookie(
        self,
        name: str,
        value: str,
        secret: str | None = None,
        max_age: int | None = None,
        expires: int | str | None = None,
        domain: str | None = None,
        path: str | None = None,
        secure: bool | None = None,
        httponly: bool | None = None,
        samesite: str | None = None,
    ) -> None: ...
    def delete_cookie(self, key: str, **kwargs: object) -> None: ...

class LocalRequest(BaseRequest): ...
class LocalResponse(BaseResponse): ...

request: LocalRequest
response: LocalResponse

class HTTPResponse(BaseResponse, BaseException):
    def __init__(
        self,
        body: object = "",
        status: int | str | None = None,
        headers: Mapping[str, str] | None = None,
        **more_headers: str,
    ) -> None: ...

class HTTPError(HTTPResponse):
    exception: BaseException | None
    traceback: str | None

class Route:
    rule: str
    config: dict[str, object]

class Bottle:
    def __init__(self, catchall: bool = True, autojson: bool = True) -> None: ...
    def hook(self, name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...
    def route(
        self,
        path: str | None = None,
        method: str = "GET",
        callback: RouteCallback | None = None,
        name: str | None = None,
        apply: object = None,
        skip: object = None,
        **config: object,
    ) -> RouteCallback | RouteDecorator: ...
    def get(self, path: str | None = None, **options: object) -> RouteDecorator: ...
    def post(self, path: str | None = None, **options: object) -> RouteDecorator: ...
    def put(self, path: str | None = None, **options: object) -> RouteDecorator: ...
    def delete(self, path: str | None = None, **options: object) -> RouteDecorator: ...
    def match(self, environ: Mapping[str, object]) -> tuple[Route, dict[str, str]]: ...
    def __call__(self, environ: MutableMapping[str, object], start_response: Callable[[str, list[tuple[str, str]]], object]) -> Iterable[bytes]: ...

class ServerAdapter:
    host: str
    port: int
    quiet: bool
    def __init__(self, host: str = "127.0.0.1", port: int = 8080, **options: object) -> None: ...
    def run(self, handler: WSGIApplication) -> None: ...

def run(
    app: Bottle | None = None,
    server: str | ServerAdapter = "wsgiref",
    host: str = "127.0.0.1",
    port: int = 8080,
    interval: int = 1,
    reloader: bool = False,
    quiet: bool = False,
    plugins: object = None,
    debug: bool | None = None,
    **kargs: object,
) -> None: ...

def abort(code: int = 500, text: str | None = None, **headers: str) -> Never: ...
def redirect(url: str, code: int = 303) -> Never: ...
def static_file(filename: str, root: str, mimetype: str | None = None, download: bool | str = False, charset: str = "UTF-8") -> HTTPResponse: ...
