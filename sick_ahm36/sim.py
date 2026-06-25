"""Offline simulator for the AHM36A.

A :class:`~sick_ahm36.transport.BaseTransport` that fabricates PGN 0xFFE0
broadcast frames on a ~10 ms cadence, with a steadily advancing position driven
by a configurable speed. It also answers PGN 0xEF00 configuration requests so
the whole library - including the config panel of the GUI - can be exercised
with nothing plugged in.

Enable it with ``interface="sim"``. Optional config keys:

    sim_speed_rpm   constant shaft speed to simulate   (default 60.0)
    sim_cycle_ms    initial broadcast period in ms     (default 10)
    sim_status      status word to report              (default 0x0000)
    source_address  encoder node address               (default 0xE0)
"""

from __future__ import annotations

import queue
import threading
import time

from . import config as configmsg
from . import j1939, protocol
from .decode import encode_ffe0
from .protocol import PARAM_INFO, ConfigMsgId, Param
from .transport import BaseTransport, Frame

# Read-only-from-the-outside defaults the simulated encoder powers up with.
_DEFAULT_PARAMS = {
    Param.BAUD_RATE: 250,
    Param.COUNTING_DIRECTION: 0,
    Param.STEPS_PER_REV: protocol.STEPS_PER_REV,
    Param.TOTAL_MEASURING_RANGE: protocol.TOTAL_RANGE,
    Param.SPEED_FORMAT: int(protocol.DEFAULT_SPEED_FORMAT),
    Param.UPDATE_TIME_T1: 2,
    Param.CYCLE_TIME_FFE0: 10,
    Param.CYCLE_TIME_FFE1: 0,
    Param.CYCLE_TIME_FFE2: 0,
    Param.NODE_ADDRESS: protocol.DEFAULT_SOURCE_ADDRESS,
}
_WRITE_ONLY = (Param.PRESET, Param.POWER_CYCLE, Param.FACTORY_RESET)


class SimulatedBus(BaseTransport):
    def __init__(self, config: dict | None = None):
        config = config or {}
        self._speed_rpm = float(config.get("sim_speed_rpm", 60.0))
        self._status = int(config.get("sim_status", 0x0000))
        self._sa = int(config.get("source_address", protocol.DEFAULT_SOURCE_ADDRESS))
        self._arb_id = j1939.build_id(protocol.PGN_FFE0, self._sa)

        self._lock = threading.Lock()
        self._params = dict(_DEFAULT_PARAMS)
        self._params[Param.CYCLE_TIME_FFE0] = int(config.get("sim_cycle_ms", 10))

        self._position = 0.0  # float accumulator, wrapped on emit
        self._next_emit = time.monotonic()
        self._replies: "queue.Queue[Frame]" = queue.Queue()

    # --- process-data broadcast --------------------------------------------

    def _period(self) -> float:
        ms = self._params[Param.CYCLE_TIME_FFE0]
        return (ms / 1000.0) if ms > 0 else 0.0  # 0 => broadcast disabled

    def _direction_sign(self) -> int:
        return -1 if self._params[Param.COUNTING_DIRECTION] == 1 else 1

    def _counts_per_period(self, period: float) -> float:
        steps = self._params[Param.STEPS_PER_REV]
        counts_per_sec = self._speed_rpm / 60.0 * steps
        return counts_per_sec * period

    def _emit_process_data(self, period: float) -> Frame:
        sign = self._direction_sign()
        total = self._params[Param.TOTAL_MEASURING_RANGE] or protocol.TOTAL_RANGE
        self._position = (self._position + sign * self._counts_per_period(period)) % total
        speed_raw = int(round(sign * self._speed_rpm))  # simulated unit: rpm
        data = encode_ffe0(int(self._position), speed_raw, self._status)
        return Frame(arbitration_id=self._arb_id, data=data,
                     is_extended_id=True, timestamp=time.time())

    def recv(self, timeout: float | None = None) -> Frame | None:
        # config replies take priority so the master never misses one
        try:
            return self._replies.get_nowait()
        except queue.Empty:
            pass

        with self._lock:
            period = self._period()

        if period <= 0.0:  # broadcasting disabled -> idle until timeout
            time.sleep(min(timeout, 0.05) if timeout is not None else 0.05)
            try:
                return self._replies.get_nowait()
            except queue.Empty:
                return None

        now = time.monotonic()
        wait = self._next_emit - now
        if wait > 0:
            if timeout is not None and wait > timeout:
                time.sleep(timeout)
                return None
            time.sleep(wait)

        with self._lock:
            frame = self._emit_process_data(period)
            self._next_emit += period
            if self._next_emit < time.monotonic():
                self._next_emit = time.monotonic() + period
        return frame

    # --- configuration (PGN 0xEF00) ----------------------------------------

    def send(self, frame: Frame) -> None:
        ident = j1939.parse_id(frame.arbitration_id)
        if ident.pgn != protocol.PGN_CONFIG or ident.destination_address != self._sa:
            return  # not a config request addressed to us
        client = ident.source_address
        msg_id, index, _length, _err, value = configmsg.parse_message(frame.data)
        with self._lock:
            reply = self._handle_config(msg_id, index, value, client)
        if reply is not None:
            self._replies.put(reply)

    def _handle_config(self, msg_id: int, index: int, value: int, client: int) -> Frame | None:
        def respond(error_code: int, val: int = 0, length: int = 0) -> Frame:
            return configmsg.build_response(
                index, source_address=self._sa, destination_address=client,
                length=length, value=val, error_code=error_code)

        try:
            param = Param(index)
        except ValueError:
            return respond(0x01)  # parameter not available

        if msg_id == ConfigMsgId.READ:
            if param in _WRITE_ONLY:
                return respond(0x05)  # read of write-only
            length = int(PARAM_INFO[param][0])
            return respond(0x00, val=self._params[param], length=length)

        if msg_id == ConfigMsgId.WRITE:
            if param == Param.PRESET:
                self._position = float(value)
                return respond(0x00)
            if param == Param.POWER_CYCLE:
                self._position = 0.0
                return respond(0x00)
            if param == Param.FACTORY_RESET:
                self._params = dict(_DEFAULT_PARAMS)
                self._position = 0.0
                return respond(0x00)
            if param in self._params:
                self._params[param] = value
                return respond(0x00)
            return respond(0x06)  # write of read-only

        return respond(0x07)  # message ID not supported

    def close(self) -> None:
        pass
