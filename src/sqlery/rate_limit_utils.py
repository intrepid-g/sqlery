"""Rate limit parsing and checking utilities.

Framework-agnostic functions have been promoted to core.utils.
This module re-exports them for backward compatibility.

Rate limit format: "{count}/{unit}" where:
- count: Number of allowed executions
- unit: Time unit - s (second), m (minute), h (hour), or custom like 10s (10 seconds)

Examples:
- "60/m" = 60 per minute
- "100/s" = 100 per second
- "1/10s" = 1 per 10 seconds
- "5000/h" = 5000 per hour
"""

# Promoted to core — re-export for backward compatibility
from sqlery.core.utils import parse_rate_limit, calculate_rate_limit_seconds, get_rate_limit_description

# #CLEANUP 2026-05-14: dead code below — Remove after 2027-05-14.
# import re
# from datetime import timedelta
# # from typing import tuple  # Not needed: tuple is a builtin (Python 3.9+)
#
#
# def parse_rate_limit(rate_limit_str: str) -> tuple[int, timedelta]:
#     """Parse rate limit string into (count, time_window).
#     ...
#     """
#     # Match format: {count}/{unit} where unit can be: s, m, h, or {number}s, {number}m, {number}h
#     pattern = r"^(\d+)/(\d+)?([smh])$"
#     match = re.match(pattern, rate_limit_str)
#
#     if not match:
#         raise ValueError(
#             f"Invalid rate limit format: '{rate_limit_str}'. "
#             f"Expected format: '{{count}}/{{unit}}' where unit is s, m, h, or like 10s, 30m"
#         )
#
#     count = int(match.group(1))
#     multiplier = int(match.group(2)) if match.group(2) else 1
#     unit = match.group(3)
#
#     # Validate count and multiplier
#     if count <= 0:
#         raise ValueError(
#             f"Rate limit count must be positive, got {count}. "
#             f"Example: '60/m' allows 60 requests per minute."
#         )
#     if multiplier <= 0:
#         raise ValueError(
#             f"Time multiplier must be positive, got {multiplier}. "
#             f"Example: '1/10s' means 1 request per 10 seconds."
#         )
#
#     # Convert to timedelta
# #CLEANUP 2026-05-14: dead code below — Remove after 2027-05-14.
#     if unit == "s":
#         time_window = timedelta(seconds=multiplier)
#     elif unit == "m":
#         time_window = timedelta(minutes=multiplier)
#     elif unit == "h":
#         time_window = timedelta(hours=multiplier)
#     else:
#         raise ValueError(f"Unknown time unit: '{unit}'. Use 's', 'm', or 'h'")
#
#     return count, time_window
#
#
# def calculate_rate_limit_seconds(rate_limit_str: str) -> tuple[int, float]:
#     """Calculate rate limit as (count, seconds).
#     ...
#     """
#     count, time_window = parse_rate_limit(rate_limit_str)
#     return count, time_window.total_seconds()
#
#
# def get_rate_limit_description(rate_limit_str: str) -> str:
#     """Get human-readable description of rate limit.
#     ...
#     """
#     count, seconds = calculate_rate_limit_seconds(rate_limit_str)
#
#     # Base description
#     if seconds == 1:
#         desc = f"{count} requests per second"
#     elif seconds == 60:
#         desc = f"{count} requests per minute"
#         rate_per_second = count / 60
#         desc += f" ({rate_per_second:.2f} per second)"
#     elif seconds == 3600:
#         desc = f"{count} requests per hour"
#         rate_per_second = count / 3600
#         desc += f" ({rate_per_second:.4f} per second)"
#     else:
#         desc = f"{count} requests per {seconds:.0f} seconds"
#         rate_per_second = count / seconds
#         desc += f" ({rate_per_second:.2f} per second)"
#
#     return desc
