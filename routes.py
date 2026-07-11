import re

ROUTES = []  # list of (method, compiled_regex, func, public)


def route(method, pattern, public=False):
    compiled = re.compile(pattern + r"$")

    def decorator(fn):
        ROUTES.append((method, compiled, fn, public))
        return fn

    return decorator


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class ResponseHelper:
    """Lets route handlers attach response headers (cookies) without owning the socket."""

    def __init__(self):
        self.extra_headers = []

    def set_cookie(self, name, value, max_age=None, http_only=True, path="/"):
        parts = [f"{name}={value}", f"Path={path}"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if http_only:
            parts.append("HttpOnly")
        parts.append("SameSite=Lax")
        self.extra_headers.append(("Set-Cookie", "; ".join(parts)))

    def clear_cookie(self, name, path="/"):
        self.extra_headers.append(("Set-Cookie", f"{name}=; Path={path}; Max-Age=0"))
