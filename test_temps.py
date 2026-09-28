"""Tests for the GameSense client, the sensor readers and the display loop.

    python3 -m unittest -v

A fake GameSense server stands in for SteelSeries GG, so no SteelSeries
hardware or software is needed.
"""
import argparse
import json
import os
import tempfile
import threading
import unittest
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import gamesense
import sensors
import temps
from sensors import Stats


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


if __name__ == "__main__":
    unittest.main()
