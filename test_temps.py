"""Tests for the GameSense client, the sensor readers and the display loop.

    python3 -m unittest -v

A fake GameSense server stands in for SteelSeries GG, so no SteelSeries
hardware or software is needed.
"""
import argparse
import datetime
import json
import os
import tempfile
import threading
import unittest
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import gamesense
import pawnio
import sensors
import struct
import sys
import temps
from sensors import Reading, Stats


class FakeGameSense:
    """Records every POST like GG would receive it. Can be told to fail."""

    def __init__(self):
        self.calls = []
        self.fail_next = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append((self.path.lstrip("/"), body))
                if fake.fail_next:
                    fake.fail_next -= 1
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":"Game not registered"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def endpoints(self):
        return [endpoint for endpoint, _ in self.calls]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GameSenseTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGameSense()
        self.client = gamesense.GameSense(self.fake.address)

    def tearDown(self):
        self.fake.close()

    def test_register_sends_metadata_and_binding(self):
        self.client.register(rgb=True)
        (ep1, meta), (ep2, bind) = self.fake.calls
        self.assertEqual((ep1, ep2), ("game_metadata", "bind_game_event"))
        self.assertEqual(meta["game"], "SYSTEM_TEMPS")
        self.assertEqual(bind["event"], "STATS")
        kinds = [h["device-type"] for h in bind["handlers"]]
        self.assertEqual(kinds, ["screened", "rgb-per-key-zones"])
        lines = bind["handlers"][0]["datas"][0]["lines"]
        self.assertEqual([l["context-frame-key"] for l in lines], ["line1", "line2"])

    def test_register_without_rgb(self):
        self.client.register(rgb=False)
        bind = self.fake.calls[1][1]
        self.assertEqual([h["device-type"] for h in bind["handlers"]], ["screened"])

    def test_send_event(self):
        self.client.send(42, "CPU 54°C  23%", "GPU 61°C  40%")
        endpoint, body = self.fake.calls[0]
        self.assertEqual(endpoint, "game_event")
        self.assertEqual(body["data"], {"value": 42, "frame": {"line1": "CPU 54°C  23%",
                                                               "line2": "GPU 61°C  40%"}})

    def test_stop_and_remove(self):
        self.client.stop()
        self.client.remove()
        self.assertEqual(self.fake.endpoints(), ["stop_game", "remove_game"])

    def test_http_error_becomes_gamesense_error(self):
        self.fake.fail_next = 1
        with self.assertRaisesRegex(gamesense.GameSenseError, "Game not registered"):
            self.client.send(1, "a", "b")

    def test_unreachable_server(self):
        client = gamesense.GameSense("127.0.0.1:9", timeout=0.5)
        with self.assertRaisesRegex(gamesense.GameSenseError, "Can't reach"):
            client.send(1, "a", "b")


class CorePropsTests(unittest.TestCase):
    def test_reads_address_from_first_valid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = os.path.join(folder, "broken.json")
            good = os.path.join(folder, "coreProps.json")
            with open(broken, "w") as f:
                f.write("not json")
            with open(good, "w") as f:
                json.dump({"address": "127.0.0.1:51234", "encrypted_address": "x"}, f)
            missing = os.path.join(folder, "missing.json")
            self.assertEqual(gamesense.find_address([missing, broken, good]), "127.0.0.1:51234")

    def test_missing_gg_is_explained(self):
        with self.assertRaisesRegex(gamesense.GameSenseError, "isn't running"):
            gamesense.find_address([])

    def test_paths_per_platform(self):
        with mock.patch.object(gamesense.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"PROGRAMDATA": "C:\\ProgramData"}):
            self.assertTrue(gamesense.core_props_paths()[0].endswith("coreProps.json"))
            self.assertIn("SteelSeries Engine 3", gamesense.core_props_paths()[0])
        with mock.patch.object(gamesense.sys, "platform", "darwin"):
            self.assertTrue(gamesense.core_props_paths()[0].startswith("/Library/Application Support"))


# A trimmed copy of LibreHardwareMonitor's data.json.
LHM_SAMPLE = {"id": 0, "Text": "Sensor", "Children": [{
    "id": 1, "Text": "DESKTOP", "ImageURL": "images_icon/computer.png", "Children": [
        {"Text": "AMD Ryzen 7 5800X", "ImageURL": "images_icon/cpu.png", "Children": [
            {"Text": "Temperatures", "ImageURL": "images/temperature.png", "Children": [
                {"Text": "Core (Tctl/Tdie)", "Value": "63,4 °C", "Children": []},
                {"Text": "CCD1 (Tdie)", "Value": "58.1 °C", "Children": []}]},
            {"Text": "Load", "ImageURL": "images/load.png", "Children": [
                {"Text": "CPU Core #1", "Value": "80.0 %", "Children": []},
                {"Text": "CPU Total", "Value": "31.5 %", "Children": []}]}]},
        {"Text": "NVIDIA GeForce RTX 3070", "ImageURL": "images_icon/nvidia.png", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "GPU Hot Spot", "Value": "75.0 °C", "Children": []},
                {"Text": "GPU Core", "Value": "66.0 °C", "Children": []}]},
            {"Text": "Load", "Children": [
                {"Text": "GPU Core", "Value": "97.0 %", "Children": []}]}]},
        {"Text": "Generic Memory", "ImageURL": "images_icon/ram.png", "Children": [
            {"Text": "Load", "Children": [{"Text": "Memory", "Value": "48.2 %", "Children": []}]}]},
    ]}]}

Temp = namedtuple("Temp", "label current high critical")


class SensorTests(unittest.TestCase):
    def test_number(self):
        self.assertEqual(sensors.number("54.0 °C"), 54.0)
        self.assertEqual(sensors.number("63,4 °C"), 63.4)
        self.assertIsNone(sensors.number(""))
        self.assertIsNone(sensors.number(None))

    def test_parse_lhm(self):
        stats = sensors.parse_lhm(LHM_SAMPLE)
        self.assertEqual(stats, Stats(cpu_temp=63.4, cpu_load=31.5, gpu_temp=66.0, gpu_load=97.0, ram=48.2))

    def test_parse_lhm_empty(self):
        self.assertEqual(sensors.parse_lhm({"Children": []}), Stats())

    def test_read_lhm_when_not_running(self):
        self.assertEqual(sensors.read_lhm("http://127.0.0.1:9/data.json", timeout=0.3), Stats())

    def test_parse_nvidia_smi(self):
        self.assertEqual(sensors.parse_nvidia_smi("61, 34\n55, 0\n"), Stats(gpu_temp=61.0, gpu_load=34.0))
        self.assertEqual(sensors.parse_nvidia_smi(""), Stats())

    def test_parse_psutil_temps(self):
        intel = {"coretemp": [Temp("Core 0", 50, 80, 100), Temp("Package id 0", 57, 80, 100)]}
        self.assertEqual(sensors.parse_psutil_temps(intel), 57)
        amd = {"k10temp": [Temp("Tctl", 62.5, None, None), Temp("Tccd1", 55, None, None)]}
        self.assertEqual(sensors.parse_psutil_temps(amd), 62.5)
        pi = {"cpu_thermal": [Temp("", 48.3, None, None)]}
        self.assertEqual(sensors.parse_psutil_temps(pi), 48.3)
        self.assertIsNone(sensors.parse_psutil_temps({"nvme": [Temp("Composite", 40, 80, 90)]}))

    def test_merge_prefers_first_source(self):
        merged = Stats(cpu_temp=60).merge(Stats(cpu_temp=10, cpu_load=20))
        self.assertEqual((merged.cpu_temp, merged.cpu_load), (60, 20))

    def test_read_stats_runs_on_this_machine(self):
        stats = sensors.read_stats()
        self.assertIsInstance(stats, Stats)


class FormatTests(unittest.TestCase):
    def test_lines_with_gpu(self):
        stats = Stats(cpu_temp=54.4, cpu_load=23.2, gpu_temp=61, gpu_load=40, ram=50)
        self.assertEqual(temps.screen_lines(stats), ("CPU 54°C  23%", "GPU 61°C  40%"))

    def test_lines_without_gpu_show_ram(self):
        stats = Stats(cpu_temp=None, cpu_load=5, ram=48.6)
        self.assertEqual(temps.screen_lines(stats), ("CPU --  5%", "RAM 49%"))

    def test_fahrenheit_and_ascii(self):
        stats = Stats(cpu_temp=50, cpu_load=10)
        self.assertEqual(temps.screen_lines(stats, fahrenheit=True)[0], "CPU 122°F  10%")
        self.assertEqual(temps.screen_lines(stats, ascii_only=True)[0], "CPU 50C  10%")

    def test_lines_fit_small_screens(self):
        worst = Stats(cpu_temp=105, cpu_load=100, gpu_temp=110, gpu_load=100)
        for line in temps.screen_lines(worst, fahrenheit=True):
            self.assertLessEqual(len(line), 16)

    def test_heat_value(self):
        self.assertEqual(temps.heat_value(None), 0)
        self.assertEqual(temps.heat_value(30), 0)
        self.assertEqual(temps.heat_value(65), 50)
        self.assertEqual(temps.heat_value(95), 100)


class DisplayLoopTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGameSense()
        self.args = argparse.Namespace(interval=0, fahrenheit=False, ascii=False, no_rgb=False,
                                       cool=40.0, hot=90.0, print=False)

    def tearDown(self):
        self.fake.close()

    def display(self):
        return temps.Display(client_factory=lambda: gamesense.GameSense(self.fake.address))

    def test_loop_registers_sends_and_stops(self):
        readings = iter([Stats(cpu_temp=65, cpu_load=20), Stats(cpu_temp=66, cpu_load=21)])
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            temps.run(self.args, read=lambda: next(readings), display=self.display(),
                      sleep=lambda s: None, ticks=2)
        self.assertEqual(self.fake.endpoints(),
                         ["game_metadata", "bind_game_event", "game_event", "game_event", "stop_game"])
        first = self.fake.calls[2][1]["data"]
        self.assertEqual(first["value"], 50)
        self.assertEqual(first["frame"]["line1"], "CPU 65°C  20%")

    def test_repeated_value_is_nudged(self):
        display = self.display()
        display.show(50, "a", "b")
        display.show(50, "c", "d")
        values = [body["data"]["value"] for ep, body in self.fake.calls if ep == "game_event"]
        self.assertEqual(values, [50, 51])

    def test_reregisters_after_gg_restart(self):
        display = self.display()
        display.show(10, "a", "b")
        self.fake.fail_next = 1               # GG forgot us
        display.show(20, "c", "d")
        self.assertEqual(self.fake.endpoints()[-3:], ["game_metadata", "bind_game_event", "game_event"])

    def test_waits_while_gg_is_missing(self):
        def missing():
            raise gamesense.GameSenseError("SteelSeries GG isn't running")

        sleeps = []
        display = temps.Display(client_factory=missing)
        with mock.patch("sys.stdout"), mock.patch("sys.stderr") as err:
            temps.run(self.args, read=lambda: Stats(cpu_temp=50), display=display,
                      sleep=sleeps.append, ticks=2)
        self.assertIn(4, sleeps)
        printed = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertEqual(printed.count("isn't running"), 1)   # said once, not every retry

    def test_print_mode_needs_no_gamesense(self):
        self.args.print = True
        with mock.patch("builtins.print") as out, mock.patch("sys.stderr"):
            temps.run(self.args, read=lambda: Stats(cpu_temp=50, cpu_load=5, ram=30),
                      display=self.display(), sleep=lambda s: None, ticks=1)
        self.assertIn("CPU 50°C  5%", out.call_args_list[-1].args[0])
        self.assertEqual(self.fake.calls, [])

    def test_once_prints_one_reading(self):
        with mock.patch.object(temps.sensors, "read_stats", return_value=Stats(cpu_temp=50, cpu_load=5, ram=30)), \
                mock.patch("builtins.print") as out, mock.patch("sys.stderr"):
            temps.main(["--once"])
        self.assertEqual(out.call_count, 1)
        self.assertIn("CPU 50°C  5%", out.call_args.args[0])

    def test_cli_validation(self):
        with mock.patch("sys.stderr"):
            for bad in (["--hot", "30", "--cool", "40"], ["--interval", "0.01"]):
                with self.subTest(args=bad), self.assertRaises(SystemExit):
                    temps.main(bad)


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGameSense()
        self.args = argparse.Namespace(hour24=False, print=False)

    def tearDown(self):
        self.fake.close()

    def test_12_and_24_hour(self):
        t = datetime.datetime(2026, 9, 29, 21, 5, 7)
        self.assertEqual(temps.clock_lines(t), ("9:05:07 PM", "Tue Sep 29 2026"))
        self.assertEqual(temps.clock_lines(t, hour24=True), ("21:05:07", "Tue Sep 29 2026"))

    def test_midnight_and_noon(self):
        self.assertEqual(temps.clock_lines(datetime.datetime(2026, 1, 1, 0, 0, 0))[0], "12:00:00 AM")
        self.assertEqual(temps.clock_lines(datetime.datetime(2026, 1, 1, 12, 0, 0))[0], "12:00:00 PM")

    def test_lines_fit_small_screens(self):
        longest = datetime.datetime(2026, 12, 30, 23, 59, 59)
        for hour24 in (False, True):
            for line in temps.clock_lines(longest, hour24):
                self.assertLessEqual(len(line), 16)
                self.assertTrue(line.isascii())

    def test_clock_sends_time_without_touching_key_lights(self):
        times = iter(datetime.datetime(2026, 9, 29, 21, 5, s, 400_000) for s in range(10))
        display = temps.Display(rgb=False, client_factory=lambda: gamesense.GameSense(self.fake.address))
        sleeps = []
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            temps.run_clock(self.args, display=display, now=lambda: next(times),
                            sleep=sleeps.append, ticks=2)
        self.assertEqual(self.fake.endpoints(),
                         ["game_metadata", "bind_game_event", "game_event", "game_event", "stop_game"])
        handlers = self.fake.calls[1][1]["handlers"]
        self.assertEqual([h["device-type"] for h in handlers], ["screened"])
        frames = [body["data"]["frame"]["line1"] for ep, body in self.fake.calls if ep == "game_event"]
        self.assertEqual(frames, ["9:05:00 PM", "9:05:02 PM"])
        self.assertAlmostEqual(sleeps[0], 0.62)      # wakes just after the next second

    def test_clock_once_prints_and_needs_no_sensors(self):
        with mock.patch.object(temps.sensors, "read_stats") as read, \
                mock.patch.object(temps, "open_builtin") as builtin, \
                mock.patch("builtins.print") as out, mock.patch("sys.stderr"):
            temps.main(["--clock", "--24h", "--once"])
        read.assert_not_called()
        builtin.assert_not_called()
        self.assertEqual(out.call_count, 1)
        self.assertRegex(out.call_args.args[0], r"^\d\d:\d\d:\d\d   \|   \w{3} \w{3} \d+ \d{4}$")


# LibreHardwareMonitor 0.9.x data.json: hardware carries HardwareId, sensors
# carry Type/SensorId/RawValue. Unknown hardware types get the CPU icon, so
# the id must win over the icon.
LHM_NEW_SAMPLE = {"id": 0, "Text": "Sensor", "ImageURL": "", "Children": [{
    "id": 1, "Text": "PC", "ImageURL": "images_icon/computer.png", "Children": [
        {"Text": "Intel Core i7-12700K", "HardwareId": "/intelcpu/0", "ImageURL": "images_icon/cpu.png", "Children": [
            {"Text": "Temperatures", "ImageURL": "images_icon/temperature.png", "Children": [
                {"Text": "Core Max", "Type": "Temperature", "SensorId": "/intelcpu/0/temperature/8",
                 "Value": "71,0 °C", "RawValue": 71.0, "Children": []},
                {"Text": "CPU Package", "Type": "Temperature", "SensorId": "/intelcpu/0/temperature/9",
                 "Value": "68,5 °C", "RawValue": 68.5, "Children": []}]},
            {"Text": "Load", "ImageURL": "images_icon/load.png", "Children": [
                {"Text": "CPU Total", "Type": "Load", "Value": "12,5 %", "RawValue": 12.5, "Children": []}]}]},
        {"Text": "Embedded Controller", "HardwareId": "/embeddedcontroller/0", "ImageURL": "images_icon/cpu.png",
         "Children": [{"Text": "Temperatures", "Children": [
             {"Text": "CPU Package", "Type": "Temperature", "Value": "99,0 °C", "RawValue": 99.0, "Children": []}]}]},
        {"Text": "Nuvoton NCT6798D", "HardwareId": "/lpc/nct6798d/0", "ImageURL": "images_icon/chip.png",
         "Children": [{"Text": "Temperatures", "Children": [
             {"Text": "CPU", "Type": "Temperature", "Value": "40,0 °C", "RawValue": 40.0, "Children": []}]}]},
        {"Text": "NVIDIA GeForce RTX 4070", "HardwareId": "/gpu-nvidia/0", "ImageURL": "images_icon/nvidia.png",
         "Children": [{"Text": "Temperatures", "Children": [
             {"Text": "GPU Core", "Type": "Temperature", "Value": "55,0 °C", "RawValue": 55.0, "Children": []}]}]},
        {"Text": "Total Memory", "HardwareId": "/ram", "ImageURL": "images_icon/ram.png", "Children": [
            {"Text": "Load", "Children": [
                {"Text": "Memory", "Type": "Load", "Value": "41,0 %", "RawValue": 41.0, "Children": []}]}]},
    ]}]}


def hwinfo_block(sensors_list, readings):
    """Build an HWiNFO shared-memory block like HWiNFO does (packed structs)."""
    sensor_size, reading_size = 264, 316
    off_s = 44
    off_r = off_s + sensor_size * len(sensors_list)
    data = bytearray(off_r + reading_size * len(readings))
    struct.pack_into("<4sIIqIIIIII", data, 0, b"HWiS", 2, 0, 0, off_s, sensor_size, len(sensors_list),
                     off_r, reading_size, len(readings))
    for i, name in enumerate(sensors_list):
        struct.pack_into("<II128s128s", data, off_s + i * sensor_size, i, 0, name.encode(), name.encode())
    for i, (rtype, sensor_index, label, value) in enumerate(readings):
        struct.pack_into("<III128s128s16sdddd", data, off_r + i * reading_size, rtype, sensor_index, i,
                         label.encode(), label.encode(), b"", value, value, value, value)
    return bytes(data)


def coretemp_block(temps, loads, tjmax=100, fahrenheit=0, delta=0, cpus=1):
    data = bytearray(sensors.CORETEMP_SIZE)
    struct.pack_into("<256I", data, 0, *(loads + [0] * (256 - len(loads))))
    struct.pack_into("<128I", data, 1024, *([tjmax] * 128))
    struct.pack_into("<II", data, 1536, len(temps) // cpus, cpus)
    struct.pack_into("<256f", data, 1544, *(temps + [0.0] * (256 - len(temps))))
    data[2684], data[2685] = fahrenheit, delta
    return bytes(data)


class NewSourceTests(unittest.TestCase):
    def test_lhm_new_format_uses_hardware_id(self):
        stats = sensors.parse_lhm(LHM_NEW_SAMPLE)
        self.assertEqual(stats, Stats(cpu_temp=68.5, cpu_load=12.5, gpu_temp=55.0, ram=41.0))

    def test_pick_prefers_named_sensors_and_skips_missing(self):
        stats = sensors.pick([Reading("cpu", "temp", "Core #1", 60), Reading("cpu", "temp", "CPU Package", None),
                              Reading("cpu", "temp", "Core Max", 64), Reading("cpu", "temp", "Tctl", float("nan"))])
        self.assertEqual(stats.cpu_temp, 64)

    def test_zero_or_impossible_temperature_is_missing(self):
        # LibreHardwareMonitor on a VM reports Tctl/Tdie as 0.0 when it can't read it.
        stats = sensors.pick([Reading("cpu", "temp", "Core (Tctl/Tdie)", 0.0), Reading("cpu", "load", "CPU Total", 6.1),
                              Reading("gpu", "temp", "GPU Core", 255.0)])
        self.assertEqual(stats, Stats(cpu_load=6.1))
        self.assertEqual(temps.screen_lines(stats)[0], "CPU --  6%")

    def test_hwinfo_intel_and_nvidia(self):
        data = hwinfo_block(
            ["CPU [#0]: Intel Core i7-12700K", "GPU [#0]: NVIDIA GeForce RTX 4070", "System: ASUS", "S.M.A.R.T.: SSD"],
            [(1, 0, "Core Max", 70.0), (1, 0, "CPU Package", 66.0), (7, 0, "Total CPU Usage", 18.0),
             (1, 1, "GPU Temperature", 52.0), (7, 1, "GPU Core Load", 35.0),
             (7, 2, "Physical Memory Load", 44.0), (1, 3, "Drive Temperature", 38.0), (3, 0, "CPU Fan", 1200.0)])
        self.assertEqual(sensors.pick(sensors.hwinfo_readings(data)),
                         Stats(cpu_temp=66.0, cpu_load=18.0, gpu_temp=52.0, gpu_load=35.0, ram=44.0))

    def test_hwinfo_amd_tctl(self):
        data = hwinfo_block(["CPU [#0]: AMD Ryzen 7 7800X3D: Enhanced"],
                            [(1, 0, "CPU (Tctl/Tdie)", 74.5), (1, 0, "CPU Die (average)", 70.0)])
        self.assertEqual(sensors.pick(sensors.hwinfo_readings(data)).cpu_temp, 74.5)

    def test_hwinfo_rejects_garbage(self):
        self.assertEqual(sensors.hwinfo_readings(b""), [])
        self.assertEqual(sensors.hwinfo_readings(b"XXXX" + bytes(60)), [])

    def test_coretemp(self):
        stats = sensors.coretemp_stats(coretemp_block([55.0, 61.0, 58.0, 57.0], [10, 20, 30, 40]))
        self.assertEqual((stats.cpu_temp, stats.cpu_load), (61.0, 25.0))

    def test_coretemp_distance_to_tjmax_and_fahrenheit(self):
        stats = sensors.coretemp_stats(coretemp_block([40.0, 35.0], [0, 0], tjmax=100, delta=1))
        self.assertEqual(stats.cpu_temp, 65.0)
        stats = sensors.coretemp_stats(coretemp_block([140.0, 131.0], [0, 0], fahrenheit=1))
        self.assertAlmostEqual(stats.cpu_temp, 60.0)

    def test_coretemp_not_running(self):
        self.assertEqual(sensors.coretemp_stats(None), Stats())
        self.assertEqual(sensors.coretemp_stats(bytes(sensors.CORETEMP_SIZE)), Stats())

    @unittest.skipIf(sys.platform == "win32", "checks the non-Windows fallback")
    def test_builtin_is_windows_only(self):
        builtin = sensors.BuiltinSensors()
        self.assertFalse(builtin.ok)
        self.assertEqual(builtin.read(), Stats())

    def test_sources_order(self):
        class Fake:
            ok = True
            read = staticmethod(lambda: Stats(cpu_temp=50))
        names = [name for name, _ in sensors.sources(Fake())]
        if sys.platform == "win32":
            self.assertTrue(names[0].startswith("built-in"))
        self.assertEqual(names[-1], "psutil")

    def test_read_stats_merges_in_order(self):
        with mock.patch.object(sensors, "sources", return_value=[
                ("a", lambda: Stats(cpu_temp=70)), ("b", lambda: Stats(cpu_temp=10, cpu_load=5, ram=30))]):
            self.assertEqual(sensors.read_stats(), Stats(cpu_temp=70, cpu_load=5, ram=30))


class PawnIOTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.settings = os.path.join(self.dir.name, "settings.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_yes_installs(self):
        calls = []
        with mock.patch("builtins.print"):
            ok = pawnio.offer_install(ask=lambda q: "", settings_path=self.settings,
                                      installer=lambda: calls.append(1) or True)
        self.assertTrue(ok)
        self.assertEqual(calls, [1])

    def test_no_is_remembered(self):
        asked = []
        with mock.patch("builtins.print"):
            pawnio.offer_install(ask=lambda q: asked.append(q) or "n", settings_path=self.settings,
                                 installer=lambda: self.fail("should not install"))
            pawnio.offer_install(ask=lambda q: asked.append(q) or "y", settings_path=self.settings,
                                 installer=lambda: self.fail("should not ask again"))
        self.assertEqual(len(asked), 1)
        self.assertTrue(pawnio.load_settings(self.settings)["pawnio_declined"])

    def test_install_runs_setup_with_install_flag(self):
        ran = []
        with mock.patch("urllib.request.urlretrieve"), mock.patch("builtins.print"):
            ok = pawnio.install(run=lambda cmd: ran.append(cmd) or mock.Mock(returncode=0))
        self.assertTrue(ok)
        self.assertEqual(ran[0][1], "-install")
        self.assertTrue(ran[0][0].endswith("PawnIO_setup.exe"))

    @unittest.skipIf(sys.platform == "win32", "PawnIO can really be installed on Windows")
    def test_not_installed_off_windows(self):
        self.assertIsNone(pawnio.installed_version())


class AutostartTests(unittest.TestCase):
    def test_on_creates_elevated_logon_task(self):
        seen = []
        ok = temps.autostart(True, exe=r"C:\Apps\SteelSeriesTemps.exe",
                             run=lambda cmd, **kw: seen.append(cmd) or mock.Mock(returncode=0))
        self.assertTrue(ok)
        cmd = seen[0]
        self.assertEqual(cmd[:4], ["schtasks", "/Create", "/TN", "SteelSeriesTemps"])
        self.assertIn('"C:\\Apps\\SteelSeriesTemps.exe" --background', cmd)   # starts quietly in the tray
        self.assertEqual(cmd[cmd.index("/SC") + 1], "ONLOGON")
        self.assertEqual(cmd[cmd.index("/RL") + 1], "HIGHEST")

    def test_off_deletes_task(self):
        seen = []
        temps.autostart(False, run=lambda cmd, **kw: seen.append(cmd) or mock.Mock(returncode=0))
        self.assertEqual(seen[0], ["schtasks", "/Delete", "/TN", "SteelSeriesTemps", "/F"])


class DiagnoseTests(unittest.TestCase):
    def test_diagnose_reports_sources_and_help(self):
        with mock.patch.object(sensors, "sources", return_value=[("psutil", lambda: Stats(cpu_load=5, ram=30))]), \
                mock.patch("builtins.print") as out:
            temps.diagnose(None)
        text = "\n".join(str(c.args[0]) if c.args else "" for c in out.call_args_list)
        self.assertIn("psutil", text)
        self.assertIn("Combined: cpu_load=5.0, ram=30.0", text)
        self.assertIn("No CPU temperature", text)

    def test_diagnose_success(self):
        with mock.patch.object(sensors, "sources", return_value=[("x", lambda: Stats(cpu_temp=60))]), \
                mock.patch("builtins.print") as out:
            temps.diagnose(None)
        self.assertIn("CPU temperature is working.", out.call_args_list[-1].args[0])


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("LHM_DIR"),
                     "Windows with LHM_DIR pointing at LibreHardwareMonitor's DLLs")
class BuiltinWindowsTests(unittest.TestCase):
    """Loads the real LibreHardwareMonitorLib.dll (run in CI on Windows)."""

    def test_library_loads_and_finds_hardware(self):
        builtin = sensors.BuiltinSensors(os.environ["LHM_DIR"])
        try:
            self.assertTrue(builtin.ok, builtin.error)
            self.assertTrue(any(name.startswith("Cpu") for name in builtin.hardware_names), builtin.hardware_names)
            readings = builtin.readings()
            print("\nhardware:", builtin.hardware_names)
            print("readings:", readings)
            print("picked:", builtin.read())
            self.assertTrue(any(r.kind == "cpu" and r.group == "load" and r.value is not None for r in readings))
        finally:
            builtin.close()


if __name__ == "__main__":
    unittest.main()
