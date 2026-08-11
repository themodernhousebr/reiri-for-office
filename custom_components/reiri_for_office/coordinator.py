"""Coordinator for Reiri for Office."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .reiri_client import ReiriClient, ReiriError

_LOGGER = logging.getLogger(__name__)


class ReiriCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.entry = entry
        self.client = ReiriClient(
            host=entry.data[CONF_HOST],
            port=entry.data[CONF_PORT],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            on_update=self._handle_push,
            on_connection=self._handle_connection,
        )

    async def _async_update_data(self) -> dict:
        try:
            if not self.client.connected:
                await self.client.async_start()
            return await self.client.async_refresh_points()
        except ReiriError as err:
            raise UpdateFailed(str(err)) from err

    def _handle_push(self, points: dict) -> None:
        self.async_set_updated_data(points)

    def _handle_connection(self, connected: bool) -> None:
        if not connected:
            self.async_update_listeners()

    async def async_shutdown(self) -> None:
        await self.client.async_close()
