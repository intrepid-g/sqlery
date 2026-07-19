"""Standalone project configuration and task module.

This package initializes the sqlery backend when imported, ensuring the database
is ready before any operations.
"""

# Import app_config to trigger backend initialization when this package is accessed
from . import app_config  # noqa: F401
