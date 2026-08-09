"""Small compatibility layer for AstrBot's protected plugin Web API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:  # AstrBot v4.27.2+
    from astrbot.api.web import error_response, json_response, request

    MODERN_WEB_API = True
except ImportError:  # pragma: no cover - retained for local SDK/test compatibility.
    from quart import jsonify, request

    MODERN_WEB_API = False

    def json_response(
        data: Any = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = jsonify({} if data is None else data)
        response.status_code = status_code
        if headers:
            response.headers.update(headers)
        return response

    def error_response(
        message: str,
        *,
        status_code: int = 400,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return json_response(
            {"status": "error", "message": message, "data": data},
            status_code=status_code,
            headers=headers,
        )


async def read_json_body(default: Any = None) -> Any:
    """Read a JSON request body from the current AstrBot Web API version."""

    fallback = {} if default is None else default
    if MODERN_WEB_API:
        return await request.json(default=fallback)
    return await request.get_json(silent=True) or fallback


def query_value(
    key: str,
    default: Any = None,
    value_type: Callable[[Any], Any] | None = None,
) -> Any:
    """Read one query value from the current AstrBot Web API version."""

    if MODERN_WEB_API:
        if value_type is None:
            return request.query.get(key, default)
        return request.query.get(key, default, type=value_type)
    if value_type is None:
        return request.args.get(key, default)
    return request.args.get(key, default, type=value_type)
