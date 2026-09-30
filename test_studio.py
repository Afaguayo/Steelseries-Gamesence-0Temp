"""Tests for the settings window, OLED designs, Spotify and the engine.

    python3 -m unittest -v

No SteelSeries hardware, Spotify or display is needed: GG is the fake server
from test_temps, Spotify and the sensors are stand-ins, and the window test
is skipped when there's no display.
"""
import copy
import datetime
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw

import engine
import gamesense
import render
import settings as settings_mod
import spotify
import startup
from sensors import SensorReader, Stats
from test_temps import FakeGameSense


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def make_gif(path, frames=4, size=(60, 30), duration=50):
    images = []
    for i in range(frames):
        im = Image.new("RGB", size, (0, 0, 0))
        ImageDraw.Draw(im).rectangle((i * 10, 5, i * 10 + 12, 20), fill=(255, 255, 255))
        images.append(im)
    images[0].save(path, save_all=True, append_images=images[1:], duration=duration, loop=0)
    return path


# ------------------------------------------------------------------ Spotify

class SpotifyTests(unittest.TestCase):
    def test_window_title(self):
        self.assertEqual(spotify.parse_window_title("Daft Punk - One More Time"),
                         spotify.Track("One More Time", "Daft Punk", True))
        self.assertEqual(spotify.parse_window_title("AC/DC - Back In Black - Remastered"),
                         spotify.Track("Back In Black - Remastered", "AC/DC", True))
        self.assertEqual(spotify.parse_window_title("Spotify Premium"), spotify.Track(playing=False))
        self.assertEqual(spotify.parse_window_title("Advertisement").title, "Advertisement")
        self.assertIsNone(spotify.parse_window_title(""))

    def test_osascript(self):
        track = spotify.parse_osascript("playing\nOne More Time\nDaft Punk\n12,5\n320000\n")
        self.assertEqual((track.title, track.artist, track.playing), ("One More Time", "Daft Punk", True))
        self.assertEqual((track.position, track.duration), (12.5, 320.0))
        self.assertEqual(spotify.parse_osascript("stopped"), spotify.Track(playing=False))
        self.assertIsNone(spotify.parse_osascript(""))

    def test_playerctl(self):
        track = spotify.parse_playerctl("Paused\tSong\tBand\t30000000\t200000000\n")
        self.assertEqual((track.title, track.playing, track.position, track.duration), ("Song", False, 30.0, 200.0))
        self.assertIsNone(spotify.parse_playerctl(""))

    def test_progress_moves_only_while_playing(self):
        playing = spotify.Track("a", "b", True, position=30, duration=120, at=10)
        self.assertEqual(playing.elapsed(now=40), 60)
        self.assertEqual(playing.progress(now=40), 0.5)
        self.assertEqual(playing.elapsed(now=1000), 120)            # never past the end
        paused = spotify.Track("a", "b", False, position=30, duration=120, at=10)
        self.assertEqual(paused.elapsed(now=40), 30)
        self.assertIsNone(spotify.Track("a").progress())
        self.assertEqual(spotify.fmt_time(185), "3:05")

    def test_reader_is_throttled_and_survives_errors(self):
        calls, clock = [], Clock()

        def backend():
            calls.append(clock.t)
            if len(calls) == 2:
                raise OSError("Spotify went away")
            return spotify.Track("Song", playing=True)

        reader = spotify.SpotifyReader(poll_every=1.0, backend=backend, clock=clock)
        self.assertEqual(reader.read().title, "Song")
        clock.t += 0.5
        self.assertEqual(reader.read().title, "Song")               # cached, not asked again
        self.assertEqual(len(calls), 1)
        clock.t += 0.6
        self.assertIsNone(reader.read())                            # the error reads as "not playing"
        self.assertEqual(len(calls), 2)

    def test_threaded_reader_never_waits_for_spotify(self):
        def slow():
            time.sleep(0.4)
            return spotify.Track("Song", playing=True)

        reader = spotify.SpotifyReader(poll_every=0.05, backend=slow, threaded=True)
        try:
            started = time.perf_counter()
            self.assertIsNone(reader.read())                        # answers at once, before Spotify does
            self.assertLess(time.perf_counter() - started, 0.05)
            deadline = time.time() + 3
            while reader.read() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(reader.read().title, "Song")
        finally:
            reader.close()
        self.assertFalse(reader.thread.is_alive())

    def test_failed_read_backs_off(self):
        calls, clock = [], Clock()
        reader = spotify.SpotifyReader(poll_every=1.0, backend=lambda: calls.append(1) / 0, clock=clock)
        reader.read()
        clock.t += 2
        reader.read()                                               # still inside the back-off
        self.assertEqual(len(calls), 1)
        clock.t += spotify.ERROR_BACKOFF
        reader.read()
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_readers_run(self):
        # No Spotify on a CI machine: both readers must simply find nothing.
        self.assertIsNone(spotify.read_window_title())
        media = spotify.WindowsMedia()
        if media.ok:
            self.assertIsNone(media.read())
        media.close()


# ------------------------------------------------------------------ drawing

def design(**changes):
    d = copy.deepcopy(settings_mod.DEFAULTS["designs"]["clock"])
    d.update(changes)
    return d


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.gif = make_gif(os.path.join(self.dir.name, "anim.gif"))

    def tearDown(self):
        self.dir.cleanup()

    def test_bitmap_size_for_every_screen(self):
        layout = render.clock_layout(datetime.datetime(2026, 9, 29, 21, 5, 7), {})
        for size in render.SIZES:
            image = render.Renderer(size).draw(layout, design(), 0)
            self.assertEqual((image.size, image.mode), (size, "1"))
            self.assertEqual(len(render.to_bytes(image)), size[0] * size[1] // 8)

    def test_bytes_are_rows_msb_first(self):
        image = Image.new("1", (128, 36), 0)
        image.putpixel((0, 0), 1)
        image.putpixel((9, 1), 1)
        data = render.to_bytes(image)
        self.assertEqual(data[0], 0b10000000)
        self.assertEqual(data[16 + 1], 0b01000000)                  # row 1, second byte

    def test_clock_text(self):
        t = datetime.datetime(2026, 9, 29, 0, 5, 7)
        self.assertEqual([l.text for l in render.clock_layout(t, {}).lines], ["12:05:07 AM", "Tue Sep 29"])
        self.assertEqual(render.clock_layout(t, {"hour24": True, "seconds": False, "date": False}).lines[0].text,
                         "00:05")

    def test_temps_and_spotify_text(self):
        lines = render.temps_layout(Stats(cpu_temp=54.4, cpu_load=23, ram=40), {"fahrenheit": True}).lines
        self.assertEqual([l.text for l in lines], ["CPU 130°F 23%", "RAM 40%"])
        track = spotify.Track("Song", "Band", False, 65, 200, at=0)
        layout = render.spotify_layout(track, {"time": True, "progress": True}, now=0)
        self.assertEqual([l.text for l in layout.lines], ["Song", "Band", "paused 1:05 / 3:20"])
        self.assertAlmostEqual(layout.progress, 0.325)
        self.assertEqual(render.spotify_layout(None, {}).lines[1].text, "not playing")

    def test_gif_background_is_animated_and_dithered(self):
        renderer = render.Renderer((128, 40))
        d = design(background="gif", gif=self.gif, date=False)
        background = renderer.background(d)
        self.assertEqual((len(background.frames), background.total_ms), (4, 200))
        self.assertTrue(background.animated)
        self.assertAlmostEqual(background.next_change(0.01), 0.04)
        layout = render.clock_layout(datetime.datetime(2026, 1, 1), d)
        self.assertNotEqual(render.to_bytes(renderer.draw(layout, d, 0.0)),
                            render.to_bytes(renderer.draw(layout, d, 0.06)))

    def test_missing_gif_falls_back_to_plain(self):
        background = render.Background("gif", (128, 40), os.path.join(self.dir.name, "gone.gif"))
        self.assertEqual(background.kind, "none")
        self.assertIn("gone.gif", background.error)
        self.assertIsNone(background.next_change(0))

    def test_generated_backgrounds_move(self):
        for kind in render.ANIMATED:
            background = render.Background(kind, (128, 40))
            self.assertNotEqual(background.image(0).tobytes(), background.image(0.5).tobytes(), kind)

    def test_text_shrinks_to_fit_and_long_lines_scroll(self):
        renderer = render.Renderer((128, 36))
        layout = render.Layout([render.Line("Big", "large"), render.Line("Big", "large")], progress=0.5)
        sizes, heights = renderer.fit(layout, "Built-in")
        self.assertLessEqual(sum(heights) + 1 + 5, 36)
        self.assertNotEqual(sizes, ["large", "large"])
        long = render.Layout([render.Line("A very long song title that does not fit", "medium")])
        self.assertTrue(renderer.animated(long, design()))
        self.assertFalse(renderer.animated(render.Layout([render.Line("12:00", "medium")]), design()))
        self.assertNotEqual(renderer.draw(long, design(), 0).tobytes(), renderer.draw(long, design(), 1).tobytes())

    def test_invert(self):
        blank = render.Layout([])
        image = render.Renderer((128, 40)).draw(blank, design(invert=True), 0)
        self.assertEqual(set(image.tobytes()), {255})


# --------------------------------------------------------------- GameSense

class BitmapGameSenseTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGameSense()
        self.client = gamesense.GameSense(self.fake.address)

    def tearDown(self):
        self.client.close()
        self.fake.close()

    def test_register_screens_binds_every_size(self):
        self.client.register_screens(rgb=True)
        self.assertEqual(self.fake.endpoints(), ["game_metadata", "bind_game_event", "bind_game_event"])
        screen = self.fake.calls[1][1]
        self.assertEqual(screen["event"], "SCREEN")
        self.assertTrue(screen["value_optional"])
        types = [h["device-type"] for h in screen["handlers"]]
        self.assertEqual(types, ["screened-128x36", "screened-128x40", "screened-128x48", "screened-128x52"])
        first = screen["handlers"][0]["datas"][0]
        self.assertEqual((first["has-text"], len(first["image-data"])), (False, 576))
        self.assertEqual(self.fake.calls[2][1]["event"], "HEAT")

    def test_send_bitmap_and_heat(self):
        self.client.send_bitmap((128, 40), [1] * 640, 1)
        self.client.send_heat(55)
        bitmap, heat = self.fake.calls[0][1], self.fake.calls[1][1]
        self.assertEqual(list(bitmap["data"]["frame"]), ["image-data-128x40"])
        self.assertEqual(len(bitmap["data"]["frame"]["image-data-128x40"]), 640)
        self.assertEqual((heat["event"], heat["data"]["value"]), ("HEAT", 55))

    def test_connection_is_reused_or_reopened(self):
        for i in range(3):
            self.client.send_heat(i)
        self.assertEqual(len(self.fake.calls), 3)


# ------------------------------------------------------------------ engine

class FakeSpotify:
    def __init__(self, track=None):
        self.track = track
        self.reads = 0
        self.poll_every = 1.0

    def read(self):
        self.reads += 1
        return self.track


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGameSense()
        self.clock = Clock()
        self.wall = datetime.datetime(2026, 9, 29, 21, 5, 7, 500_000)
        self.stats_reads = 0
        self.settings = settings_mod.merged(settings_mod.DEFAULTS, {})

    def tearDown(self):
        self.fake.close()

    def read_stats(self):
        self.stats_reads += 1
        return Stats(cpu_temp=65, cpu_load=10)

    def engine(self, spotify_reader=None, **settings):
        self.settings.update(settings)
        return engine.Engine(self.settings, client_factory=lambda: gamesense.GameSense(self.fake.address),
                             stats_reader=self.read_stats, spotify_reader=spotify_reader,
                             clock=self.clock, wall=lambda: self.wall)

    def events(self, name="SCREEN"):
        return [body for ep, body in self.fake.calls if ep == "game_event" and body["event"] == name]

    def test_clock_sends_once_per_change_and_sleeps_to_next_second(self):
        oled = self.engine()
        delay = oled.step()
        self.assertAlmostEqual(delay, 0.52)
        self.assertEqual(self.fake.endpoints()[:2], ["game_metadata", "bind_game_event"])
        self.clock.t += 0.5
        oled.step()                                                   # same second: nothing new to send
        self.assertEqual(len(self.events()), 1)
        self.wall = self.wall.replace(second=8)
        oled.step()
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.stats_reads, 0)                         # a clock never reads sensors

    def test_keepalive_resends_an_unchanged_frame(self):
        oled = self.engine()
        oled.step()
        self.clock.t += engine.KEEPALIVE + 0.1
        oled.step()
        values = [e["data"]["value"] for e in self.events()]
        self.assertEqual(len(values), 2)
        self.assertNotEqual(values[0], values[1])                     # value flips so GG never skips it

    def test_spotify_takes_over_while_playing(self):
        reader = FakeSpotify(spotify.Track("Song", "Band", True, 10, 100, at=self.clock.t))
        oled = self.engine(spotify_reader=reader)
        oled.step()
        self.assertEqual(oled.screen, "spotify")
        self.assertEqual(reader.poll_every, engine.SPOTIFY_IDLE_POLL)
        reader.track = spotify.Track(playing=False)
        oled.step()
        self.assertEqual(oled.screen, "clock")

    def test_spotify_not_read_when_off(self):
        reader = FakeSpotify()
        oled = self.engine(spotify_reader=reader, spotify_auto=False)
        oled.step()
        self.assertEqual(reader.reads, 0)

    def test_temps_and_key_colors(self):
        oled = self.engine(show="temps", rgb=True)
        oled.step()
        heat = self.events("HEAT")
        self.assertEqual(heat[0]["data"]["value"], 50)
        self.clock.t += 0.3
        oled.step()
        self.assertEqual(self.stats_reads, 1)                         # sensors read at most once a second
        self.assertEqual(len(self.events("HEAT")), 1)                 # unchanged color isn't resent

    def test_animation_rate_is_capped(self):
        oled = self.engine(max_fps=5)
        oled.apply(settings_mod.merged(self.settings, {"designs": {"clock": {"background": "stars"}}}))
        self.assertAlmostEqual(oled.step(), 0.2)

    def test_waits_quietly_while_gg_is_missing(self):
        def missing():
            raise gamesense.GameSenseError("SteelSeries GG isn't running")
        oled = engine.Engine(self.settings, client_factory=missing, clock=self.clock, wall=lambda: self.wall)
        self.assertAlmostEqual(oled.step(), engine.RETRY)
        self.assertIn("isn't running", oled.status)
        self.assertIsNotNone(oled.image)                              # still drawn for the preview

    def test_reregisters_after_gg_restart_and_releases_on_stop(self):
        oled = self.engine()
        oled.step()
        self.fake.fail_next = 1
        self.wall = self.wall.replace(second=9)
        oled.step()                                                   # fails: GG forgot us
        oled.step()                                                   # registers again and sends
        self.assertEqual(self.fake.endpoints()[-3:], ["game_metadata", "bind_game_event", "game_event"])
        oled.stop()
        oled.run()
        self.assertEqual(self.fake.endpoints()[-1], "stop_game")

    def test_preview_uses_sample_data(self):
        oled = self.engine(spotify_reader=FakeSpotify())
        image, sample = oled.preview("spotify")
        self.assertTrue(sample)
        self.assertEqual(image.size, (128, 40))
        self.assertEqual(self.fake.calls, [])                         # previews never go to GG


class SensorReaderTests(unittest.TestCase):
    def test_empty_sources_are_skipped_for_a_while(self):
        calls, clock = [], Clock()
        sources = [("empty", lambda: calls.append("empty") or Stats()),
                   ("psutil", lambda: calls.append("psutil") or Stats(cpu_load=5, ram=30))]
        with mock.patch("sensors.sources", return_value=sources):
            reader = SensorReader(retry=30, clock=clock)
            self.assertEqual(reader.read(), Stats(cpu_load=5, ram=30))
            reader.read()
            clock.t += 31
            reader.read()
        self.assertEqual(calls, ["empty", "psutil", "psutil", "empty", "psutil"])


# -------------------------------------------------------- settings, startup

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "settings.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_fill_gaps_and_other_keys_survive(self):
        import pawnio
        pawnio.save_settings({"pawnio_declined": True, "designs": {"clock": {"hour24": True}}}, self.path)
        s = settings_mod.load(self.path)
        self.assertTrue(s["designs"]["clock"]["hour24"])
        self.assertTrue(s["designs"]["clock"]["seconds"])            # filled from defaults
        s["show"] = "spotify"
        settings_mod.save(s, self.path)
        saved = pawnio.load_settings(self.path)
        self.assertEqual((saved["show"], saved["pawnio_declined"]), ("spotify", True))

    def test_import_media_copies_the_file(self):
        gif = make_gif(os.path.join(self.dir.name, "mine.gif"))
        stored = settings_mod.import_media(gif, self.path)
        self.assertTrue(stored.startswith(settings_mod.media_dir(self.path)))
        self.assertTrue(os.path.isfile(stored))

    def test_screen_size(self):
        self.assertEqual(settings_mod.screen_size({"screen": "128x52"}), (128, 52))
        self.assertEqual(settings_mod.screen_size({"screen": "junk"}), (128, 40))


class StartupTests(unittest.TestCase):
    def test_windows_task_runs_in_background_as_admin(self):
        seen = []
        ok = startup.set_enabled(True, [r"C:\Program Files\SS\SteelSeriesTemps.exe", "--background"],
                                 platform="win32", run=lambda cmd, **kw: seen.append(cmd) or mock.Mock(returncode=0))
        self.assertTrue(ok)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("/TR") + 1], '"C:\\Program Files\\SS\\SteelSeriesTemps.exe" --background')
        self.assertEqual(cmd[cmd.index("/RL") + 1], "HIGHEST")

    def test_mac_and_linux_files(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            for platform in ("darwin", "linux"):
                self.assertTrue(startup.set_enabled(True, ["/usr/bin/python3", "/a b/temps.py", "--background"],
                                                    platform=platform, home=home))
                self.assertTrue(startup.is_enabled(platform=platform, home=home))
                startup.set_enabled(False, platform=platform, home=home)
                self.assertFalse(startup.is_enabled(platform=platform, home=home))
            startup.set_enabled(True, ["/usr/bin/python3", "/a b/temps.py"], platform="linux", home=home)
            with open(startup.linux_desktop_path(home)) as f:
                self.assertIn('Exec=/usr/bin/python3 "/a b/temps.py"', f.read())


# ------------------------------------------------------------------ window

def have_display():
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return False
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(have_display(), "needs a display")
class WindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter
        import gui
        self.gui = gui
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "settings.json")
        self.settings = settings_mod.load(self.path)
        self.oled = engine.Engine(copy.deepcopy(self.settings), client_factory=self.no_gg)
        self.startup = mock.Mock(is_enabled=mock.Mock(return_value=False), set_enabled=mock.Mock(return_value=True))
        self.root = tkinter.Tk()
        self.app = gui.App(self.root, self.oled, self.settings, self.path, tray=False, startup_mod=self.startup)

    def tearDown(self):
        self.root.destroy()
        self.dir.cleanup()

    @staticmethod
    def no_gg():
        raise gamesense.GameSenseError("no GG in tests")

    def labels(self, widget):
        found = []
        for child in widget.winfo_children():
            try:
                found.append(child.cget("text"))
            except Exception:
                pass
            found += self.labels(child)
        return found

    def test_has_tabs_preview_and_footer(self):
        tabs = [self.app.notebook.tab(i, "text") for i in range(self.app.notebook.index("end"))]
        self.assertEqual(tabs, ["General", "Clock", "Temps", "Spotify"])
        self.assertIn("made by frijolito trash with love", self.labels(self.root))
        self.app.refresh_preview(force=True)
        self.assertEqual((self.app.photo.width(), self.app.photo.height()), (384, 120))
        for index in range(1, 4):
            self.app.notebook.select(index)
            self.app.refresh_preview(force=True)
            self.assertIn("Preview", self.app.status.cget("text"))

    def test_changes_apply_live_and_are_saved(self):
        self.app.set(("designs", "clock", "hour24"), True)
        self.app.set(("show",), "spotify")
        self.assertTrue(self.oled.settings["designs"]["clock"]["hour24"])
        self.assertEqual(self.oled.settings["show"], "spotify")
        self.app.save()
        self.assertEqual(settings_mod.load(self.path)["show"], "spotify")

    def test_autostart_question_is_asked_once(self):
        with mock.patch.object(self.gui.messagebox, "askyesno", return_value=True) as ask:
            self.app.ask_autostart()
            self.app.ask_autostart()
        self.assertEqual(ask.call_count, 1)
        self.startup.set_enabled.assert_called_once_with(True)
        self.assertTrue(settings_mod.load(self.path)["autostart_asked"])

    def test_choose_gif(self):
        gif = make_gif(os.path.join(self.dir.name, "cool.gif"))
        tab_vars = mock.Mock()
        with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=gif):
            self.app.choose_gif("clock", tab_vars, mock.Mock())
        d = self.oled.settings["designs"]["clock"]
        self.assertTrue(d["gif"].endswith(".gif"))
        self.assertTrue(os.path.isfile(d["gif"]))
        tab_vars.set.assert_called_with("My GIF / image")


if __name__ == "__main__":
    unittest.main()
