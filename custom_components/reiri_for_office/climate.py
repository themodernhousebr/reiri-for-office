"""Climate entities for Reiri for Office."""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SLAVE_POLICY,
    DEFAULT_SLAVE_POLICY,
    DOMAIN,
    HA_TO_MODE,
    MODE_TO_HA,
    SLAVE_POLICY_ALL_REPORTED,
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        ReiriClimate(coordinator, entry, point_id) for point_id in coordinator.data
    )


class ReiriClimate(CoordinatorEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator, entry, point_id):
        super().__init__(coordinator)
        self.entry, self.point_id = entry, point_id
        point = coordinator.data[point_id]
        self._attr_unique_id = f"{entry.unique_id}_{point_id}"
        self._attr_name = point.get("name") or point_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.unique_id}_{point_id}")},
            name=self._attr_name,
            manufacturer="Daikin / Reiri",
            model="DCPF01 AC point",
            via_device=(DOMAIN, entry.unique_id),
        )

    @property
    def point(self):
        return self.coordinator.data.get(self.point_id, {})

    @property
    def supported_features(self):
        features = self._attr_supported_features
        horizontal_cap = self.point.get("flap2_cap", {})
        if horizontal_cap.get("D", 0) or horizontal_cap.get("S"):
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def available(self):
        return self.coordinator.client.connected and self.point.get("comm_stat") is True

    @property
    def current_temperature(self):
        return self.point.get("temp")

    @property
    def target_temperature(self):
        return self.point.get("hsp") if self.point.get("mode") == "H" else self.point.get("csp")

    @property
    def min_temp(self):
        value = self.point.get("hsp_range") if self.point.get("mode") == "H" else self.point.get("csp_range")
        return value[0] if isinstance(value, list) and len(value) == 2 else 16

    @property
    def max_temp(self):
        value = self.point.get("hsp_range") if self.point.get("mode") == "H" else self.point.get("csp_range")
        return value[1] if isinstance(value, list) and len(value) == 2 else 32

    @property
    def target_temperature_step(self):
        return self.point.get("sp_step", 0.5)

    @property
    def hvac_mode(self):
        if self.point.get("stat") != "on":
            return HVACMode.OFF
        return MODE_TO_HA.get(self.point.get("mode") or self.point.get("actual_mode"), HVACMode.FAN_ONLY)

    @property
    def hvac_modes(self):
        modes = [HVACMode.OFF]
        advertised = [MODE_TO_HA[k] for k, enabled in self.point.get("mode_cap", {}).items() if enabled and k in MODE_TO_HA]
        if self.point.get("ch_master") or self.entry.options.get(CONF_SLAVE_POLICY, DEFAULT_SLAVE_POLICY) == SLAVE_POLICY_ALL_REPORTED:
            return modes + advertised
        master = next((p for p in self.coordinator.data.values() if p.get("ch_master")), None)
        master_mode = (master or {}).get("mode")
        allowed = {"C": [HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY], "H": [HVACMode.HEAT, HVACMode.FAN_ONLY], "F": [HVACMode.FAN_ONLY]}.get(master_mode, [HVACMode.FAN_ONLY])
        return modes + [mode for mode in allowed if mode in advertised]

    @property
    def fan_mode(self):
        return str(self.point.get("fanstep"))

    @property
    def fan_modes(self):
        cap = self.point.get("fanstep_cap", {})
        result = ["A"] if cap.get("A") else []
        steps = cap.get("S", 0)

        # Confirmado presencialmente no DCPF01:
        # S=2 usa L (Low) e H (High), e não valores numéricos.
        if steps == 2:
            result.extend(("L", "H"))
        elif steps == 5:
            result.extend(str(value) for value in range(1, 6))
        elif isinstance(steps, int):
            # Mantém o comportamento anterior para capacidades ainda não
            # validadas presencialmente, como S=5.
            result.extend(str(value) for value in range(1, steps + 1))

        return result or [self.fan_mode]

    @property
    def swing_mode(self):
        return str(self.point.get("flap"))

    @property
    def swing_modes(self):
        cap = self.point.get("flap_cap", {})
        directions = cap.get("D", 0)
        values = [str(value) for value in range(directions)] if isinstance(directions, int) else []
        if cap.get("S"):
            values.append("S")
        return values or [self.swing_mode]

    @property
    def swing_horizontal_mode(self):
        value = self.point.get("flap2")
        return str(value) if value is not None else None

    @property
    def swing_horizontal_modes(self):
        cap = self.point.get("flap2_cap", {})
        directions = cap.get("D", 0)
        values = (
            [str(value) for value in range(directions)]
            if isinstance(directions, int)
            else []
        )
        if cap.get("S"):
            values.append("S")
        return values or None

    @property
    def extra_state_attributes(self):
        return {key: self.point.get(key) for key in ("stat", "mode", "actual_mode", "comm_stat", "ch_master", "mode_cap", "fanstep_cap", "flap_cap", "flap2", "flap2_cap")}

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        if hvac_mode not in self.hvac_modes:
            raise ValueError("Modo incompatível com a política atual da unidade slave")
        await self.coordinator.client.async_operate(self.point_id, "mode", HA_TO_MODE[hvac_mode])
        if self.point.get("stat") != "on":
            await self.coordinator.client.async_operate(self.point_id, "stat", "on")

    async def async_turn_on(self):
        await self.coordinator.client.async_operate(self.point_id, "stat", "on")

    async def async_turn_off(self):
        await self.coordinator.client.async_operate(self.point_id, "stat", "off")

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE in kwargs:
            await self.coordinator.client.async_operate(self.point_id, "sp", kwargs[ATTR_TEMPERATURE])

    async def async_set_fan_mode(self, fan_mode):
        value = fan_mode
        if self.point.get("fanstep_cap", {}).get("S") == 5 and str(fan_mode).isdigit():
            value = int(fan_mode)
        await self.coordinator.client.async_operate(
            self.point_id, "fanstep", value
        )

    async def async_set_swing_mode(self, swing_mode):
        value = int(swing_mode) if str(swing_mode).isdigit() else swing_mode
        await self.coordinator.client.async_operate(self.point_id, "flap", value)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode):
        value = (
            int(swing_horizontal_mode)
            if str(swing_horizontal_mode).isdigit()
            else swing_horizontal_mode
        )
        await self.coordinator.client.async_operate(self.point_id, "flap2", value)
