"""The diagnostics download for a Hunch Home entry (glue only; the view is `hunch_logic.record.diagnostics_view`).

Diagnostics files are made to be pasted into bug reports, often in public. So this one holds no sentence, no
hand-back detail (which may name a device or a room), no entity ID (each is cut to its domain), no server
address and no secret (only whether one is set). It does hold, under "sharing", exactly what the daily counts
would send for yesterday, whether or not sharing exists yet, and whether a deletion of what was sent is still
owed. The install identifier is not in it: the receiving end takes that identifier as the proof for deleting
or replacing a home's counts, so a pasted diagnostics file must not give it away. In its place is the redaction
mark, and beside it an "install_reference" (the start of the identifier's SHA-256), which the privacy notice
asks people to quote when they write about their counts and which cannot be turned back into the identifier.
The whole record, sentences included, is the user's alone, through the admin-only action
`hunch_home.show_record`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HunchConfigEntry
from .const import SHARE_ENDPOINT
from .hunch_logic.record import Record, diagnostics_view
from .hunch_logic.share import offered


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HunchConfigEntry) -> dict[str, Any]:
    local = getattr(entry, "runtime_data", None)
    if local is None:  # the entry is not running: its record is not loaded
        return diagnostics_view(Record(), entry.data, entry.options,
                                {"offered": offered(SHARE_ENDPOINT), "loaded": False})
    return diagnostics_view(local.record, entry.data, entry.options, local.sharing_view())
