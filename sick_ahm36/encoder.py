"""High-level client for the SICK AHM36A encoder.

This is the only class most callers need. It opens a transport (real or
simulated), listens for the encoder's broadcast process data, and hands back
decoded :class:`~sick_ahm36.decode.ProcessData`.

Because the encoder *broadcasts* position + speed by default (PGN 0xFFE0 every
10 ms), reading is a listen-and-decode operation - there is nothing to request.

Example::

    from sick_ahm36 import Ahm36Encoder

    with Ahm36Encoder({"interface": "sim"}) as enc:
        print(enc.get_position(), enc.get_speed())
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

from . import config as configmsg
from . import j1939, protocol
from .config import ConfigError, ConfigResponse
from .decode import ProcessData, decode_ffe0
from .protocol import PARAM_INFO, ConfigMsgId, Param, SpeedFormat
from .transport import open_transport


@dataclass(frozen=True)
class ParamCheck:
    """Result of comparing one read-back parameter against an expected value."""

    param: Param
    expected: int
    actual: int | None        # None if the read failed
    ok: bool
    note: str = ""            # failure reason when actual is None


@dataclass(frozen=True)
class ParamWrite:
    """Result of writing (or skipping) one parameter."""

    param: Param
    value: int
    ok: bool
    skipped: bool = False     # already at target, write not attempted
    error: str = ""


class Ahm36Encoder:
    def __init__(self, config: dict | None = None):
        """``config`` selects the transport and how frames are interpreted.

        Keys:
            interface       backend name ("sim", "pcan", "socketcan", ...)
            channel         backend channel (adapter dependent)
            bitrate         CAN bit rate (default 250000)
            source_address  encoder node address to accept, or None for any
                            (default 0xE0 / 224)
            client_address  this PC's J1939 source address for config requests
                            (default 0xF9 / 249, the SAE "service tool" address)
            speed_format    SpeedFormat the encoder is configured for (label only)
            steps_per_rev   counts per revolution for angle conversion
            config_timeout  seconds to wait for a config reply (default 1.0)
        """
        self.config = dict(config or {})
        sa = self.config.get("source_address", protocol.DEFAULT_SOURCE_ADDRESS)
        self._source_address = sa  # None => accept any source address
        # concrete address to send config requests to (None filter still needs a target)
        self._encoder_address = sa if sa is not None else protocol.DEFAULT_SOURCE_ADDRESS
        self._client_address = int(self.config.get("client_address", 0xF9))
        self._config_timeout = float(self.config.get("config_timeout", 1.0))
        self._speed_format = SpeedFormat(self.config.get("speed_format",
                                                         protocol.DEFAULT_SPEED_FORMAT))
        self._steps_per_rev = int(self.config.get("steps_per_rev", protocol.STEPS_PER_REV))

        self._transport = open_transport(self.config)

        # background-dispatcher state
        self._latest: ProcessData | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._config_replies: "queue.Queue[ConfigResponse]" = queue.Queue()
        self._config_lock = threading.Lock()  # one config transaction at a time

    # --- frame handling -----------------------------------------------------

    def _matches(self, arbitration_id: int) -> bool:
        ident = j1939.parse_id(arbitration_id)
        if ident.pgn != protocol.PGN_FFE0:
            return False
        if self._source_address is None:
            return True
        return ident.source_address == self._source_address

    def _is_config_reply(self, ident: j1939.J1939Id) -> bool:
        return (ident.pgn == protocol.PGN_CONFIG
                and ident.source_address == self._encoder_address
                and ident.destination_address == self._client_address)

    def read_process_data(self, timeout: float = 1.0) -> ProcessData | None:
        """Block until the next matching 0xFFE0 frame, or ``timeout`` seconds.

        Returns the decoded :class:`ProcessData`, or ``None`` on timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            frame = self._transport.recv(timeout=remaining)
            if frame is None:
                continue
            if self._matches(frame.arbitration_id):
                return decode_ffe0(frame.data,
                                   speed_format=self._speed_format,
                                   steps_per_rev=self._steps_per_rev)

    # --- convenience accessors ---------------------------------------------

    def _fresh_or_latest(self, timeout: float) -> ProcessData | None:
        if self._thread is not None:
            return self.latest
        return self.read_process_data(timeout=timeout)

    def get_position(self, timeout: float = 1.0) -> int | None:
        """Latest absolute position in counts (None if unavailable)."""
        pd = self._fresh_or_latest(timeout)
        return pd.position_counts if pd else None

    def get_speed(self, timeout: float = 1.0) -> int | None:
        """Latest speed in the encoder's configured unit (None if unavailable)."""
        pd = self._fresh_or_latest(timeout)
        return pd.speed_raw if pd else None

    def get_status(self, timeout: float = 1.0) -> int | None:
        """Latest status word (0x0000 = healthy; None if unavailable)."""
        pd = self._fresh_or_latest(timeout)
        return pd.status_word if pd else None

    # --- optional background listener --------------------------------------

    @property
    def latest(self) -> ProcessData | None:
        """Most recent decoded frame captured by the background listener."""
        with self._lock:
            return self._latest

    def start_background(self) -> None:
        """Start a thread that continuously caches the latest process data,
        making ``latest`` / ``get_*`` non-blocking."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ahm36-listener",
                                        daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        """Single reader: route every frame so process data and config replies
        never steal each other's frames."""
        while not self._stop.is_set():
            frame = self._transport.recv(timeout=0.2)
            if frame is None:
                continue
            ident = j1939.parse_id(frame.arbitration_id)
            if ident.pgn == protocol.PGN_FFE0 and self._matches(frame.arbitration_id):
                pd = decode_ffe0(frame.data, speed_format=self._speed_format,
                                 steps_per_rev=self._steps_per_rev)
                with self._lock:
                    self._latest = pd
            elif self._is_config_reply(ident):
                self._config_replies.put(configmsg.parse_response(frame.data))

    # --- configuration (PGN 0xEF00) ----------------------------------------

    def _config_txn(self, frame, param_index: int, timeout: float) -> ConfigResponse | None:
        """Send a config request and wait for the matching reply.

        Uses the dispatcher's reply queue when the background reader is running;
        otherwise reads directly. Returns None on timeout.
        """
        with self._config_lock:
            return self._config_txn_locked(frame, param_index, timeout)

    def _config_txn_locked(self, frame, param_index: int, timeout: float) -> ConfigResponse | None:
        deadline = time.monotonic() + timeout
        if self._thread is not None:
            # drain any stale replies, then send and wait
            try:
                while True:
                    self._config_replies.get_nowait()
            except queue.Empty:
                pass
            self._transport.send(frame)
            while time.monotonic() < deadline:
                try:
                    resp = self._config_replies.get(timeout=deadline - time.monotonic())
                except queue.Empty:
                    break
                if resp.param_index == param_index:
                    return resp
            return None

        # no dispatcher: send and read directly, skipping process-data frames
        self._transport.send(frame)
        while time.monotonic() < deadline:
            f = self._transport.recv(timeout=deadline - time.monotonic())
            if f is None:
                continue
            ident = j1939.parse_id(f.arbitration_id)
            if self._is_config_reply(ident):
                resp = configmsg.parse_response(f.data)
                if resp.param_index == param_index:
                    return resp
        return None

    def read_param(self, param: Param) -> int:
        """Read a configuration parameter from the encoder. Raises on error.

        The SICK read request must carry the parameter's byte length (its data
        type size), same as a write - the manual's read examples show e.g.
        len=1 for counting direction (UINT8), len=4 for steps/rev (UINT32).
        Sending length 0 makes the encoder reject the read with error 0xFF.
        """
        ptype, _desc = PARAM_INFO[param]
        frame = configmsg.build_request(
            ConfigMsgId.READ, int(param),
            source_address=self._client_address,
            destination_address=self._encoder_address,
            length=int(ptype))
        resp = self._config_txn(frame, int(param), self._config_timeout)
        if resp is None:
            raise TimeoutError(f"no reply reading param {int(param)} ({param.name})")
        if not resp.ok:
            raise ConfigError(resp)
        return resp.value

    def write_param(self, param: Param, value: int) -> None:
        """Write a configuration parameter. Raises ConfigError if rejected.

        Note: writing baud rate or node address can drop the encoder off the
        bus until the master matches the new setting; resets are destructive.
        """
        ptype, _desc = PARAM_INFO[param]
        # A few params read and write at different indexes (e.g. measuring range
        # reads at 128 but writes at 131); honour that here.
        write_index = protocol.WRITE_INDEX_OVERRIDE.get(param, int(param))
        frame = configmsg.build_request(
            ConfigMsgId.WRITE, write_index,
            source_address=self._client_address,
            destination_address=self._encoder_address,
            length=int(ptype), value=int(value))
        resp = self._config_txn(frame, write_index, self._config_timeout)
        if resp is None:
            raise TimeoutError(f"no reply writing param {int(param)} ({param.name})")
        if not resp.ok:
            raise ConfigError(resp)
        # keep local decode settings consistent with the device
        if param == Param.SPEED_FORMAT:
            self._speed_format = SpeedFormat(int(value))
        elif param == Param.STEPS_PER_REV:
            self._steps_per_rev = int(value)

    def read_all_params(self) -> dict[Param, int]:
        """Read every readable parameter; skips write-only ones and any the
        encoder reports as unavailable."""
        out: dict[Param, int] = {}
        for param in PARAM_INFO:
            if param in (Param.PRESET, Param.POWER_CYCLE, Param.FACTORY_RESET):
                continue
            try:
                out[param] = self.read_param(param)
            except ConfigError:
                continue
        return out

    def verify_params(self, expected: dict[Param, int]) -> list[ParamCheck]:
        """Read each expected parameter back and compare to its target value.

        Never raises: an unreadable parameter becomes a failed
        :class:`ParamCheck` with the reason in ``note`` so the whole report
        survives one bad parameter.
        """
        results: list[ParamCheck] = []
        for param, want in expected.items():
            try:
                actual = self.read_param(param)
                results.append(ParamCheck(param, want, actual, actual == want))
            except (ConfigError, TimeoutError) as exc:
                results.append(ParamCheck(param, want, None, False, note=str(exc)))
        return results

    def apply_params(self, values: dict[Param, int], *,
                     skip_if_equal: bool = True) -> list[ParamWrite]:
        """Write each parameter, in the dict's order. Never raises.

        With ``skip_if_equal`` (default) a parameter already at its target is
        read first and left untouched - this avoids needlessly re-writing
        disruptive parameters (baud rate, node address) that are already
        correct. Failures are captured per-parameter so one rejection does not
        abort the rest.
        """
        results: list[ParamWrite] = []
        for param, want in values.items():
            try:
                if skip_if_equal:
                    try:
                        if self.read_param(param) == want:
                            results.append(ParamWrite(param, want, ok=True, skipped=True))
                            continue
                    except (ConfigError, TimeoutError):
                        pass  # unreadable -> fall through and attempt the write
                self.write_param(param, want)
                results.append(ParamWrite(param, want, ok=True))
            except (ConfigError, TimeoutError) as exc:
                results.append(ParamWrite(param, want, ok=False, error=str(exc)))
        return results

    def preset(self, value: int) -> None:
        """Set the current position to ``value`` (param 200, write-only)."""
        self.write_param(Param.PRESET, value)

    def power_cycle(self) -> None:
        """Trigger an encoder reset / power cycle (param 254, write-only).

        The trigger value is 0 (confirmed live: value 1 is rejected with 0xFF;
        value 0 is accepted and the encoder resets).
        """
        self.write_param(Param.POWER_CYCLE, 0)

    def factory_reset(self) -> None:
        """Restore factory defaults (param 255, write-only). Destructive.

        Trigger value 0, matching the power-cycle command (these reset commands
        are triggered by writing 0, not 1).
        """
        self.write_param(Param.FACTORY_RESET, 0)

    # --- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self.stop_background()
        self._transport.close()

    def __enter__(self) -> "Ahm36Encoder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
