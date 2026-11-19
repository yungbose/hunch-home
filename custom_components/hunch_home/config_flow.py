"""Settings for Hunch Home: the Hunch server's address, its shared secret (optional), the timeout, whether Home
Assistant answers first, and Locks and doors (experimental, on by default). Options (Configure): the local
record, on by default, and clearing it; and the choice to share daily counts, which appears only once sharing
has somewhere to send them (`const.SHARE_ENDPOINT`). Its wording names the privacy notice
(`const.PRIVACY_NOTICE`) and the contact (`const.DELETION_CONTACT`). Turning it off reloads the entry like any
option change, and the glue then deletes what was sent (`share.settle`, `localrecord.LocalRecord`).

No account or token is needed here. The model runs in the Hunch Home server (`const.SERVER_PAGE`), not in Home
Assistant, and the server carries it.

The shared secret is a password field. Probatio's `Secret` marker keeps it out of validation errors; it is
stored in the config entry's data, sent only as the server's bearer token, never logged and never put in
diagnostics. A server that turns the secret away (HTTP 401) starts the reauthentication flow below, which says
whether the secret is missing or wrong. Reconfigure shows the stored secret masked; clearing the field removes
it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_HA_FIRST,
    CONF_LOCKS_AND_DOORS,
    CONF_RECORD,
    CONF_SECRET,
    CONF_SHARE_COUNTS,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_HA_FIRST,
    DEFAULT_LOCKS_AND_DOORS,
    DEFAULT_RECORD,
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    DELETION_CONTACT,
    DOMAIN,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    NAME,
    PRIVACY_NOTICE,
    SERVER_PAGE,
    SHARE_ENDPOINT,
)
from .hunch_logic.client import clean_secret
from .hunch_logic.settings import option_fields, options, settings
from .hunch_logic.share import offered

SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
SCHEMA = probatio.Schema({
    probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
    probatio.Optional(probatio.Secret(CONF_SECRET)): SECRET,
    probatio.Required(CONF_TIMEOUT): NumberSelector(NumberSelectorConfig(
        min=MIN_TIMEOUT, max=MAX_TIMEOUT, step=0.1, unit_of_measurement="s", mode=NumberSelectorMode.BOX)),
    probatio.Required(CONF_HA_FIRST): BooleanSelector(),
    probatio.Required(CONF_LOCKS_AND_DOORS): BooleanSelector(),
})
DEFAULTS = {CONF_URL: DEFAULT_URL, CONF_TIMEOUT: DEFAULT_TIMEOUT, CONF_HA_FIRST: DEFAULT_HA_FIRST,
            CONF_LOCKS_AND_DOORS: DEFAULT_LOCKS_AND_DOORS}
REAUTH_SCHEMA = probatio.Schema({probatio.Required(probatio.Secret(CONF_SECRET)): SECRET})
OPTION_DEFAULTS = {CONF_RECORD: DEFAULT_RECORD, CONF_SHARE_COUNTS: False}


class HunchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Hunch server as a conversation agent."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HunchOptionsFlow:
        return HunchOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = settings(user_input, MIN_TIMEOUT, MAX_TIMEOUT)
            if not errors:
                await self.async_set_unique_id(data[CONF_URL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=data)
        return self.async_show_form(
            step_id="user", errors=errors, description_placeholders={"server_page": SERVER_PAGE},
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input or DEFAULTS))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = settings(user_input, MIN_TIMEOUT, MAX_TIMEOUT)
            if not errors:
                existing = await self.async_set_unique_id(data[CONF_URL])
                if existing is not None and existing.entry_id != entry.entry_id:
                    self._abort_if_unique_id_configured()
                # The whole data is replaced, so a secret cleared in the form is removed.
                kept = {key: value for key, value in entry.data.items() if key != CONF_SECRET}
                return self.async_update_reload_and_abort(entry, unique_id=data[CONF_URL], data=kept | data)
        return self.async_show_form(
            step_id="reconfigure", errors=errors,
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input or {**DEFAULTS, **entry.data}))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The server turned the request away (HTTP 401): say whether the secret is missing or wrong."""
        if entry_data.get(CONF_SECRET):
            return await self.async_step_reauth_wrong()
        return await self.async_step_reauth_missing()

    async def async_step_reauth_missing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._reauth("reauth_missing", user_input)

    async def async_step_reauth_wrong(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._reauth("reauth_wrong", user_input)

    def _reauth(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            secret = clean_secret(user_input.get(CONF_SECRET))
            if secret:
                return self.async_update_reload_and_abort(self._get_reauth_entry(),
                                                          data_updates={CONF_SECRET: secret})
            errors[CONF_SECRET] = "invalid_secret" if secret is None else "secret_required"
        return self.async_show_form(step_id=step_id, data_schema=REAUTH_SCHEMA, errors=errors)


class HunchOptionsFlow(OptionsFlowWithReload):
    """Configure: the local record, clearing it, and (once it exists) the choice to share daily counts."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Two names for the settings step, so that while sharing is not offered its wording appears nowhere.
        settings_step = "record_and_sharing" if offered(SHARE_ENDPOINT) else "record"
        return self.async_show_menu(step_id="init", menu_options=[settings_step, "clear_record"])

    async def async_step_record(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._settings("record", user_input)

    async def async_step_record_and_sharing(self, user_input: dict[str, Any] | None = None
                                            ) -> ConfigFlowResult:
        return self._settings("record_and_sharing", user_input)

    def _settings(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        sharing = offered(SHARE_ENDPOINT)
        if (step_id == "record_and_sharing") != sharing:
            return self.async_abort(reason="not_offered")
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen, errors = options(user_input, sharing)
            if not errors:
                return self.async_create_entry(data=chosen)
        schema = probatio.Schema({probatio.Required(name): BooleanSelector() for name in option_fields(sharing)})
        suggested = {**OPTION_DEFAULTS, **self.config_entry.options}
        return self.async_show_form(step_id=step_id, errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(schema, suggested),
                                    description_placeholders=({"contact": DELETION_CONTACT, "notice": PRIVACY_NOTICE}
                                                              if sharing else None))

    async def async_step_clear_record(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="clear_record", data_schema=probatio.Schema({}))
        local = getattr(self.config_entry, "runtime_data", None)
        if local is None:
            return self.async_abort(reason="not_loaded")
        await local.async_clear()
        # The options are unchanged, so nothing reloads.
        return self.async_create_entry(data=dict(self.config_entry.options))
