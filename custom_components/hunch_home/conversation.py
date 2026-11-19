"""Hunch Home as a conversation agent (glue only; every decision is in `hunch_logic`).

For each sentence:

1. If the setting is on (it is by default), Home Assistant's own exact sentences answer first.
2. Otherwise the sentence goes to the Hunch server, which returns the two answers.
3. `hunch_logic.decide` turns them into an intent with slots, or hands the sentence back.
4. A plan is fired as a built-in intent. If Home Assistant cannot carry it out, the sentence is handed back.
5. Handing back means giving the sentence to Home Assistant's own agent, which answers as if the plug-in were
   not there. A server that is down or slow is handed back the same way, with one line in the log.
6. Each sentence and what became of it goes into the local record (`localrecord.py`, the entry's
   `runtime_data`), which never leaves this Home Assistant; a carried-out plan is then watched for 30 seconds
   in case it is undone. A server that turns the shared secret away (HTTP 401) also starts Home Assistant's
   reauthentication flow, which tells the user the secret is missing or wrong.
"""

from __future__ import annotations

import logging
from typing import Literal

import aiohttp
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_HA_FIRST,
    CONF_LOCKS_AND_DOORS,
    CONF_SECRET,
    CONF_TIMEOUT,
    CONF_URL,
    DEFAULT_HA_FIRST,
    DEFAULT_LOCKS_AND_DOORS,
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    DOMAIN,
    LANGUAGES,
    NAME,
)
from .homeview import async_device_area, async_home_view
from .hunch_logic import client, speech
from .hunch_logic.decide import Plan, decide, response_problem

_LOGGER = logging.getLogger(__name__)


class _NoRecord:
    """Stands in for the local record when the entry has none (only in offline tests): does nothing."""

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: {}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([HunchConversationEntity(entry)])


class HunchConversationEntity(conversation.ConversationEntity):
    """Asks the Hunch server, then carries out the answer or hands the sentence back."""

    _attr_name = NAME  # the name the voice assistant settings list
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=NAME,
                                            manufacturer="xevall", model="Hunch Home",
                                            entry_type=DeviceEntryType.SERVICE)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return LANGUAGES

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        record = getattr(self.entry, "runtime_data", None) or _NoRecord()
        if self.entry.data.get(CONF_HA_FIRST, DEFAULT_HA_FIRST):
            handled = await self._async_exact_sentences(user_input, chat_log)
            if handled is not None:
                record.note_home_assistant_first(user_input.text)
                return self._result(handled, user_input, chat_log)

        answer = await client.ask(self._async_post, self.entry.data.get(CONF_URL, DEFAULT_URL), user_input.text,
                                  float(self.entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
                                  self.entry.data.get(CONF_SECRET) or "")
        if isinstance(answer, client.Answer):
            _LOGGER.debug("Hunch: %r -> %s / %s (similarity %s, label %s, %s ms)", user_input.text,
                          answer.action, answer.kind, answer.similarity, answer.label, answer.ms)
        elif answer.reason == client.REFUSED:
            # The secret is missing or wrong: Home Assistant asks the user for it (once, however many sentences).
            self.entry.async_start_reauth(self.hass)
        area = async_device_area(self.hass, user_input.device_id, user_input.satellite_id)
        plan = decide(user_input.text, answer, async_home_view(self.hass), area,
                      locks_and_doors=self.entry.data.get(CONF_LOCKS_AND_DOORS, DEFAULT_LOCKS_AND_DOORS) is True)
        if isinstance(plan, client.HandBack):
            record.note_hand_back(user_input.text, answer, plan)
            return await self._async_hand_back(plan, user_input, chat_log)

        before = record.before(plan, user_input.context)
        response = await self._async_fire(plan, user_input, area)
        if response is None:
            record.not_carried_out(user_input.context)
            why = client.HandBack("not carried out", plan.intent)
            record.note_hand_back(user_input.text, answer, why, plan, error=True)
            return await self._async_hand_back(why, user_input, chat_log)
        record.note_carried_out(user_input.text, answer, plan, user_input.context, before)
        return self._result(response, user_input, chat_log)

    async def _async_post(self, request: client.Request, timeout: float) -> tuple[int, bytes]:
        session = async_get_clientsession(self.hass)
        async with session.post(request.url, data=request.body, headers=request.headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as reply:
            return reply.status, await reply.read()

    async def _async_exact_sentences(self, user_input: conversation.ConversationInput,
                                     chat_log: conversation.ChatLog) -> intent.IntentResponse | None:
        """Home Assistant's own sentence triggers and exact intent sentences, carried out if one matches."""
        text = await conversation.async_handle_sentence_triggers(self.hass, user_input, chat_log)
        if text is not None:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech(text)
            return response
        return await conversation.async_handle_intents(self.hass, user_input, chat_log)

    async def _async_fire(self, plan: Plan, user_input: conversation.ConversationInput,
                          area: str | None) -> intent.IntentResponse | None:
        """Carry out a plan as a built-in intent; None when Home Assistant could not."""
        combined = None
        # Usually one intent, or one per device found; a step in brightness may mix HassLightSet with on and
        # off calls.
        for intent_name, slots in plan.ha_calls():
            if area is not None and not plan.by_entity:
                slots["preferred_area_id"] = {"value": area}
            try:
                response = await intent.async_handle(
                    self.hass, DOMAIN, intent_name, slots, user_input.text, user_input.context,
                    user_input.language,
                    assistant=conversation.DOMAIN, device_id=user_input.device_id,
                    satellite_id=user_input.satellite_id, conversation_agent_id=user_input.agent_id)
            except intent.IntentError as error:
                _LOGGER.debug("Hunch: %s %s not carried out: %s", intent_name, slots, error)
                return None
            if response_problem(response.response_type.value, bool(response.failed_results)) is not None:
                _LOGGER.debug("Hunch: %s %s gave an error: %s", intent_name, slots, response.error_code)
                return None
            if combined is None:
                combined = response
            else:
                combined.success_results.extend(response.success_results)
                combined.matched_states.extend(response.matched_states)
                combined.unmatched_states.extend(response.unmatched_states)
        if combined is not None and (plan.by_entity or not combined.speech.get("plain", {}).get("speech")):
            combined.async_set_speech(self._speech(plan, combined))
        return combined

    @staticmethod
    def _speech(plan: Plan, response: intent.IntentResponse) -> str:
        if plan.intent == "HassGetWeather" and response.matched_states:
            state = response.matched_states[0]
            return speech.weather(state.state, state.attributes.get("temperature"),
                                  state.attributes.get("temperature_unit"))
        names = [target.name for target in response.success_results
                 if target.type == intent.IntentResponseTargetType.ENTITY]
        names = names or [target.name for target in response.success_results]
        return speech.done(plan, names)

    async def _async_hand_back(self, why: client.HandBack, user_input: conversation.ConversationInput,
                               chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        """Give the sentence to Home Assistant's own agent, which answers as if the plug-in were not there.

        The same conversation id is passed on, so the default agent works in this turn's chat log."""
        log = _LOGGER.warning if why.from_server else _LOGGER.debug
        log("Hunch: handed back to Home Assistant (%s%s)", why.reason, f": {why.detail}" if why.detail else "")
        return await conversation.async_converse(
            self.hass, user_input.text, chat_log.conversation_id, user_input.context, user_input.language,
            agent_id=conversation.HOME_ASSISTANT_AGENT, device_id=user_input.device_id,
            satellite_id=user_input.satellite_id, extra_system_prompt=user_input.extra_system_prompt)

    @staticmethod
    def _result(response: intent.IntentResponse, user_input: conversation.ConversationInput,
                chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(
            agent_id=user_input.agent_id, content=response.speech.get("plain", {}).get("speech", "")))
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
