"""Interactive terminal (PTY) bridge: browser WebSocket <-> `docker exec -it` stream.

Docker's `/exec/{id}/start` with `Tty: true` upgrades the HTTP connection to a raw
bidirectional byte stream. httpx can't hijack connections, so we speak HTTP/1.1 over
an asyncio socket ourselves (unix socket or TCP).
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import shlex
import ssl
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import WebSocket, WebSocketDisconnect

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.security import decrypt_secrets
from gitseed_core.services.compute_runtime import (
    _docker_client_for_backend,
    _ws_encode_frame,
    _ws_read_frame,
    is_k8s_backend,
    k8s_open_exec_ws,
)
from gitseed_core.services.errors import Invalid

logger = logging.getLogger("uvicorn.error")


class _K8sPtyWriter:
    """Adapter around an asyncio.StreamWriter that wraps outgoing PTY stdin bytes in K8s channel 0 frames."""

    def __init__(self, raw_writer: asyncio.StreamWriter, pump_task: asyncio.Task[None]):
        self._raw = raw_writer
        self._pump_task = pump_task

    def write(self, data: bytes) -> None:
        if data:
            self._raw.write(_ws_encode_frame(b"\x00" + data))

    async def drain(self) -> None:
        await self._raw.drain()

    async def send_resize(self, cols: int, rows: int) -> None:
        msg = json.dumps({"Width": max(10, cols), "Height": max(2, rows)}).encode("utf-8")
        self._raw.write(_ws_encode_frame(b"\x04" + msg))
        await self._raw.drain()

    def close(self) -> None:
        self._pump_task.cancel()
        try:
            self._raw.write(_ws_encode_frame(b"", opcode=0x8))
        except Exception:
            pass
        self._raw.close()

    async def wait_closed(self) -> None:
        await self._raw.wait_closed()


_K8S_PTY_WRITERS: dict[str, _K8sPtyWriter] = {}


class ExecStream:
    def __init__(self, exec_id: str, reader: asyncio.StreamReader, writer: Any):
        self.exec_id = exec_id
        self.reader = reader
        self.writer = writer

    async def close(self) -> None:
        _K8S_PTY_WRITERS.pop(self.exec_id, None)
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


async def _open_raw_connection(backend: Backend) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    default_ep = getattr(settings, "DOCKER_HOST", "unix:///var/run/docker.sock") or "unix:///var/run/docker.sock"
    target = (backend.endpoint or default_ep).strip().rstrip("/")
    if target == "unix:///var/run/docker.sock" and default_ep != target and not Path("/var/run/docker.sock").exists():
        target = default_ep.strip().rstrip("/")
    if target.startswith("unix://"):
        reader, writer = await asyncio.open_unix_connection(  # type: ignore[attr-defined]
            target.removeprefix("unix://")
        )
        return reader, writer, "docker"
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    if target.startswith("tcp://"):
        use_tls = bool(secrets_map.get("tls_cert") or secrets_map.get("tls_ca"))
        target = ("https://" if use_tls else "http://") + target.removeprefix("tcp://")
    elif not target.startswith(("http://", "https://")):
        target = f"http://{target}"
    parts = urlsplit(target)
    is_tls = parts.scheme == "https"
    port = parts.port or (443 if is_tls else 2375)
    ssl_ctx: ssl.SSLContext | None = None
    if is_tls:
        from gitseed_core.services.compute_runtime import build_docker_ssl_context

        ssl_ctx = build_docker_ssl_context(secrets_map) or ssl.create_default_context()
    reader, writer = await asyncio.open_connection(parts.hostname, port, ssl=ssl_ctx)
    return reader, writer, parts.netloc


async def open_exec_pty(
    backend: Backend,
    container_id: str,
    cmd: list[str],
    *,
    user: str | None = None,
    env: list[str] | None = None,
    workdir: str = "/workspace",
    cols: int = 120,
    rows: int = 32,
) -> ExecStream:
    if is_k8s_backend(backend):
        env_exports = " ".join(
            f"{k}={shlex.quote(v)}"
            for item in ["TERM=xterm-256color", "COLORTERM=truecolor", *(env or [])]
            if "=" in item and item.split("=", 1)[0].isidentifier()
            for k, v in [item.split("=", 1)]
        )
        inner_cmd = cmd[0] if len(cmd) == 1 else " ".join(shlex.quote(c) for c in cmd)
        wrapped_cmd = [
            "sh",
            "-c",
            f"cd {shlex.quote(workdir)} 2>/dev/null || true; export {env_exports}; exec {inner_cmd}",
        ]
        try:
            raw_reader, raw_writer = await k8s_open_exec_ws(
                backend,
                container_id,
                wrapped_cmd,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
            )
        except Exception as exc:
            raise Invalid(f"Could not open Kubernetes terminal: {exc}") from exc

        demux_reader = asyncio.StreamReader()

        async def _demux_k8s_ws() -> None:
            try:
                while True:
                    frame = await _ws_read_frame(raw_reader)
                    if frame is None:
                        break
                    opcode, payload = frame
                    if opcode == 0x8:
                        break
                    if opcode == 0x9:
                        raw_writer.write(_ws_encode_frame(payload, opcode=0xA))
                        await raw_writer.drain()
                        continue
                    if payload and payload[0] in (1, 2) and len(payload) > 1:
                        demux_reader.feed_data(payload[1:])
            except Exception:
                pass
            finally:
                demux_reader.feed_eof()

        pump_task = asyncio.create_task(_demux_k8s_ws())
        k8s_writer = _K8sPtyWriter(raw_writer, pump_task)
        exec_id = f"k8s:{container_id}:{int(time.monotonic() * 1000)}"
        _K8S_PTY_WRITERS[exec_id] = k8s_writer
        try:
            await k8s_writer.send_resize(cols, rows)
        except Exception:
            pass
        return ExecStream(exec_id, demux_reader, k8s_writer)

    client, base_url = _docker_client_for_backend(backend)
    payload: dict[str, Any] = {
        "AttachStdin": True,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": True,
        "Cmd": cmd,
        "WorkingDir": workdir,
        "Env": ["TERM=xterm-256color", "COLORTERM=truecolor", *(env or [])],
        "ConsoleSize": [rows, cols],
    }
    if user:
        payload["User"] = user
    async with client:
        resp = await client.post(
            f"{base_url}/containers/{quote(container_id, safe='')}/exec", json=payload
        )
        if resp.status_code not in (200, 201):
            raise Invalid(f"Could not open terminal (HTTP {resp.status_code}): {resp.text[:200]}")
        exec_id = str(resp.json().get("Id") or "")
    if not exec_id:
        raise Invalid("Docker did not return an exec id")

    reader, writer, host = await _open_raw_connection(backend)
    body = json.dumps({"Detach": False, "Tty": True, "ConsoleSize": [rows, cols]}).encode()
    request = (
        f"POST /exec/{exec_id}/start HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: tcp\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    writer.write(request)
    await writer.drain()

    header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    status_line = header.split(b"\r\n", 1)[0].decode("latin-1")
    if " 101 " not in status_line and " 200 " not in status_line:
        writer.close()
        raise Invalid(f"Terminal attach failed: {status_line}")
    return ExecStream(exec_id, reader, writer)


async def resize_exec(backend: Backend, exec_id: str, cols: int, rows: int) -> None:
    if is_k8s_backend(backend) or exec_id.startswith("k8s:"):
        k8s_writer = _K8S_PTY_WRITERS.get(exec_id)
        if k8s_writer is not None:
            try:
                await k8s_writer.send_resize(cols, rows)
            except Exception:
                pass
        return

    client, base_url = _docker_client_for_backend(backend)
    try:
        async with client:
            await client.post(
                f"{base_url}/exec/{quote(exec_id, safe='')}/resize",
                params={"h": max(2, rows), "w": max(10, cols)},
            )
    except Exception:
        pass


class TranscriptWriter:
    """asciicast v2 transcript (https://docs.asciinema.org/manual/asciicast/v2/)."""

    def __init__(self, path: Path, cols: int, rows: int):
        self.path = path
        self.start = time.monotonic()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        self.fh = path.open("a", encoding="utf-8")
        if new_file:
            self.fh.write(
                json.dumps({"version": 2, "width": cols, "height": rows, "timestamp": int(time.time())})
                + "\n"
            )
        else:
            # Appending a new connection: mark it so replays show the reconnect
            self.fh.write(json.dumps([0.0, "m", "reconnected"]) + "\n")

    def output(self, data: bytes) -> None:
        text = self.decoder.decode(data)
        if text:
            self.fh.write(json.dumps([round(time.monotonic() - self.start, 3), "o", text]) + "\n")

    def close(self) -> None:
        try:
            self.fh.flush()
            self.fh.close()
        except Exception:
            pass


class LivePty:
    """A terminal process that outlives browser connections.

    The exec keeps running in the container when the user navigates away; output keeps
    flowing into a bounded scrollback buffer (and the transcript). Reconnecting replays
    the scrollback and forces a redraw, so full-screen agent TUIs resume where they were.
    Lives until the process exits (CLI quit, container stopped/deleted).
    """

    SCROLLBACK_BYTES = 512 * 1024

    def __init__(
        self,
        key: str,
        backend: Backend,
        container_id: str,
        stream: ExecStream,
        transcript: TranscriptWriter | None,
    ):
        self.key = key
        self.backend = backend
        self.container_id = container_id
        self.stream = stream
        self.transcript = transcript
        self.buffer = bytearray()
        self.clients: dict[int, asyncio.Queue[bytes | None]] = {}
        self.closed = False
        self._write_lock = asyncio.Lock()
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                chunk = await self.stream.reader.read(65536)
                if not chunk:
                    break
                if self.transcript:
                    self.transcript.output(chunk)
                self.buffer += chunk
                overflow = len(self.buffer) - self.SCROLLBACK_BYTES
                if overflow > 0:
                    del self.buffer[:overflow]
                for q in self.clients.values():
                    q.put_nowait(chunk)
        except Exception as exc:
            logger.debug("Live terminal %s ended: %s", self.key, exc)
        finally:
            self.closed = True
            if _LIVE.get(self.key) is self:
                _LIVE.pop(self.key, None)
            for q in self.clients.values():
                q.put_nowait(None)
            await self.stream.close()
            if self.transcript:
                self.transcript.close()

    def subscribe(self) -> tuple[int, asyncio.Queue[bytes | None]]:
        """Register a viewer; its queue starts with the current scrollback (no gap, no dupes)."""
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        if self.buffer:
            q.put_nowait(bytes(self.buffer))
        token = id(q)
        self.clients[token] = q
        return token, q

    def unsubscribe(self, token: int) -> None:
        self.clients.pop(token, None)

    async def write(self, data: bytes) -> None:
        if self.closed or not data:
            return
        async with self._write_lock:
            self.stream.writer.write(data)
            await self.stream.writer.drain()

    async def resize(self, cols: int, rows: int) -> None:
        await resize_exec(self.backend, self.stream.exec_id, cols, rows)

    async def terminate(self) -> None:
        await self.stream.close()
        self._task.cancel()


_LIVE: dict[str, LivePty] = {}


def get_live_pty(key: str, container_id: str) -> LivePty | None:
    live = _LIVE.get(key)
    if live is None or live.closed:
        return None
    if live.container_id != container_id:
        # Container was replaced (stopped/woken, recreated): the old process is gone.
        _LIVE.pop(key, None)
        asyncio.create_task(live.terminate())
        return None
    return live


def register_live_pty(
    key: str,
    backend: Backend,
    container_id: str,
    stream: ExecStream,
    transcript: TranscriptWriter | None,
) -> LivePty:
    previous = _LIVE.pop(key, None)
    if previous is not None and not previous.closed:
        asyncio.create_task(previous.terminate())
    live = LivePty(key, backend, container_id, stream, transcript)
    _LIVE[key] = live
    return live


async def close_live_pty(key: str) -> None:
    live = _LIVE.pop(key, None)
    if live is not None and not live.closed:
        await live.terminate()


async def attach_websocket(
    websocket: WebSocket,
    live: LivePty,
    *,
    can_write: bool,
    cols: int,
    rows: int,
    resumed: bool,
    on_activity: Callable[[], Awaitable[None]] | None = None,
    preamble: bytes | None = None,
) -> None:
    """Attach a browser to a live terminal. Disconnecting detaches; the process keeps running."""
    if preamble:
        await websocket.send_bytes(preamble)

    token, queue = live.subscribe()
    last_activity = 0.0

    async def touch() -> None:
        nonlocal last_activity
        now = time.monotonic()
        if on_activity and now - last_activity > 30:
            last_activity = now
            try:
                await on_activity()
            except Exception:
                pass

    async def pump_out() -> None:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            # Coalesce whatever is queued into one frame (big replay + live output)
            parts = [chunk]
            while not queue.empty():
                nxt = queue.get_nowait()
                if nxt is None:
                    await websocket.send_bytes(b"".join(parts))
                    return
                parts.append(nxt)
            await websocket.send_bytes(b"".join(parts))

    async def pump_in() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            raw = msg.get("text")
            if raw is None and msg.get("bytes") is not None:
                if can_write:
                    await live.write(msg["bytes"])
                    await touch()
                continue
            try:
                data = json.loads(raw or "{}")
            except json.JSONDecodeError:
                continue
            if data.get("type") == "resize":
                await live.resize(int(data.get("cols") or 120), int(data.get("rows") or 32))
            elif data.get("type") == "input" and can_write:
                await live.write(str(data.get("data") or "").encode("utf-8"))
                await touch()

    if resumed:
        # Nudge the size so full-screen TUIs get SIGWINCH and repaint the whole screen
        await live.resize(cols, max(2, rows - 1))
        await live.resize(cols, rows)

    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in())
    try:
        done, pending = await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.debug("Terminal attach ended: %s", exc)
    finally:
        live.unsubscribe(token)
        try:
            if live.closed:
                await websocket.close(code=1000, reason="Session exited")
            else:
                await websocket.close()
        except Exception:
            pass
