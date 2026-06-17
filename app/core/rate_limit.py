"""Shared slowapi limiter. Lives here so route modules can import it without
creating a circular dependency on app.main."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
