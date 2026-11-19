"""Hunch Home for Home Assistant: a conversation agent that asks a local Hunch server what a sentence means.

Glue only: the decisions are in `hunch_logic`, which imports nothing from Home Assistant.

Each config entry keeps a local record (`localrecord.LocalRecord`, its `runtime_data`) that never leaves this
Home Assistant. Two admin-only actions read and clear it: `hunch_home.show_record` returns the whole record,
sentences included (Developer tools > Actions shows it, ready to copy), and `hunch_home.clear_record` deletes
it. The diagnostics download leaves the sentences out (`diagnostics.py`).
"""

from __future__ import annotations

import probatio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .localrecord import LocalRecord

PLATFORMS = [Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CONF_CONFIG_ENTRY_ID = "config_entry_id"
SERVICE_SCHEMA = probatio.Schema({probatio.Optional(CONF_CONFIG_ENTRY_ID): str})

type HunchConfigEntry = ConfigEntry[LocalRecord]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the two record actions, once for the integration."""

    def record_for(call: ServiceCall) -> LocalRecord:
        entry = service.async_get_config_entry(hass, DOMAIN, call.data.get(CONF_CONFIG_ENTRY_ID))
        return entry.runtime_data

    async def show_record(call: ServiceCall) -> ServiceResponse:
        return record_for(call).show()

    async def clear_record(call: ServiceCall) -> None:
        await record_for(call).async_clear()

    service.async_register_admin_service(hass, DOMAIN, "show_record", show_record, SERVICE_SCHEMA,
                                         SupportsResponse.ONLY)
    service.async_register_admin_service(hass, DOMAIN, "clear_record", clear_record, SERVICE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    """Load the record, then set up the conversation agent for one Hunch server."""
    record = LocalRecord(hass, entry)
    await record.async_load()
    entry.runtime_data = record
    record.start_sharing()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    """Remove the conversation agent, and save the record."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> None:
    """Deleting the integration's entry deletes its record too."""
    await LocalRecord.async_remove(hass, entry.entry_id)
