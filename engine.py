"""Keeps the OLED up to date with the chosen screen, using as little CPU as it can.

- It sleeps until the picture next changes: once a second for a clock or
  temperatures, and only as fast as a GIF's own frame timing (capped by
  max_fps) for animations and scrolling text.
- A frame identical to the last one isn't sent, except every few seconds
  to keep GG from taking the screen back.
- Temperatures are read only while they're on screen or coloring the keys,
  and Spotify only while its screen is shown or can switch on by itself.
- If GG isn't running, it retries every few seconds and otherwise idles.
"""
import datetime
import threading
import time

import gamesense
import render
import settings as settings_mod

KEEPALIVE = 5.0          # GG hands the screen back after 10 s without events
RETRY = 4.0              # seconds between attempts to reach GG
STATS_EVERY = 1.0
SPOTIFY_IDLE_POLL = 2.0  # how often to check Spotify while another screen is shown


def heat_value(celsius, cool=40.0, hot=90.0):
    if celsius is None:
        return 0
    return round(100 * min(1.0, max(0.0, (celsius - cool) / (hot - cool))))


class Engine:
    def __init__(self, settings, client_factory=gamesense.GameSense, stats_reader=None,
                 spotify_reader=None, clock=time.monotonic, wall=datetime.datetime.now):
        self.client_factory = client_factory
        self.stats_reader = stats_reader            # zero-argument callable -> sensors.Stats
        self.spotify_reader = spotify_reader        # object with read() -> spotify.Track or None
        self.clock = clock
        self.wall = wall
        self.start = clock()
        self.lock = threading.Lock()
        self.data_lock = threading.Lock()           # sensors and Spotify: the window's preview reads them too
        self.draw_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.client = None
        self.connected = False
        self.status = "Starting..."
        self.next_retry = 0.0
        self.last_bytes = None
        self.last_sent = -1e9
        self.last_heat = None
        self.heat_sent = -1e9
        self.toggle = 0
        self.stats = None
        self.stats_time = -1e9
        self.track = None
        self.image = None                           # last frame, for the settings window's preview
        self.screen = None
        self.animated = False
        self.preview_animated = False
        self.apply(settings)

    # -------------------------------------------------------------- settings
    def apply(self, settings):
        """Use new settings from now on (safe to call from another thread)."""
        with self.lock:
            size = settings_mod.screen_size(settings)
            rgb_changed = getattr(self, "settings", None) is not None and \
                settings.get("rgb") != self.settings.get("rgb")
            self.settings = settings
            if getattr(self, "renderer", None) is None or self.renderer.size != size:
                self.renderer = render.Renderer(size)
            self.last_bytes = None                  # redraw and resend right away
            if rgb_changed:
                self.connected = False              # re-register with or without the key-color event
                self.next_retry = 0.0

    # ----------------------------------------------------------------- data
    def needs_spotify(self, s):
        return self.spotify_reader is not None and (s["show"] == "spotify" or s.get("spotify_auto"))

    def read_track(self, s):
        if not self.needs_spotify(s):
            return None
        if s["show"] != "spotify":
            self.spotify_reader.poll_every = SPOTIFY_IDLE_POLL
        else:
            self.spotify_reader.poll_every = 1.0
        return self.spotify_reader.read()

    def read_stats(self, now):
        if self.stats_reader is None:
            return None
        if now - self.stats_time >= STATS_EVERY:
            self.stats = self.stats_reader()
            self.stats_time = now
        return self.stats

    def pick_screen(self, s, track):
        if s["show"] == "spotify":
            return "spotify"
        if s.get("spotify_auto") and track is not None and track.playing and track.title:
            return "spotify"
        return s["show"] if s["show"] in settings_mod.SCREENS else "clock"

    def layout(self, screen, s, now_mono, track, stats):
        design = s["designs"][screen]
        if screen == "clock":
            return render.clock_layout(self.wall(), design)
        if screen == "temps":
            from sensors import Stats
            return render.temps_layout(stats or Stats(), design)
        return render.spotify_layout(track, design, now_mono)

    def frame(self):
        """Draw the current frame. Returns (image, seconds until it may change, stats)."""
        with self.lock:
            s, renderer = self.settings, self.renderer
        now = self.clock()
        with self.data_lock:
            track = self.read_track(s)
            screen = self.pick_screen(s, track)
            stats = self.read_stats(now) if (screen == "temps" or s.get("rgb")) else None
        layout = self.layout(screen, s, now, track, stats)
        design = s["designs"][screen]
        t = now - self.start
        with self.draw_lock:
            image = renderer.draw(layout, design, t)
            animated = renderer.animated(layout, design)
            change = renderer.background(design).next_change(t) if animated else None
        self.image, self.screen, self.track = image, screen, track
        self.animated = animated
        if animated:
            delay = max(1.0 / max(1, int(s.get("max_fps", 12))), change or 0)
        else:
            wall = self.wall()
            delay = 1.02 - wall.microsecond / 1_000_000       # just after the next whole second
        return image, delay, stats

    def preview(self, screen):
        """A frame of any screen for the settings window, with sample data if there's none."""
        import spotify
        from sensors import Stats
        with self.lock:
            s, renderer = self.settings, self.renderer
        now = self.clock()
        track = stats = None
        sample = False
        with self.data_lock:
            if screen == "spotify":
                track = self.spotify_reader.read() if self.spotify_reader is not None else None
            if screen == "temps":
                stats = self.read_stats(now)
        if screen == "spotify" and (track is None or not track.title):
            track = spotify.Track("Your song title here", "Artist name", True, 42.0, 180.0, now)
            sample = True
        if screen == "temps" and (stats is None or stats == Stats()):
            stats = Stats(cpu_temp=54, cpu_load=23, gpu_temp=61, gpu_load=40)
            sample = True
        layout = self.layout(screen, s, now, track, stats)
        with self.draw_lock:
            image = renderer.draw(layout, s["designs"][screen], now - self.start)
            self.preview_animated = renderer.animated(layout, s["designs"][screen])
        return image, sample

    # ---------------------------------------------------------------- output
    def connect(self):
        self.client = self.client_factory()
        self.client.register_screens(rgb=bool(self.settings.get("rgb")))
        self.connected = True
        self.last_bytes = None
        self.last_heat = None
        self.status = "Showing on SteelSeries GG"

    def send(self, image, stats):
        now = self.clock()
        if not self.connected:
            if now < self.next_retry:
                return
            try:
                self.connect()
            except gamesense.GameSenseError as exc:
                self.status = str(exc)
                self.next_retry = now + RETRY
                return
        data = render.to_bytes(image)
        try:
            if data != self.last_bytes or now - self.last_sent >= KEEPALIVE:
                self.toggle ^= 1            # a changing value makes sure GG never skips a frame
                self.client.send_bitmap(self.renderer.size, data, self.toggle)
                self.last_bytes, self.last_sent = data, now
            if self.settings.get("rgb") and stats is not None:
                heat = heat_value(stats.cpu_temp, self.settings.get("cool", 40), self.settings.get("hot", 90))
                if heat != self.last_heat or now - self.heat_sent >= KEEPALIVE:
                    self.client.send_heat(heat)
                    self.last_heat, self.heat_sent = heat, now
        except gamesense.GameSenseError as exc:
            # GG restarted (new port) or forgot us: register again on the next frame.
            self.status = str(exc)
            self.connected = False
            if self.client is not None:
                self.client.close()
            self.client = None
            self.next_retry = now

    def step(self):
        """Draw and send one frame. Returns how long to wait before the next."""
        image, delay, stats = self.frame()
        self.send(image, stats)
        if not self.connected:
            # Nobody sees frames while GG is away (the window draws its own preview): idle until the next retry.
            delay = max(delay, self.next_retry - self.clock())
        return max(0.01, delay)

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(self.step())
        finally:
            self.release()

    def stop(self):
        self.stop_event.set()

    def release(self):
        """Hand the screen and key lights back to GG."""
        if self.client is not None and self.connected:
            try:
                self.client.stop()
            except gamesense.GameSenseError:
                pass
            self.client.close()
        self.connected = False
