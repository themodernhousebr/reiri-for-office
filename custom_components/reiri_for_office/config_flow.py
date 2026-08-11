"""Config flow for Reiri for Office."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback

from .const import (
    CONF_SLAVE_POLICY,
    DEFAULT_PORT,
    DEFAULT_SLAVE_POLICY,
    DOMAIN,
    SLAVE_POLICY_ALL_REPORTED,
    SLAVE_POLICY_CONSERVATIVE,
)
from .reiri_client import ReiriAuthError, ReiriClient, ReiriConnectionError


class ReiriConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = ReiriClient(**user_input)
            try:
                await client.async_connect_once()
            except ReiriAuthError:
                errors["base"] = "invalid_auth"
            except (ReiriConnectionError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # Home Assistant flow must not expose protocol details
                errors["base"] = "unknown"
            else:
                await client.async_close()
                await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Reiri DCPF01 ({user_input[CONF_HOST]})", data=user_input
                )
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ReiriOptionsFlow()


class ReiriOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_SLAVE_POLICY, DEFAULT_SLAVE_POLICY)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLAVE_POLICY, default=current): vol.In(
                        [SLAVE_POLICY_CONSERVATIVE, SLAVE_POLICY_ALL_REPORTED]
                    )
                }
            ),
        )
