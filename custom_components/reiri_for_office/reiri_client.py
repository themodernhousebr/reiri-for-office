"""Async local WebSocket client for Reiri DCPF01."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import json
import logging
from typing import Any

from aiohttp import ClientSession, ClientWebSocketResponse, WSMsgType
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .const import FLAP_DEBOUNCE_SECONDS

_LOGGER = logging.getLogger(__name__)


class ReiriError(Exception):
    """Base protocol error."""


class ReiriConnectionError(ReiriError):
    """Connection error."""


class ReiriAuthError(ReiriError):
    """Authentication error."""


class ReiriClient:
    def __init__(self, host, port, username, password, on_update=None, on_connection=None):
        self.host, self.port = host, int(port)
        self.username, self.password = username, password
        self.on_update: Callable[[dict], None] | None = on_update
        self.on_connection: Callable[[bool], None] | None = on_connection
        self.points: dict[str, dict[str, Any]] = {}
        self.connected = False
        self._session: ClientSession | None = None
        self._ws: ClientWebSocketResponse | None = None
        self._key: bytes | None = None
        self._reader_task: asyncio.Task | None = None
        self._runner_task: asyncio.Task | None = None
        self._closing = False
        self._send_lock = asyncio.Lock()
        self._requests: dict[str, list[asyncio.Future]] = {}
        self._confirmations: list[dict] = []

    async def async_connect_once(self) -> dict:
        await self._connect_and_login()
        points = await self._request("mplist", encrypted_marker=True, timeout=15)
        self._set_points(points)
        return self.points

    async def async_start(self) -> None:
        if self._runner_task and not self._runner_task.done():
            return
        self._closing = False
        await self.async_connect_once()
        self._runner_task = asyncio.create_task(self._reconnect_loop())

    async def _connect_and_login(self) -> None:
        await self._disconnect()
        self._session = ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                f"ws://{self.host}:{self.port}",
                origin=f"http://{self.host}",
                heartbeat=30,
                receive_timeout=90,
            )
            private_key = await asyncio.to_thread(
                rsa.generate_private_key, public_exponent=65537, key_size=2048
            )
            public_pem = private_key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.PKCS1
            ).decode("ascii")
            await self._send_packet([None, None, ["sys_info", public_pem]])
            packet = await self._receive_raw(timeout=15)
            command, data = self._normalize(packet, None)
            if command != "sys_info" or not isinstance(data, dict):
                raise ReiriConnectionError("Resposta sys_info inválida")
            self._key = await asyncio.to_thread(
                self._decrypt_common_key, private_key, data["common_key"]
            )
            await self._send_encrypted(
                "login", {"name": self.username, "passwd": self.password, "uuid": None}
            )
            while True:
                command, body = self._normalize(await self._receive_raw(timeout=15), self._key)
                if command == "login":
                    if not isinstance(body, dict) or body.get("result") != "OK":
                        raise ReiriAuthError("Login Reiri recusado")
                    break
            self.connected = True
            if self.on_connection:
                self.on_connection(True)
            self._reader_task = asyncio.create_task(self._reader())
        except Exception:
            await self._disconnect()
            raise

    async def _reconnect_loop(self) -> None:
        delay = 2
        while not self._closing:
            task = self._reader_task
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    return
                except Exception as err:
                    _LOGGER.debug("Reiri listener disconnected: %s", err)
            if self._closing:
                return
            await self._disconnect()
            await asyncio.sleep(delay)
            try:
                await self.async_connect_once()
                delay = 2
            except Exception as err:
                _LOGGER.debug("Reiri reconnect failed: %s", err)
                delay = min(delay * 2, 60)

    async def _reader(self) -> None:
        while not self._closing and self._ws is not None:
            packet = await self._receive_raw(timeout=95)
            command, body = self._normalize(packet, self._key)
            if command == "cos" and isinstance(body, dict):
                for point_id, changes in body.items():
                    if point_id in self.points and isinstance(changes, dict):
                        self.points[point_id].update(changes)
                self._resolve_cos(body)
                if self.on_update:
                    self.on_update(self.points)
            else:
                waiters = self._requests.get(command, [])
                if waiters:
                    future = waiters.pop(0)
                    if not future.done():
                        future.set_result(body)

    async def async_refresh_points(self) -> dict:
        body = await self._request("mplist", encrypted_marker=True, timeout=15)
        self._set_points(body)
        if self.on_update:
            self.on_update(self.points)
        return self.points

    def _set_points(self, body: Any) -> None:
        if not isinstance(body, dict) or body.get("result") == "no_auth":
            raise ReiriAuthError("mplist indisponível para este usuário")
        self.points = {
            point_id: point
            for point_id, point in body.items()
            if isinstance(point, dict) and point.get("type") == "Ac" and point.get("usage") == "ac"
        }

    async def async_operate(self, point_id: str, attribute: str, value: Any) -> None:
        if attribute == "flap":
            await asyncio.sleep(FLAP_DEBOUNCE_SECONDS)
        loop = asyncio.get_running_loop()
        cos_future = loop.create_future()
        confirmation = {"point": point_id, "attribute": attribute, "value": value, "future": cos_future}
        self._confirmations.append(confirmation)
        try:
            op_task = asyncio.create_task(
                self._request("op", {point_id: {attribute: value}}, timeout=15)
            )
            op_body = await op_task
            if not isinstance(op_body, dict) or op_body.get("result") != "OK":
                raise ReiriError(f"Operação recusada: {op_body!r}")
            try:
                await asyncio.wait_for(asyncio.shield(cos_future), timeout=15)
                return
            except TimeoutError:
                await self.async_refresh_points()
                actual = self.points.get(point_id, {}).get(attribute)
                if attribute == "sp":
                    point = self.points.get(point_id, {})
                    actual = point.get("hsp") if point.get("mode") == "H" else point.get("csp")
                if actual != value:
                    raise ReiriError("Operação aceita, mas não confirmada por COS ou mplist")
        finally:
            if confirmation in self._confirmations:
                self._confirmations.remove(confirmation)

    def _resolve_cos(self, body: dict) -> None:
        for item in tuple(self._confirmations):
            changes = body.get(item["point"], {})
            attr = item["attribute"]
            matched = changes.get(attr) == item["value"]
            if attr == "sp":
                matched = changes.get("csp") == item["value"] or changes.get("hsp") == item["value"]
            if matched and not item["future"].done():
                item["future"].set_result(True)

    async def _request(self, command, data=None, encrypted_marker=False, timeout=15):
        if not self.connected or not self._ws:
            raise ReiriConnectionError("DCPF01 desconectado")
        future = asyncio.get_running_loop().create_future()
        self._requests.setdefault(command, []).append(future)
        try:
            if data is not None:
                await self._send_encrypted(command, data)
            else:
                await self._send_packet(["enc" if encrypted_marker else None, None, [command]])
            return await asyncio.wait_for(future, timeout)
        finally:
            waiters = self._requests.get(command, [])
            if future in waiters:
                waiters.remove(future)

    async def _send_encrypted(self, command: str, data: Any) -> None:
        if self._key is None:
            raise ReiriConnectionError("Sessão sem common_key")
        encrypted = self._aes_encrypt(data, self._key)
        await self._send_packet(["enc", None, [command, encrypted]])

    async def _send_packet(self, packet: list) -> None:
        if self._ws is None:
            raise ReiriConnectionError("WebSocket desconectado")
        async with self._send_lock:
            await self._ws.send_str(json.dumps(packet, ensure_ascii=False, separators=(",", ":")))

    async def _receive_raw(self, timeout: float) -> list:
        if self._ws is None:
            raise ReiriConnectionError("WebSocket desconectado")
        msg = await asyncio.wait_for(self._ws.receive(), timeout)
        if msg.type != WSMsgType.TEXT:
            raise ReiriConnectionError(f"WebSocket encerrado: {msg.type}")
        return json.loads(msg.data)

    @classmethod
    def _normalize(cls, packet: list, key: bytes | None):
        if not isinstance(packet, list) or len(packet) < 3 or not isinstance(packet[2], list):
            raise ReiriError("Pacote Reiri inválido")
        command = packet[2][0]
        data = packet[2][1] if len(packet[2]) > 1 else None
        if packet[0] == "enc" and data is not None:
            if key is None:
                raise ReiriError("Pacote cifrado antes da common_key")
            data = cls._aes_decrypt(data, key)
        return command, data

    @staticmethod
    def _aes_encrypt(data: Any, key: bytes) -> str:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        padder = PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
        return (encryptor.update(padded) + encryptor.finalize()).hex()

    @staticmethod
    def _aes_decrypt(value: str, key: bytes) -> Any:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
        padded = decryptor.update(bytes.fromhex(value)) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        return json.loads((unpadder.update(padded) + unpadder.finalize()).decode())

    @staticmethod
    def _decrypt_common_key(private_key, value: str) -> bytes:
        compact = "".join(value.split())
        try:
            ciphertext = base64.b64decode(compact, validate=True)
        except Exception:
            ciphertext = bytes.fromhex(compact)
        for rsa_padding in (
            padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
            padding.PKCS1v15(),
        ):
            try:
                clear = private_key.decrypt(ciphertext, rsa_padding)
                if len(clear) == 16:
                    return clear
                text = clear.decode("ascii").strip()
                decoded = base64.b64decode(text)
                if len(decoded) == 16:
                    return decoded
                if len(text.encode()) == 16:
                    return text.encode()
            except Exception:
                continue
        raise ReiriError("Não foi possível obter common_key AES de 16 bytes")

    async def _disconnect(self) -> None:
        self.connected = False
        if self.on_connection:
            self.on_connection(False)
        current = asyncio.current_task()
        if self._reader_task and self._reader_task is not current:
            self._reader_task.cancel()
        self._reader_task = None
        if self._ws:
            await self._ws.close()
        self._ws = None
        if self._session:
            await self._session.close()
        self._session = None
        for waiters in self._requests.values():
            for future in waiters:
                if not future.done():
                    future.set_exception(ReiriConnectionError("DCPF01 desconectado"))
        self._requests.clear()

    async def async_close(self) -> None:
        self._closing = True
        if self._runner_task:
            self._runner_task.cancel()
        self._runner_task = None
        await self._disconnect()
