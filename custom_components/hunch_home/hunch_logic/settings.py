"""Validate settings and options without importing Home Assistant."""

import math

from .client import clean_secret, clean_url

# The options (Configure in Home Assistant), apart from the settings made at set-up.
RECORD = "record"
SHARE_COUNTS = "share_counts"


def settings(user_input: dict, minimum: float = 0.2, maximum: float = 30.0) -> tuple[dict, dict]:
    """Tidy the address and accept only a finite timeout and boolean preferences.

    "locks_and_doors" (experimental, on by default) is kept only when given; missing means the default.
    "secret" (the server's shared secret) is kept only when given and not empty: Home Assistant leaves an
    empty optional field out of the form's answer, so a missing or empty secret means none."""
    url = clean_url(user_input.get("url"))
    errors = {}
    if url is None:
        errors["url"] = "invalid_url"
    timeout = user_input.get("timeout")
    try:
        if isinstance(timeout, bool):
            raise TypeError
        timeout = float(timeout)
        if not math.isfinite(timeout) or not minimum <= timeout <= maximum:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        errors["timeout"] = "invalid_timeout"
    first = user_input.get("ha_first", True)
    if not isinstance(first, bool):
        errors["ha_first"] = "invalid_preference"
    data = {"url": url, "timeout": timeout, "ha_first": first}
    if "locks_and_doors" in user_input:
        data["locks_and_doors"] = user_input["locks_and_doors"]
        if not isinstance(data["locks_and_doors"], bool):
            errors["locks_and_doors"] = "invalid_switch"
    if "secret" in user_input:
        secret = clean_secret(user_input["secret"])
        if secret is None:
            errors["secret"] = "invalid_secret"
        elif secret:
            data["secret"] = secret
    if errors:
        return {}, errors
    return data, {}


def option_fields(sharing_offered: bool) -> tuple[str, ...]:
    """The options the form shows. Sharing counts appears only once there is somewhere to send them."""
    return (RECORD, SHARE_COUNTS) if sharing_offered else (RECORD,)


def options(user_input: dict, sharing_offered: bool) -> tuple[dict, dict]:
    """The record switch (on unless switched off) and the choice to share counts (off unless chosen).

    While sharing is not offered, the choice is stored as off whatever the form says, so a stale or crafted
    answer can never switch it on."""
    errors = {}
    record = user_input.get(RECORD, True)
    if not isinstance(record, bool):
        errors[RECORD] = "invalid_switch"
    share = user_input.get(SHARE_COUNTS, False) if sharing_offered else False
    if not isinstance(share, bool):
        errors[SHARE_COUNTS] = "invalid_switch"
    if errors:
        return {}, errors
    return {RECORD: record, SHARE_COUNTS: share}, {}
