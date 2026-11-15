"""Request-body helpers and the error type that routes and modules raise. No routes here.

"Now" and "today" are asked for here, through the module (`common.now()`, `common.today()`, never
`from common import today`), so the tests' `clock` and `freeze_today` fixtures can move them."""
import re
import time
from datetime import date

from flask import request


class ApiError(Exception):
    """Raised anywhere while handling a request; app.py turns it into {"error": message} with this status. Extra
    keyword arguments become extra keys of the JSON body."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def json_body() -> dict:
    """The request's JSON object. Anything else (no body, bad JSON, a list, a string) is 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object.")
    return data


def text(data: dict, key: str, *, required: bool = False, max_len: int = 2000) -> str | None:
    """data[key] stripped. Missing, null or blank is None, or 400 "<key> is required." when required. Anything but a
    string is 400, and so is a string longer than max_len characters (after stripping)."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ApiError(400, f"{key} must be text.")
    value = (value or "").strip()
    if not value:
        if required:
            raise ApiError(400, f"{key} is required.")
        return None
    if len(value) > max_len:
        raise ApiError(400, f"{key} is too long (at most {max_len} characters).")
    return value


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MIN_YEAR = 1900  # a year like 0024 is a slip of the thumb on a phone, not a date in anyone's history


def parse_date(value, *, field: str, required: bool = False) -> str | None:
    """A real calendar date as YYYY-MM-DD, from a string in exactly that form (surrounding spaces are forgiven).
    None or blank is None, or 400 "<field> is required." when required. Anything else (25/02/2025, 2025-2-3,
    2025-02-30, a number, a year before 1900) is 400 "<field> must be a date like 2024-03-01."
    `field` is the name a person reads, e.g. "Start date"."""
    bad = ApiError(400, f"{field} must be a date like 2024-03-01.")
    if value is not None and not isinstance(value, str):
        raise bad
    value = (value or "").strip()
    if not value:
        if required:
            raise ApiError(400, f"{field} is required.")
        return None
    if not _DATE.fullmatch(value):  # date.fromisoformat alone would also take 20250228 and 2025-W09-5
        raise bad
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise bad from None
    if day.year < MIN_YEAR:
        raise bad
    return value


def now() -> int:
    """Seconds since the epoch. Every module asks here, so tests can move time (the `clock` fixture)."""
    return int(time.time())


def today() -> str:
    """Today as YYYY-MM-DD. Every module asks here, so tests can freeze it (the `freeze_today` fixture)."""
    return date.today().isoformat()


def multipart(view):
    """Marks a view as taking multipart/form-data instead of JSON. The mutation guard in app.py still requires
    X-Sickbay: 1 and a same-origin Origin. Put it under the route decorator, so the route registers the marked view:

        @app.post("/api/files")
        @multipart
        def upload(): ...
    """
    view.multipart = True
    return view
