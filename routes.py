import re

ROUTES = []


def route(method, pattern):
    compiled = re.compile(pattern + r"$")

    def decorator(fn):
        ROUTES.append((method, compiled, fn))
        return fn

    return decorator


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message
