"""Tkinter GUI for the SICK AHM36A encoder.

A single window that:
  * connects to the encoder via any python-can backend (or the offline "sim"),
  * polls and displays live position + speed + status,
  * reads and writes configuration parameters over PGN 0xEF00, with confirmation
    dialogs guarding the disruptive ones (baud rate, node address, resets).

Run it:
    python -m sick_ahm36.gui          (or the installed `sick-ahm36-gui` command)

Tkinter is part of the standard library, so there is nothing extra to install.

Threading model: the encoder's background dispatcher owns the CAN reader. The
GUI never touches widgets from a worker thread - blocking calls (connect,
config read/write) run on short-lived threads and post their results back to the
Tk main loop through a queue drained by ``after()``.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import profile, protocol
from .config import ConfigError
from .encoder import Ahm36Encoder
from .protocol import PARAM_INFO, SPEED_UNIT_LABELS, Param, SpeedFormat

# python-can backends commonly used with this encoder; "sim" is built in here.
INTERFACES = ["sim", "socketcan", "pcan", "kvaser", "vector", "virtual",
              "ixxat", "slcan", "seeedstudio", "usb2can", "neovi"]

# Writes that can knock the device off the bus or are destructive -> confirm.
RISKY_PARAMS = {Param.BAUD_RATE, Param.NODE_ADDRESS}

# Parameters shown as editable rows (write-only actions handled separately).
READABLE_PARAMS = [p for p in PARAM_INFO
                   if p not in (Param.PRESET, Param.POWER_CYCLE, Param.FACTORY_RESET)]


class EncoderGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SICK AHM36A - Encoder Monitor & Config")
        self.minsize(720, 560)

        self._enc: Ahm36Encoder | None = None
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._param_rows: dict[Param, dict] = {}

        self._build_connection_bar()
        self._build_live_panel()
        self._build_config_panel()
        self._build_statusbar()

        self._set_connected(False)
        self.after(80, self._pump)            # drain worker results
        self.after(150, self._refresh_live)   # update live readout
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- layout -------------------------------------------------------------

    def _build_connection_bar(self):
        f = ttk.LabelFrame(self, text="Connection")
        f.pack(fill="x", padx=8, pady=(8, 4))

        self.var_interface = tk.StringVar(value="sim")
        self.var_channel = tk.StringVar(value="")
        self.var_bitrate = tk.StringVar(value=str(protocol.DEFAULT_BITRATE))
        self.var_addr = tk.StringVar(value=str(protocol.DEFAULT_SOURCE_ADDRESS))

        ttk.Label(f, text="Interface").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        ttk.Combobox(f, textvariable=self.var_interface, values=INTERFACES,
                     width=12, state="readonly").grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(f, text="Channel").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(f, textvariable=self.var_channel, width=14).grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(f, text="Bitrate").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        ttk.Entry(f, textvariable=self.var_bitrate, width=9).grid(row=0, column=5, padx=4, pady=4)

        ttk.Label(f, text="Encoder addr").grid(row=0, column=6, padx=4, pady=4, sticky="e")
        ttk.Entry(f, textvariable=self.var_addr, width=6).grid(row=0, column=7, padx=4, pady=4)

        self.btn_connect = ttk.Button(f, text="Connect", command=self._toggle_connect)
        self.btn_connect.grid(row=0, column=8, padx=8, pady=4)

    def _build_live_panel(self):
        f = ttk.LabelFrame(self, text="Live")
        f.pack(fill="x", padx=8, pady=4)

        self.var_pos = tk.StringVar(value="-")
        self.var_revs = tk.StringVar(value="-")
        self.var_speed = tk.StringVar(value="-")
        self.var_status = tk.StringVar(value="-")
        self.var_yaw = tk.StringVar(value="-")
        self.var_zero = tk.StringVar(value="0")

        big = ("Segoe UI", 22, "bold")
        ttk.Label(f, text="Position (counts)").grid(row=0, column=0, padx=10, sticky="w")
        ttk.Label(f, textvariable=self.var_pos, font=big).grid(row=1, column=0, padx=10, sticky="w")
        ttk.Label(f, textvariable=self.var_revs).grid(row=2, column=0, padx=10, sticky="w", pady=(0, 6))

        ttk.Label(f, text="Speed").grid(row=0, column=1, padx=10, sticky="w")
        ttk.Label(f, textvariable=self.var_speed, font=big).grid(row=1, column=1, padx=10, sticky="w")

        ttk.Label(f, text="Status").grid(row=0, column=2, padx=10, sticky="w")
        self.lbl_status = ttk.Label(f, textvariable=self.var_status, font=("Segoe UI", 11, "bold"))
        self.lbl_status.grid(row=1, column=2, padx=10, sticky="w")

        # Tophandler yaw: the angle the VCU firmware computes from the same raw
        # counts (16:1 gear, wrap 262144, +/-180 deg fold, minus zero offset).
        ttk.Label(f, text="Tophandler yaw").grid(row=0, column=3, padx=10, sticky="w")
        ttk.Label(f, textvariable=self.var_yaw, font=big).grid(row=1, column=3, padx=10, sticky="w")
        zoff = ttk.Frame(f)
        zoff.grid(row=2, column=3, padx=10, sticky="w", pady=(0, 6))
        ttk.Label(zoff, text="zero offset").pack(side="left")
        ttk.Entry(zoff, textvariable=self.var_zero, width=9).pack(side="left", padx=4)
        ttk.Button(zoff, text="Zero here", width=9,
                   command=self._zero_here).pack(side="left")

        f.columnconfigure(2, weight=1)

    def _build_config_panel(self):
        outer = ttk.LabelFrame(self, text="Configuration (PGN 0xEF00)")
        outer.pack(fill="both", expand=True, padx=8, pady=4)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", padx=4, pady=4)
        self.btn_readall = ttk.Button(bar, text="Read all", command=self._read_all)
        self.btn_readall.pack(side="left")

        # Reach-stacker yaw profile: one-click match-the-firmware config + check.
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_verify = ttk.Button(bar, text="Verify reach-stacker profile",
                                     command=self._verify_profile)
        self.btn_verify.pack(side="left")
        self.btn_apply = ttk.Button(bar, text="Apply reach-stacker profile",
                                    command=self._apply_profile)
        self.btn_apply.pack(side="left", padx=4)
        ttk.Label(bar, text="direction").pack(side="left", padx=(8, 2))
        self.var_direction = tk.StringVar(value="leave")
        ttk.Combobox(bar, textvariable=self.var_direction, width=6, state="readonly",
                     values=["leave", "CW", "CCW"]).pack(side="left")

        grid = ttk.Frame(outer)
        grid.pack(fill="both", expand=True, padx=4, pady=4)
        headers = ["Parameter", "Current", "New value", "", "", "Notes"]
        for c, h in enumerate(headers):
            ttk.Label(grid, text=h, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=c, padx=4, pady=2, sticky="w")

        for r, param in enumerate(READABLE_PARAMS, start=1):
            ptype, desc = PARAM_INFO[param]
            cur = tk.StringVar(value="-")
            new = tk.StringVar(value="")
            ttk.Label(grid, text=f"{param.name} ({int(param)})").grid(
                row=r, column=0, padx=4, pady=2, sticky="w")
            ttk.Label(grid, textvariable=cur, width=12).grid(
                row=r, column=1, padx=4, pady=2, sticky="w")
            ttk.Entry(grid, textvariable=new, width=14).grid(
                row=r, column=2, padx=4, pady=2)
            ttk.Button(grid, text="Read", width=6,
                       command=lambda p=param: self._read_one(p)).grid(row=r, column=3, padx=2)
            ttk.Button(grid, text="Write", width=6,
                       command=lambda p=param: self._write_one(p)).grid(row=r, column=4, padx=2)
            ttk.Label(grid, text=desc, foreground="#666",
                      wraplength=260).grid(row=r, column=5, padx=4, sticky="w")
            self._param_rows[param] = {"cur": cur, "new": new}

        # Friendly "turns before wrapping" view over TOTAL_MEASURING_RANGE
        # (encoder-shaft revolutions = measuring range / steps-per-rev).
        turns_fr = ttk.Frame(outer)
        turns_fr.pack(fill="x", padx=4, pady=(8, 0))
        ttk.Label(turns_fr, text="Turns before wrapping (encoder revs):").pack(side="left", padx=(0, 4))
        self.var_turns = tk.StringVar(value="-")
        ttk.Entry(turns_fr, textvariable=self.var_turns, width=10).pack(side="left")
        ttk.Button(turns_fr, text="Read", width=6, command=self._read_turns).pack(side="left", padx=4)
        ttk.Button(turns_fr, text="Write", width=6, command=self._write_turns).pack(side="left")
        ttk.Label(turns_fr, text="= total measuring range ÷ steps-per-rev",
                  foreground="#666").pack(side="left", padx=8)

        # write-only actions
        actions = ttk.Frame(outer)
        actions.pack(fill="x", padx=4, pady=(6, 4))
        self.var_preset = tk.StringVar(value="0")
        ttk.Label(actions, text="Preset position to:").pack(side="left", padx=(0, 4))
        ttk.Entry(actions, textvariable=self.var_preset, width=12).pack(side="left")
        ttk.Button(actions, text="Set preset", command=self._do_preset).pack(side="left", padx=4)
        ttk.Button(actions, text="Power cycle", command=self._do_power_cycle).pack(side="left", padx=12)
        ttk.Button(actions, text="Factory reset", command=self._do_factory_reset).pack(side="left")
        self._config_widgets = (outer,)

    def _build_statusbar(self):
        self.var_msg = tk.StringVar(value="Not connected.")
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        ttk.Separator(bar, orient="horizontal").pack(fill="x")
        ttk.Label(bar, textvariable=self.var_msg, anchor="w").pack(fill="x", padx=8, pady=3)

    # --- connection ---------------------------------------------------------

    def _build_config(self) -> dict:
        cfg: dict = {"interface": self.var_interface.get()}
        ch = self.var_channel.get().strip()
        if ch:
            cfg["channel"] = ch
        try:
            cfg["bitrate"] = int(self.var_bitrate.get())
        except ValueError:
            pass
        try:
            cfg["source_address"] = int(self.var_addr.get())
        except ValueError:
            pass
        return cfg

    def _toggle_connect(self):
        if self._enc is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        cfg = self._build_config()
        self._set_msg(f"Connecting ({cfg.get('interface')}) ...")
        self.btn_connect.config(state="disabled")

        def work():
            try:
                enc = Ahm36Encoder(cfg)
                enc.start_background()
                self._post(lambda: self._on_connected(enc))
            except Exception as exc:  # noqa: BLE001 - surface any backend error
                self._post(lambda e=exc: self._on_connect_failed(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_connected(self, enc: Ahm36Encoder):
        self._enc = enc
        self._set_connected(True)
        self._set_msg("Connected. Reading parameters ...")
        self._read_all()

    def _on_connect_failed(self, exc: Exception):
        self.btn_connect.config(state="normal")
        self._set_msg(f"Connect failed: {exc}")
        messagebox.showerror("Connect failed", str(exc))

    def _disconnect(self):
        enc, self._enc = self._enc, None
        if enc is not None:
            threading.Thread(target=enc.close, daemon=True).start()
        self._set_connected(False)
        self._set_msg("Disconnected.")
        self.var_pos.set("-")
        self.var_revs.set("-")
        self.var_speed.set("-")
        self.var_status.set("-")
        self.var_yaw.set("-")

    def _set_connected(self, connected: bool):
        self.btn_connect.config(text="Disconnect" if connected else "Connect",
                                state="normal")
        state = "normal" if connected else "disabled"
        self.btn_readall.config(state=state)
        self.btn_verify.config(state=state)
        self.btn_apply.config(state=state)

    # --- live readout -------------------------------------------------------

    def _refresh_live(self):
        enc = self._enc
        if enc is not None:
            pd = enc.latest
            if pd is not None:
                self.var_pos.set(f"{pd.position_counts:,}")
                self.var_revs.set(f"{pd.revolutions:.3f} rev   |   {pd.angle_deg:.2f}°")
                self.var_speed.set(f"{pd.speed_raw} {pd.speed_unit}")
                self.var_yaw.set(f"{profile.tophandler_angle_deg(pd.position_counts, self._zero_offset()):+.2f}°")
                if pd.healthy:
                    self.var_status.set("OK")
                    self.lbl_status.config(foreground="#1a7f37")
                else:
                    self.var_status.set("FAULT: " + "; ".join(pd.status_messages))
                    self.lbl_status.config(foreground="#b00")
        self.after(150, self._refresh_live)

    # --- worker plumbing ----------------------------------------------------

    def _post(self, fn):
        self._ui_queue.put(fn)

    def _pump(self):
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self._set_msg(f"UI error: {exc}")
        self.after(80, self._pump)

    def _run_async(self, fn, on_ok, busy: str):
        """Run blocking ``fn`` off the main thread; deliver result via the queue."""
        if self._enc is None:
            return
        self._set_msg(busy)

        def work():
            try:
                res = fn()
                self._post(lambda: on_ok(res))
            except Exception as exc:  # noqa: BLE001
                self._post(lambda e=exc: self._on_async_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_async_error(self, exc: Exception):
        if isinstance(exc, ConfigError):
            self._set_msg(f"Rejected: {exc}")
        else:
            self._set_msg(f"Error: {exc}")
        messagebox.showerror("Operation failed", str(exc))

    # --- config actions -----------------------------------------------------

    def _read_all(self):
        enc = self._enc
        if enc is None:
            return

        def on_ok(values: dict):
            for param, val in values.items():
                row = self._param_rows.get(param)
                if row:
                    row["cur"].set(self._format_value(param, val))
            self._set_msg(f"Read {len(values)} parameters.")

        self._run_async(enc.read_all_params, on_ok, "Reading all parameters ...")

    # --- reach-stacker yaw profile -----------------------------------------

    def _zero_offset(self) -> int:
        try:
            return int(self.var_zero.get().strip() or 0, 0)
        except ValueError:
            return 0

    def _zero_here(self):
        """Capture the current raw position as the tophandler-yaw zero offset."""
        enc = self._enc
        pd = enc.latest if enc is not None else None
        if pd is None:
            self._set_msg("No live position yet to zero against.")
            return
        self.var_zero.set(str(pd.position_counts))
        self._set_msg(f"Tophandler yaw zero set to {pd.position_counts} counts "
                      "(display only - does not write the encoder).")

    def _verify_profile(self):
        enc = self._enc
        if enc is None:
            return

        def on_ok(checks):
            ok = all(c.ok for c in checks)
            report = "\n".join(profile.format_check(c) for c in checks)
            self._set_msg("Profile matches firmware." if ok
                          else "Profile MISMATCH - see report.")
            body = f"Required parameters:\n{report}\n\n{profile.MANUAL_NOTES}"
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "Verify reach-stacker profile", body)

        self._run_async(lambda: profile.verify(enc), on_ok,
                        "Verifying reach-stacker profile ...")

    def _apply_profile(self):
        enc = self._enc
        if enc is None:
            return
        choice = self.var_direction.get()
        direction = {"CW": 0, "CCW": 1}.get(choice)  # None for "leave"
        dir_line = ("counting direction left unchanged"
                    if direction is None else f"counting direction = {choice} ({direction})")
        if not messagebox.askyesno(
                "Apply reach-stacker profile",
                f"{profile.summary()}\n  {dir_line}\n\n"
                "Node address and baud are written last and skipped if already "
                "correct, but changing either can briefly drop the encoder off "
                "the bus.\n\nProceed?"):
            return

        def on_ok(results):
            report = "\n".join(profile.format_write(r) for r in results)
            failed = [r for r in results if not r.ok]
            self._set_msg("Profile applied."
                          if not failed else f"Profile applied with {len(failed)} failure(s).")
            messagebox.showinfo("Apply reach-stacker profile",
                                f"{report}\n\n{profile.MANUAL_NOTES}")
            self._read_all()

        self._run_async(lambda: profile.apply(enc, counting_direction=direction),
                        on_ok, "Applying reach-stacker profile ...")

    def _read_turns(self):
        """Show TOTAL_MEASURING_RANGE expressed as encoder-shaft revolutions."""
        enc = self._enc
        if enc is None:
            return

        def work():
            spr = enc.read_param(Param.STEPS_PER_REV)
            rng = enc.read_param(Param.TOTAL_MEASURING_RANGE)
            return rng, spr

        def on_ok(res):
            rng, spr = res
            if not spr:
                self.var_turns.set("?")
                return
            turns = rng / spr
            self.var_turns.set(f"{turns:g}")
            self._set_msg(f"Wraps after {turns:g} encoder turns "
                          f"(range {rng} ÷ {spr} steps/rev).")

        self._run_async(work, on_ok, "Reading turns before wrapping ...")

    def _write_turns(self):
        """Write TOTAL_MEASURING_RANGE from a turns value, with a guard dialog."""
        enc = self._enc
        if enc is None:
            return
        raw = self.var_turns.get().strip()
        try:
            turns = float(raw)
        except ValueError:
            messagebox.showwarning("Invalid value", f"'{raw}' is not a number.")
            return
        if not messagebox.askyesno(
                "Confirm write: turns before wrapping",
                "Changing this parameter may cause the math of the tophandler "
                "to work improperly. Proceed?",
                icon="warning"):
            return

        def work():
            spr = enc.read_param(Param.STEPS_PER_REV)
            rng = int(round(turns * spr))
            enc.write_param(Param.TOTAL_MEASURING_RANGE, rng)
            return rng

        def on_ok(rng):
            self._set_msg(f"Wrote measuring range = {rng} counts ({turns:g} turns). Re-reading ...")
            self._read_turns()
            self._read_all()

        self._run_async(work, on_ok, f"Writing {turns:g} turns ...")

    def _read_one(self, param: Param):
        enc = self._enc
        if enc is None:
            return

        def on_ok(val):
            self._param_rows[param]["cur"].set(self._format_value(param, val))
            self._set_msg(f"{param.name} = {val}")

        self._run_async(lambda: enc.read_param(param), on_ok, f"Reading {param.name} ...")

    def _write_one(self, param: Param):
        enc = self._enc
        if enc is None:
            return
        raw = self._param_rows[param]["new"].get().strip()
        try:
            value = int(raw, 0)
        except ValueError:
            messagebox.showwarning("Invalid value", f"'{raw}' is not an integer.")
            return
        if param in RISKY_PARAMS and not self._confirm_risky(param, value):
            return

        def on_ok(_):
            self._set_msg(f"Wrote {param.name} = {value}. Re-reading ...")
            self._read_one(param)

        self._run_async(lambda: enc.write_param(param, value), on_ok,
                        f"Writing {param.name} = {value} ...")

    def _confirm_risky(self, param: Param, value: int) -> bool:
        warn = {
            Param.BAUD_RATE: ("Changing the baud rate will drop the encoder off "
                              "the bus until the master also switches to the new "
                              "rate (and usually needs a power cycle)."),
            Param.NODE_ADDRESS: ("Changing the node address means the encoder will "
                                 "respond at a different address; update 'Encoder "
                                 "addr' and reconnect afterwards."),
        }[param]
        return messagebox.askyesno(
            f"Confirm write: {param.name}",
            f"Set {param.name} = {value}?\n\n{warn}\n\nProceed?")

    def _do_preset(self):
        enc = self._enc
        if enc is None:
            return
        try:
            value = int(self.var_preset.get().strip(), 0)
        except ValueError:
            messagebox.showwarning("Invalid value", "Preset must be an integer.")
            return
        if not messagebox.askyesno("Confirm preset",
                                   f"Set the current position to {value}?"):
            return
        self._run_async(lambda: enc.preset(value),
                        lambda _: self._set_msg(f"Preset to {value}."),
                        f"Presetting position to {value} ...")

    def _do_power_cycle(self):
        enc = self._enc
        if enc is None:
            return
        if not messagebox.askyesno("Confirm power cycle",
                                   "Trigger an encoder reset / power cycle?"):
            return
        self._run_async(enc.power_cycle,
                        lambda _: self._set_msg("Power cycle sent."),
                        "Sending power cycle ...")

    def _do_factory_reset(self):
        enc = self._enc
        if enc is None:
            return
        if not messagebox.askyesno(
                "Confirm FACTORY RESET",
                "This restores ALL parameters to factory defaults and cannot be "
                "undone.\n\nProceed?", icon="warning"):
            return
        self._run_async(enc.factory_reset,
                        lambda _: (self._set_msg("Factory reset sent."), self._read_all()),
                        "Sending factory reset ...")

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _format_value(param: Param, val: int) -> str:
        if param == Param.SPEED_FORMAT:
            try:
                return f"{val} ({SPEED_UNIT_LABELS[SpeedFormat(val)]})"
            except ValueError:
                return str(val)
        if param == Param.COUNTING_DIRECTION:
            return f"{val} ({'CCW' if val == 1 else 'CW'})"
        return str(val)

    def _set_msg(self, text: str):
        self.var_msg.set(text)

    def _on_close(self):
        if self._enc is not None:
            try:
                self._enc.close()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def main():
    EncoderGui().mainloop()


if __name__ == "__main__":
    main()
