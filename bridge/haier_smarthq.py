"""
SmartHQ client using the current gehomesdk WebSocket API (v2026+).
ERD codes and enum mappings confirmed from live device output.
"""
import asyncio
import logging
import aiohttp
from gehomesdk import (
    GeWebsocketClient,
    ErdCode,
    EVENT_ADD_APPLIANCE,
    EVENT_APPLIANCE_STATE_CHANGE,
)
from gehomesdk.erd.values.ac.common_enums import ErdAcOperationMode, ErdAcFanSetting
from gehomesdk.erd.values.common.erd_on_off import ErdOnOff

log = logging.getLogger(__name__)

# Mode: string UI value → ErdAcOperationMode enum
_MODE_TO_ERD = {
    "cool": ErdAcOperationMode.COOL,
    "fan":  ErdAcOperationMode.FAN_ONLY,
    "eco":  ErdAcOperationMode.ENERGY_SAVER,
}
_MODE_FROM_ERD = {v: k for k, v in _MODE_TO_ERD.items()}

# Fan: string UI value → ErdAcFanSetting enum
_FAN_TO_ERD = {
    "auto":   ErdAcFanSetting.AUTO,
    "low":    ErdAcFanSetting.LOW,
    "medium": ErdAcFanSetting.MED,
    "high":   ErdAcFanSetting.HIGH,
}
_FAN_FROM_ERD = {v: k for k, v in _FAN_TO_ERD.items()}


class SmartHQClient:
    def __init__(self, username: str, password: str, device_id: str, region: str = "US"):
        self._username = username
        self._password = password
        self._device_id = device_id.upper()
        self._region = region
        self._client: GeWebsocketClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._devices: list[dict] = []
        self._connected = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_good_temp: int | None = None

    async def start(self):
        self._session = aiohttp.ClientSession()
        self._client = GeWebsocketClient(self._username, self._password, self._region)
        self._client.add_event_handler(EVENT_ADD_APPLIANCE, self._on_add_appliance)
        self._client.add_event_handler(EVENT_APPLIANCE_STATE_CHANGE, self._on_state_change)
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10)
        except asyncio.TimeoutError:
            log.warning("SmartHQ connection timed out — continuing in background")

    async def stop(self):
        """Shut everything down within a hard budget.

        gehomesdk's disconnect() can hang on a half-closed websocket after the
        run-task is cancelled, so every step is bounded by a timeout — we'd
        rather drop a TCP connection than leave uvicorn stuck on
        'Waiting for application shutdown'."""
        self._stopping = True

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        if self._client:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=3)
            except (asyncio.TimeoutError, Exception):
                log.debug("SmartHQ disconnect timed out or errored — continuing")

        if self._session and not self._session.closed:
            try:
                await asyncio.wait_for(self._session.close(), timeout=2)
            except (asyncio.TimeoutError, Exception):
                log.debug("aiohttp session close timed out — continuing")

    async def _run(self):
        while not self._stopping:
            try:
                await self._client.async_get_credentials_and_run(self._session)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._stopping:
                    break
                log.error("SmartHQ disconnected: %s — retrying in 15s", e)
                try:
                    await asyncio.sleep(15)
                except asyncio.CancelledError:
                    break

    async def _on_add_appliance(self, appliance):
        mac = str(appliance.mac_addr)
        name = str(getattr(appliance, "name", "") or mac)
        if not any(d["id"] == mac for d in self._devices):
            self._devices.append({"id": mac, "name": name,
                                   "type": str(getattr(appliance, "appliance_type", ""))})
            log.info("Discovered device: %s  name=%s", mac, name)
        self._connected.set()

    async def _on_state_change(self, data):
        try:
            appliance, state_changes = data
            log.debug("State change %s: %s", appliance.mac_addr, state_changes)
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────

    def list_devices(self) -> list[dict]:
        return self._devices

    def _appliance(self):
        if self._client is None:
            raise RuntimeError("Client not started")
        a = self._client.appliances.get(self._device_id)
        if a is None:
            ids = list(self._client.appliances.keys())
            raise RuntimeError(
                f"Device '{self._device_id}' not found. "
                f"Available: {ids or '(still connecting — wait a moment and retry)'}"
            )
        return a

    def _get(self, appliance, erd_code):
        try:
            return appliance.get_erd_value(erd_code)
        except KeyError:
            return None

    def get_status(self) -> dict:
        a = self._appliance()

        power_val = self._get(a, ErdCode.AC_POWER_STATUS)
        mode_val  = self._get(a, ErdCode.AC_OPERATION_MODE)
        temp_val  = self._get(a, ErdCode.AC_TARGET_TEMPERATURE)
        fan_val   = self._get(a, ErdCode.AC_FAN_SETTING)
        ambient   = self._get(a, ErdCode.AC_AMBIENT_TEMPERATURE)

        # Sanitize target temp — SmartHQ occasionally returns junk (e.g. 18xxx)
        # after long idle on fan mode. Clamp to a sane AC range; fall back to
        # last known good value, then 68.
        temp_out: int
        if temp_val is not None:
            try:
                t = int(temp_val)
            except (TypeError, ValueError):
                t = None
            if t is not None and 50 <= t <= 99:
                self._last_good_temp = t
                temp_out = t
            else:
                log.warning("Discarding bad target temp from device: %r", temp_val)
                temp_out = self._last_good_temp if self._last_good_temp is not None else 68
        else:
            temp_out = self._last_good_temp if self._last_good_temp is not None else 68

        amb_out: int | None
        if ambient is not None:
            try:
                a_i = int(ambient)
                amb_out = a_i if 0 <= a_i <= 150 else None
            except (TypeError, ValueError):
                amb_out = None
        else:
            amb_out = None

        return {
            "power":   "on"  if isinstance(power_val, ErdOnOff) and power_val == ErdOnOff.ON else "off",
            "mode":    _MODE_FROM_ERD.get(mode_val, "cool"),
            "temp":    temp_out,
            "fan":     _FAN_FROM_ERD.get(fan_val, "auto"),
            "ambient": amb_out,
            "unit":    "F",
        }

    async def request_refresh(self) -> bool:
        """Ask the appliance to re-send its ERD values. Returns True on success."""
        try:
            a = self._appliance()
        except RuntimeError:
            return False
        try:
            await a.async_request_update()
            return True
        except Exception as e:
            log.debug("request_refresh failed: %s", e)
            return False

    async def control(self, power: str | None, mode: str | None,
                      temp: int | None, fan: str | None) -> dict:
        a = self._appliance()
        if power is not None:
            await a.async_set_erd_value(ErdCode.AC_POWER_STATUS,
                                        ErdOnOff.ON if power == "on" else ErdOnOff.OFF)
        if mode is not None and mode in _MODE_TO_ERD:
            await a.async_set_erd_value(ErdCode.AC_OPERATION_MODE, _MODE_TO_ERD[mode])
        if temp is not None:
            await a.async_set_erd_value(ErdCode.AC_TARGET_TEMPERATURE, temp)
        if fan is not None and fan in _FAN_TO_ERD:
            await a.async_set_erd_value(ErdCode.AC_FAN_SETTING, _FAN_TO_ERD[fan])
        return {"ok": True}
