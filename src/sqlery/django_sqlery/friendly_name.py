"""Deterministically convert a UUID into a human-friendly name.

    adj-adj-prof-7f

Same UUID always produces the same output.
Not collision-proof — output space is much smaller than UUID space.
"""
from __future__ import annotations

import uuid


ADJECTIVES = [
    "agile", "amber", "ancient", "autumn", "bold", "brave", "bright", "brisk",
    "calm", "clever", "crisp", "daring", "dawn", "deep", "eager", "fancy",
    "fast", "fierce", "firm", "frosty", "gentle", "golden", "grand", "green",
    "happy", "hardy", "honest", "icy", "iron", "keen", "kind", "lively",
    "lucky", "mighty", "misty", "modern", "noble", "odd", "plain", "proud",
    "quick", "quiet", "rapid", "red", "rich", "rocky", "royal", "rugged",
    "sharp", "silent", "silver", "smart", "solid", "spring", "steady", "stone",
    "stormy", "strong", "swift", "tall", "tough", "true", "urban", "vast",
    "vivid", "warm", "wild", "wise", "young", "zesty",
]

PROFESSIONS = [
    "builder", "carpenter", "mason", "plumber", "electrician", "welder",
    "blacksmith", "roofer", "painter", "tiler", "mechanic", "locksmith",
    "cooper", "fletcher", "tanner", "chandler", "weaver", "miller", "brewer",
    "dyer", "potter", "scribe", "smith", "cartwright", "wheelwright",
    "armorer", "farmer", "miner", "logger", "fisher", "bricklayer", "glazier",
    "ironworker", "shipwright", "architect", "planner", "forger", "maker",
    "craftsman", "artisan", "founder", "constructor",
]

HEX_LETTERS = "abcdef"


def uuid_to_friendly(value: str | uuid.UUID) -> str:
    """Convert a UUID to a deterministic human-friendly name."""
    if isinstance(value, str):
        u = uuid.UUID(value)
    else:
        u = value

    n = u.int

    adj1 = ADJECTIVES[n % len(ADJECTIVES)]
    n //= len(ADJECTIVES)

    adj2 = ADJECTIVES[n % len(ADJECTIVES)]
    n //= len(ADJECTIVES)

    prof = PROFESSIONS[n % len(PROFESSIONS)]
    n //= len(PROFESSIONS)

    digit = str(n % 10)
    n //= 10

    letter = HEX_LETTERS[n % len(HEX_LETTERS)]

    return f"{adj1}-{adj2}-{prof}-{digit}{letter}"
