#!/usr/bin/env python3
"""Show CPU and GPU temperatures on SteelSeries OLED screens.

    python3 temps.py                  # run until Ctrl+C
    python3 temps.py --fahrenheit
    python3 temps.py --no-rgb         # screen only, leave the key lighting alone
    python3 temps.py --print          # just print readings; no SteelSeries gear needed
    python3 temps.py --once           # print one reading and exit (check your sensors)

The OLED shows two lines, for example:

    CPU 54°C  23%
    GPU 61°C  40%

and the keyboard's function-key row shades from green to red as the CPU
heats up (between --cool and --hot degrees).
"""
import argparse
import signal
import sys
import time

import gamesense
import sensors


def fmt_temp(celsius, fahrenheit=False, ascii_only=False):
    if celsius is None:
        return "--"
    value = celsius * 9 / 5 + 32 if fahrenheit else celsius
    unit = "F" if fahrenheit else "C"
    return f"{value:.0f}{'' if ascii_only else '°'}{unit}"


def fmt_load(percent):
    return "--" if percent is None else f"{percent:.0f}%"


def screen_lines(stats, fahrenheit=False, ascii_only=False):
    """The two OLED lines. Without a GPU reading, line 2 shows RAM use."""
    line1 = f"CPU {fmt_temp(stats.cpu_temp, fahrenheit, ascii_only)}  {fmt_load(stats.cpu_load)}"
    if stats.gpu_temp is None and stats.gpu_load is None:
        line2 = f"RAM {fmt_load(stats.ram)}"
    else:
        line2 = f"GPU {fmt_temp(stats.gpu_temp, fahrenheit, ascii_only)}  {fmt_load(stats.gpu_load)}"
    return line1, line2


def heat_value(celsius, cool=40.0, hot=90.0):
    """Map a temperature onto 0-100 for the color gradient (cool=green, hot=red)."""
    if celsius is None:
        return 0
    fraction = (celsius - cool) / (hot - cool)
    return round(100 * min(1.0, max(0.0, fraction)))


class Display:
    """Sends readings to GameSense, re-registering if GG restarts."""

    def __init__(self, rgb=True, client_factory=gamesense.GameSense):
        self.rgb = rgb
        self.client_factory = client_factory
        self.client = None
        self.last_value = None

    def connect(self):
        self.client = self.client_factory()
        self.client.register(self.rgb)
        self.last_value = None

    def show(self, value, line1, line2):
        # GG can skip an event whose value didn't change, which would freeze
        # the screen text. Nudging the value by 1 keeps every update visible
        # without a noticeable change in color.
        if value == self.last_value:
            value = value + 1 if value < 100 else value - 1
        if self.client is None:
            self.connect()
        try:
            self.client.send(value, line1, line2)
        except gamesense.GameSenseError:
            # GG restarted (new port) or dropped our registration: start over once.
            self.connect()
            self.client.send(value, line1, line2)
        self.last_value = value

    def close(self):
        if self.client:
            try:
                self.client.stop()
            except gamesense.GameSenseError:
                pass


def run(args, read=None, display=None, sleep=time.sleep, ticks=None):
    read = read or sensors.read_stats
    display = display or Display(rgb=not args.no_rgb)
    warned_temp = False
    waiting = False
    count = 0
    try:
        while ticks is None or count < ticks:
            count += 1
            stats = read()
            line1, line2 = screen_lines(stats, args.fahrenheit, args.ascii)
            if stats.cpu_temp is None and not warned_temp:
                print("No CPU temperature found. " + sensors.temperature_help(), file=sys.stderr)
                warned_temp = True
            if args.print:
                print(f"{line1}   |   {line2}", flush=True)
            else:
                try:
                    display.show(heat_value(stats.cpu_temp, args.cool, args.hot), line1, line2)
                    if waiting:
                        print("Connected to SteelSeries GG.", file=sys.stderr)
                        waiting = False
                    print(f"\r{line1}   |   {line2}      ", end="", flush=True)
                except gamesense.GameSenseError as exc:
                    if not waiting:
                        print(f"\n{exc} Retrying every few seconds...", file=sys.stderr)
                        waiting = True
                    display.client = None
                    sleep(4)
            sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if not args.print:
            display.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between updates (default: 1)")
    ap.add_argument("--fahrenheit", "-F", action="store_true", help="show °F instead of °C")
    ap.add_argument("--no-rgb", action="store_true", help="don't color the function keys")
    ap.add_argument("--cool", type=float, default=40.0, help="°C shown as fully green (default: 40)")
    ap.add_argument("--hot", type=float, default=90.0, help="°C shown as fully red (default: 90)")
    ap.add_argument("--ascii", action="store_true", help="write 54C instead of 54°C")
    ap.add_argument("--print", action="store_true", help="print readings instead of sending them to GG")
    ap.add_argument("--once", action="store_true", help="print one reading and exit")
    args = ap.parse_args(argv)
    if args.hot <= args.cool:
        ap.error("--hot must be higher than --cool")
    if args.interval < 0.2:
        ap.error("--interval must be at least 0.2 seconds")

    # Closing the app any way (Ctrl+C, kill, logging out) goes through the same
    # clean shutdown, which hands the OLED back to GG.
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    # psutil measures CPU load between calls, so prime it once.
    sensors.read_psutil()
    if args.once:
        time.sleep(0.5)                  # give psutil a moment to measure load
        args.print = True
        run(args, ticks=1, sleep=lambda s: None)
        return
    if not args.print:
        print("Sending temperatures to SteelSeries GG. Press Ctrl+C to stop.", file=sys.stderr)
    run(args)


if __name__ == "__main__":
    main()
