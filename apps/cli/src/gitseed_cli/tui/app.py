from __future__ import annotations

import codecs
import ctypes
import os
import re
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.live import Live

from gitseed_cli.client import GitSeedClient
from gitseed_cli.tui.state import TUIController, TUIState
from gitseed_cli.tui.views import render_tui_frame

try:
    from ctypes import wintypes
except ImportError:
    wintypes = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    import select
except ImportError:
    select = None  # type: ignore[assignment]

try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_SGR_MOUSE_RE = re.compile(r"^\[<(\d+);(\d+);(\d+)([Mm])$")

_SS3_KEY_MAP: dict[str, str] = {
    "A": "UP",
    "B": "DOWN",
    "C": "RIGHT",
    "D": "LEFT",
    "H": "HOME",
    "F": "END",
}

_CSI_LETTER_KEY_MAP: dict[str, str] = {
    "A": "UP",
    "B": "DOWN",
    "C": "RIGHT",
    "D": "LEFT",
    "H": "HOME",
    "F": "END",
    "Z": "SHIFT_TAB",
}

_CSI_TILDE_KEY_MAP: dict[str, str] = {
    "1": "HOME",
    "3": "DELETE",
    "4": "END",
    "5": "PGUP",
    "6": "PGDN",
    "7": "HOME",
    "8": "END",
}

_MSVC_EXTENDED_KEY_MAP: dict[str, str] = {
    "H": "UP",      # 0x48
    "P": "DOWN",    # 0x50
    "K": "LEFT",    # 0x4B
    "M": "RIGHT",   # 0x4D
    "I": "PGUP",    # 0x49
    "Q": "PGDN",    # 0x51
    "S": "DELETE",  # 0x53
    "G": "HOME",    # 0x47
    "O": "END",     # 0x4F
}

_STD_INPUT_HANDLE_DWORD = 0xFFFFFFF6  # (DWORD)-10 == STD_INPUT_HANDLE


def _read_csi_sequence(read_next: Callable[[], str | None]) -> tuple[str, str]:
    """Read CSI parameter/intermediate bytes (0x20..0x3F) up to final byte (0x40..0x7E)."""
    params_chars: list[str] = []
    final = ""
    for _ in range(64):
        ch = read_next()
        if not ch:
            return "", ""
        code = ord(ch[0])
        if 0x40 <= code <= 0x7E:
            final = ch[0]
            break
        if 0x20 <= code <= 0x3F:
            params_chars.append(ch[0])
            continue
        return "", ""
    return "".join(params_chars), final


def _parse_escape_tail(read_next: Callable[[], str | None]) -> str:
    """Parse an ANSI / CSI / SS3 / SGR escape sequence after reading the initial ESC (\\x1b)."""
    first = read_next()
    if not first:
        return "Escape"
    if first == "\x1b":
        second = read_next()
        if not second or second == "\x1b":
            return "Escape"
        if second == "[":
            _read_csi_sequence(read_next)
            return ""
        if second == "O":
            read_next()
            return ""
        return ""

    # SS3 sequence: ESC O <final>
    if first == "O":
        ss3 = read_next()
        if not ss3:
            return ""
        return _SS3_KEY_MAP.get(ss3[0], "")

    # Non-CSI / non-SS3 (e.g. Alt+<key>): consume and treat as no-op rather than Escape
    if first != "[":
        return ""

    # CSI sequence: ESC [ <parameter/intermediate 0x20..0x3F>* <final 0x40..0x7E>
    params, final = _read_csi_sequence(read_next)
    if not final:
        return ""

    # SGR mouse sequence: ESC [ < btn ; col ; row M/m
    if params.startswith("<"):
        m = _SGR_MOUSE_RE.match(f"[{params}{final}")
        if m:
            btn = int(m.group(1))
            col = int(m.group(2))
            row = int(m.group(3))
            action = m.group(4)
            if btn == 64:
                return f"MOUSE_WHEEL_UP:{col}:{row}"
            if btn == 65:
                return f"MOUSE_WHEEL_DOWN:{col}:{row}"
            if btn == 0 and action == "M":
                return f"MOUSE_CLICK:{col}:{row}"
        return ""

    if final == "~":
        primary_code = params.split(";", 1)[0]
        return _CSI_TILDE_KEY_MAP.get(primary_code, "")

    return _CSI_LETTER_KEY_MAP.get(final, "")


def _configure_kernel32_console_api(kernel32: Any) -> None:
    """Set 64-bit safe argtypes/restype on kernel32 console HANDLE functions."""
    if wintypes is None:
        return
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL


def _is_valid_win_handle(handle: int | None) -> bool:
    if handle is None or handle == 0:
        return False
    invalid = ctypes.c_void_p(-1).value
    return handle != invalid


def _apply_windows_console_mouse_mode() -> tuple[int | None, int | None]:
    """Configure Windows Console input mode to deliver mouse events and disable QuickEdit."""
    if os.name != "nt" or wintypes is None:
        return None, None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _configure_kernel32_console_api(kernel32)
        raw_handle = kernel32.GetStdHandle(_STD_INPUT_HANDLE_DWORD)
        win_handle = int(raw_handle) if raw_handle is not None else None
        if not _is_valid_win_handle(win_handle):
            return None, None
        assert win_handle is not None
        mode = wintypes.DWORD(0)
        if kernel32.GetConsoleMode(win_handle, ctypes.byref(mode)):
            old_mode = int(mode.value)
            # ENABLE_WINDOW_INPUT (0x0008) | ENABLE_MOUSE_INPUT (0x0010)
            # | ENABLE_EXTENDED_FLAGS (0x0080) | ENABLE_VIRTUAL_TERMINAL_INPUT (0x0200)
            # Clear ENABLE_QUICK_EDIT_MODE (0x0040) so left-click is sent to the app
            new_mode = (old_mode | 0x0008 | 0x0010 | 0x0080 | 0x0200) & ~0x0040
            kernel32.SetConsoleMode(win_handle, new_mode)
            return win_handle, old_mode
    except Exception:
        pass
    return None, None


@contextmanager
def _enable_terminal_mouse(stream: Any = None) -> Iterator[None]:
    """Enable SGR mouse reporting and Windows console mouse input during the TUI session."""
    out = stream if stream is not None else sys.stdout
    is_tty = getattr(out, "isatty", None)
    if not callable(is_tty) or not is_tty():
        yield
        return

    win_handle, old_win_in_mode = _apply_windows_console_mouse_mode()
    try:
        out.write("\x1b[?1000h\x1b[?1006h")
        out.flush()
        yield
    finally:
        try:
            out.write("\x1b[?1006l\x1b[?1000l")
            out.flush()
        except Exception:
            pass
        if os.name == "nt" and old_win_in_mode is not None and win_handle is not None:
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                _configure_kernel32_console_api(kernel32)
                kernel32.SetConsoleMode(win_handle, old_win_in_mode)
            except Exception:
                pass


def _decode_msvcrt_char(first_ch: str, read_second: Callable[[], str | None]) -> str | None:
    """Decode a Windows msvcrt.getwch() character, including \\x00/\\xe0 extended scan codes."""
    if first_ch in {"\x00", "\xe0"}:
        ext = read_second()
        if not ext:
            return None
        return _MSVC_EXTENDED_KEY_MAP.get(ext[0])
    if first_ch == "\x1b":
        return "Escape"
    if first_ch in {"\r", "\n"}:
        return "Enter"
    if first_ch == "\x08":
        return "Backspace"
    if first_ch == "\t":
        return "TAB"
    return first_ch


def read_key(timeout: float = 0.1) -> str | None:
    """Read a single keypress or mouse event non-blockingly across Windows and POSIX terminals."""
    if os.name == "nt":
        return _read_key_windows(timeout)
    return _read_key_posix(timeout)


def _read_key_windows(timeout: float) -> str | None:
    """Read keyboard and mouse events on Windows using ReadConsoleInputW with msvcrt fallback."""
    if not sys.stdin.isatty() or wintypes is None:
        time.sleep(timeout)
        return None

    try:
        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("UnicodeChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class MOUSE_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("dwMousePosition", COORD),
                ("dwButtonState", wintypes.DWORD),
                ("dwControlKeyState", wintypes.DWORD),
                ("dwEventFlags", wintypes.DWORD),
            ]

        class _EventUnion(ctypes.Union):
            _fields_ = [
                ("KeyEvent", KEY_EVENT_RECORD),
                ("MouseEvent", MOUSE_EVENT_RECORD),
                ("Padding", ctypes.c_byte * 16),
            ]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [
                ("EventType", wintypes.WORD),
                ("Event", _EventUnion),
            ]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _configure_kernel32_console_api(kernel32)
        kernel32.GetNumberOfConsoleInputEvents.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
        kernel32.ReadConsoleInputW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(INPUT_RECORD),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.ReadConsoleInputW.restype = wintypes.BOOL

        raw_h_in = kernel32.GetStdHandle(_STD_INPUT_HANDLE_DWORD)
        h_in = int(raw_h_in) if raw_h_in is not None else None
        if not _is_valid_win_handle(h_in):
            time.sleep(timeout)
            return None

        def _peek_count() -> int | None:
            cnt = wintypes.DWORD(0)
            if kernel32.GetNumberOfConsoleInputEvents(h_in, ctypes.byref(cnt)):
                return int(cnt.value)
            return None

        def _read_one_record() -> INPUT_RECORD | None:
            rec = INPUT_RECORD()
            read_cnt = wintypes.DWORD(0)
            if kernel32.ReadConsoleInputW(h_in, ctypes.byref(rec), 1, ctypes.byref(read_cnt)):
                if read_cnt.value > 0:
                    return rec
            return None

        def _read_next_vt_char() -> str | None:
            t_end = time.monotonic() + 0.02
            while time.monotonic() < t_end:
                pending = _peek_count()
                if pending is not None and pending > 0:
                    r = _read_one_record()
                    if r is not None and r.EventType == 0x0001 and r.Event.KeyEvent.bKeyDown:
                        ch = r.Event.KeyEvent.UnicodeChar
                        if ch:
                            return str(ch)
                else:
                    time.sleep(0.001)
            return None

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            pending_events = _peek_count()
            if pending_events is None:
                if msvcrt is not None and msvcrt.kbhit():  # type: ignore[attr-defined]
                    first_wch = str(msvcrt.getwch())  # type: ignore[attr-defined]
                    if first_wch == "\x1b":
                        def _next_msvc_vt() -> str | None:
                            if msvcrt is not None and msvcrt.kbhit():  # type: ignore[attr-defined]
                                return str(msvcrt.getwch())  # type: ignore[attr-defined]
                            return None

                        tok = _parse_escape_tail(_next_msvc_vt)
                        return tok if tok else None
                    return _decode_msvcrt_char(
                        first_wch,
                        lambda: str(msvcrt.getwch()) if msvcrt is not None else None,  # type: ignore[attr-defined]
                    )
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)
                continue

            if pending_events > 0:
                rec = _read_one_record()
                if rec is None:
                    continue

                # KEY_EVENT == 0x0001
                if rec.EventType == 0x0001:
                    ke = rec.Event.KeyEvent
                    if not ke.bKeyDown:
                        continue
                    vk = int(ke.wVirtualKeyCode)
                    ctrl_state = int(ke.dwControlKeyState)
                    ch = str(ke.UnicodeChar) if ke.UnicodeChar else ""

                    if ch == "\x1b":
                        token = _parse_escape_tail(_read_next_vt_char)
                        if token:
                            return token
                        continue

                    if ch in {"\x00", "\xe0"} and msvcrt is not None and msvcrt.kbhit():  # type: ignore[attr-defined]
                        ext_mapped = _decode_msvcrt_char(
                            ch,
                            lambda: str(msvcrt.getwch()) if msvcrt is not None else None,  # type: ignore[attr-defined]
                        )
                        if ext_mapped:
                            return ext_mapped

                    vk_map = {
                        0x26: "UP",      # VK_UP
                        0x28: "DOWN",    # VK_DOWN
                        0x25: "LEFT",    # VK_LEFT
                        0x27: "RIGHT",   # VK_RIGHT
                        0x21: "PGUP",    # VK_PRIOR
                        0x22: "PGDN",    # VK_NEXT
                        0x2E: "DELETE",  # VK_DELETE
                        0x24: "HOME",    # VK_HOME
                        0x23: "END",     # VK_END
                        0x1B: "Escape",  # VK_ESCAPE
                        0x0D: "Enter",   # VK_RETURN
                        0x08: "Backspace",  # VK_BACK
                        0x09: "SHIFT_TAB" if (ctrl_state & 0x0010) else "TAB",  # VK_TAB
                    }
                    special_chars = {"\x00", "\r", "\n", "\x08", "\t", "\x1b"}
                    if vk in vk_map and (not ch or ch in special_chars):
                        return vk_map[vk]
                    if ch in {"\r", "\n"}:
                        return "Enter"
                    if ch == "\x08":
                        return "Backspace"
                    if ch == "\t":
                        return "SHIFT_TAB" if (ctrl_state & 0x0010) else "TAB"
                    if ch and ch != "\x00":
                        return ch

                # MOUSE_EVENT == 0x0002
                elif rec.EventType == 0x0002:
                    me = rec.Event.MouseEvent
                    col = int(me.dwMousePosition.X) + 1
                    row = int(me.dwMousePosition.Y) + 1
                    flags = int(me.dwEventFlags)
                    btn_state = int(me.dwButtonState)

                    # MOUSE_WHEELED == 0x0004
                    if flags & 0x0004:
                        delta = ctypes.c_short((btn_state >> 16) & 0xFFFF).value
                        if delta > 0:
                            return f"MOUSE_WHEEL_UP:{col}:{row}"
                        if delta < 0:
                            return f"MOUSE_WHEEL_DOWN:{col}:{row}"
                    # Button press (flags == 0 or DOUBLE_CLICK == 0x0002)
                    elif flags in {0, 0x0002} and (btn_state & 0x0001):
                        return f"MOUSE_CLICK:{col}:{row}"
                continue

            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)
    except Exception:
        time.sleep(timeout)
        return None


_POSIX_CBREAK_ACTIVE = False
_POSIX_CHAR_BUFFER: deque[str] = deque()
_POSIX_UTF8_DECODER = codecs.getincrementaldecoder("utf-8")("replace")


def _reset_posix_input_state() -> None:
    """Clear buffered POSIX characters and reset the incremental UTF-8 decoder."""
    _POSIX_CHAR_BUFFER.clear()
    _POSIX_UTF8_DECODER.reset()


@contextmanager
def _posix_cbreak_mode() -> Iterator[None]:
    """Enter POSIX cbreak terminal mode once for the duration of the TUI session."""
    global _POSIX_CBREAK_ACTIVE
    if (
        os.name == "nt"
        or not sys.stdin.isatty()
        or termios is None
        or tty is None
        or _POSIX_CBREAK_ACTIVE
    ):
        yield
        return

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except Exception:
        yield
        return

    try:
        tty.setcbreak(fd)
        _POSIX_CBREAK_ACTIVE = True
        yield
    finally:
        _POSIX_CBREAK_ACTIVE = False
        _reset_posix_input_state()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass


def _fill_posix_char_buffer(fd: int, timeout: float) -> bool:
    """Read raw bytes via os.read(fd, 32) and incrementally decode UTF-8 into _POSIX_CHAR_BUFFER."""
    if _POSIX_CHAR_BUFFER:
        return True
    assert select is not None
    rlist, _, _ = select.select([fd], [], [], max(0.0, timeout))
    if not rlist:
        return False

    chunk = os.read(fd, 32)
    if not chunk:
        tail = _POSIX_UTF8_DECODER.decode(b"", final=True)
        if tail:
            _POSIX_CHAR_BUFFER.extend(tail)
        return bool(_POSIX_CHAR_BUFFER)

    decoded = _POSIX_UTF8_DECODER.decode(chunk, final=False)
    if decoded:
        _POSIX_CHAR_BUFFER.extend(decoded)
        return True

    # Incomplete multi-byte UTF-8 sequence at end of chunk: wait briefly for continuation bytes
    deadline = time.monotonic() + 0.015
    while not _POSIX_CHAR_BUFFER:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            flushed = _POSIX_UTF8_DECODER.decode(b"", final=True)
            if flushed:
                _POSIX_CHAR_BUFFER.extend(flushed)
            break
        r_more, _, _ = select.select([fd], [], [], remaining)
        if not r_more:
            flushed = _POSIX_UTF8_DECODER.decode(b"", final=True)
            if flushed:
                _POSIX_CHAR_BUFFER.extend(flushed)
            break
        more = os.read(fd, 32)
        if not more:
            flushed = _POSIX_UTF8_DECODER.decode(b"", final=True)
            if flushed:
                _POSIX_CHAR_BUFFER.extend(flushed)
            break
        more_decoded = _POSIX_UTF8_DECODER.decode(more, final=False)
        if more_decoded:
            _POSIX_CHAR_BUFFER.extend(more_decoded)
            break

    return bool(_POSIX_CHAR_BUFFER)


def _read_key_posix_once(timeout: float, *, fd: int | None = None) -> str | None:
    target_fd = fd if fd is not None else sys.stdin.fileno()
    if not _POSIX_CHAR_BUFFER and not _fill_posix_char_buffer(target_fd, timeout):
        return None
    if not _POSIX_CHAR_BUFFER:
        return None

    ch = _POSIX_CHAR_BUFFER.popleft()
    if ch == "\x1b":
        def _next_posix_char() -> str | None:
            if _POSIX_CHAR_BUFFER or _fill_posix_char_buffer(target_fd, 0.015):
                return _POSIX_CHAR_BUFFER.popleft()
            return None

        token = _parse_escape_tail(_next_posix_char)
        return token if token else None
    if ch in {"\r", "\n"}:
        return "Enter"
    if ch in {"\x7f", "\x08"}:
        return "Backspace"
    if ch == "\t":
        return "TAB"
    return ch


def _read_key_posix(timeout: float) -> str | None:
    if not sys.stdin.isatty() or select is None or termios is None or tty is None:
        time.sleep(timeout)
        return None

    if _POSIX_CBREAK_ACTIVE:
        return _read_key_posix_once(timeout)

    with _posix_cbreak_mode():
        return _read_key_posix_once(timeout)


def run_tui(
    client: GitSeedClient,
    *,
    initial_project: str | None = None,
    initial_view: str = "seeds",
    refresh_interval: float = 3.0,
    once: bool = False,
    console: Console | None = None,
) -> TUIState:
    """Launch the interactive GitSeed Terminal UI (or render a single snapshot when once=True)."""
    controller = TUIController(
        client,
        initial_project=initial_project,
        initial_view=initial_view,
        async_mode=not once,
    )
    controller.refresh_data()

    if once:
        snap_console = console or Console(width=160, height=36)
        with controller.lock:
            controller.state.terminal_width = snap_console.size.width
            controller.state.terminal_height = snap_console.size.height
            initial_frame = render_tui_frame(controller.state)
        snap_console.print(initial_frame)
        controller.close()
        return controller.state

    live_console = console or Console()
    with controller.lock:
        controller.state.terminal_width = live_console.size.width
        controller.state.terminal_height = live_console.size.height
        first_frame = render_tui_frame(controller.state)
    last_refresh = time.monotonic()

    try:
        with (
            _posix_cbreak_mode(),
            _enable_terminal_mouse(live_console.file),
            Live(
                first_frame,
                console=live_console,
                screen=True,
                refresh_per_second=20,
            ) as live,
        ):
            while True:
                with controller.lock:
                    controller.state.terminal_width = live_console.size.width
                    controller.state.terminal_height = live_console.size.height
                key = read_key(timeout=0.04)
                needs_render = False
                if key is not None:
                    keep_going = controller.handle_key(key)
                    if not keep_going:
                        break
                    # Coalesce rapid queued key/wheel bursts into a single smooth frame
                    exit_requested = False
                    for _ in range(8):
                        next_key = read_key(timeout=0.0)
                        if next_key is None:
                            break
                        if not controller.handle_key(next_key):
                            exit_requested = True
                            break
                    if exit_requested:
                        break
                    needs_render = True

                with controller.lock:
                    if controller.dirty:
                        controller.dirty = False
                        needs_render = True
                    frame_to_render = render_tui_frame(controller.state) if needs_render else None
                    input_mode_active = controller.state.input_mode is not None

                if frame_to_render is not None:
                    live.update(frame_to_render)

                now = time.monotonic()
                if (
                    not input_mode_active
                    and refresh_interval > 0
                    and (now - last_refresh) >= refresh_interval
                ):
                    controller.trigger_background_refresh()
                    last_refresh = now
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()

    return controller.state
