"""What Spotify is playing, read locally without a Spotify account or API key.

- Windows: the media controls Windows shows on the volume flyout (SMTC),
  which give title, artist, play/pause and the song's position. If the
  winrt packages aren't available, Spotify's window title
  ("Artist - Song" while playing) is used instead, without the position.
- macOS: asks the Spotify app through AppleScript, only while it's running
  (asking a closed app would launch it).
- Linux: playerctl (MPRIS).

Reading is throttled: however often the screen redraws, Spotify is asked at
most once per `poll_every` seconds, and the position in between is worked
out from the clock.
"""
import subprocess
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Optional

IS_WINDOWS = sys.platform == "win32"


@dataclass
class Track:
    title: str = ""
    artist: str = ""
    playing: bool = False
    position: Optional[float] = None    # seconds into the song, measured at `at`
    duration: Optional[float] = None    # seconds
    at: float = 0.0                     # time.monotonic() of the measurement

    def elapsed(self, now=None):
        """Seconds into the song now, moving on from the last measurement while playing."""
        if self.position is None:
            return None
        now = time.monotonic() if now is None else now
        value = self.position + (now - self.at if self.playing else 0)
        return min(value, self.duration) if self.duration else value

    def progress(self, now=None):
        """0.0-1.0 through the song, or None if the length isn't known."""
        elapsed = self.elapsed(now)
        if elapsed is None or not self.duration:
            return None
        return max(0.0, min(1.0, elapsed / self.duration))


def fmt_time(seconds):
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


# ----------------------------------------------------------- window title

IDLE_TITLES = ("spotify", "spotify premium", "spotify free")


def parse_window_title(title):
    """Spotify's window title -> Track. None if it isn't a Spotify main window title."""
    title = (title or "").strip()
    if not title:
        return None
    if title.lower() in IDLE_TITLES:
        return Track(playing=False)                  # open, paused or stopped
    if " - " in title:
        artist, song = title.split(" - ", 1)
        return Track(title=song.strip(), artist=artist.strip(), playing=True)
    return Track(title=title, playing=True)          # "Advertisement" and the like


def read_window_title():
    """Track from the title of Spotify.exe's main window (Windows)."""
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    titles = []
    buffer = ctypes.create_unicode_buffer(512)

    def is_spotify(pid):
        handle = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            size = wintypes.DWORD(len(buffer))
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            return bool(ok) and buffer.value.lower().endswith("\\spotify.exe")
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        # Spotify's main window is a Chromium window: check the cheap class name first.
        if user32.GetClassNameW(hwnd, buffer, len(buffer)) and buffer.value.startswith("Chrome_WidgetWin"):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if is_spotify(pid.value):
                    titles.append(text.value)
        return True

    user32.EnumWindows(visit, 0)
    for title in titles:
        track = parse_window_title(title)
        if track is not None:
            return track
    return None


# ------------------------------------------------------ Windows media (SMTC)

class WindowsMedia:
    """Spotify's session in Windows' media controls. `ok` is False without winrt."""

    def __init__(self):
        self.ok = False
        self.error = None
        try:
            import asyncio
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager)
            self.loop = asyncio.new_event_loop()
            self.manager = self.loop.run_until_complete(Manager.request_async())
            self.ok = True
        except Exception as exc:          # ImportError, or no media service (Windows Server)
            self.error = f"{type(exc).__name__}: {exc}"

    def read(self):
        for session in self.manager.get_sessions():
            if "spotify" in (session.source_app_user_model_id or "").lower():
                return self.loop.run_until_complete(self._track(session))
        return None

    async def _track(self, session):
        import datetime
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        timeline = session.get_timeline_properties()
        playing = int(info.playback_status) == 4          # ...PlaybackStatus.PLAYING
        duration = timeline.end_time.total_seconds() if timeline.end_time else 0
        position = timeline.position.total_seconds() if timeline.position is not None else None
        if position is not None and playing and timeline.last_updated_time:
            # Spotify only updates the timeline now and then: carry it forward to now.
            now = datetime.datetime.now(datetime.timezone.utc)
            position += max(0.0, (now - timeline.last_updated_time).total_seconds())
        return Track(title=props.title or "", artist=props.artist or "", playing=playing,
                     position=position if duration else None,
                     duration=duration or None, at=time.monotonic())

    def close(self):
        if self.ok:
            self.loop.close()


# ------------------------------------------------------------------ macOS

APPLESCRIPT = """
if application "Spotify" is running then
    tell application "Spotify"
        if player state is stopped then return "stopped"
        set t to current track
        return (player state as text) & linefeed & (name of t) & linefeed & (artist of t) & linefeed & ¬
            (player position as text) & linefeed & ((duration of t) as text)
    end tell
end if
return ""
"""


def parse_osascript(output):
    """'playing\\nSong\\nArtist\\n12.5\\n215000' -> Track (duration is in ms)."""
    lines = output.strip().splitlines()
    if not lines:
        return None
    if lines[0] == "stopped" or len(lines) < 5:
        return Track(playing=False)
    try:
        position = float(lines[3].replace(",", "."))
        duration = float(lines[4].replace(",", ".")) / 1000
    except ValueError:
        position = duration = None
    return Track(title=lines[1], artist=lines[2], playing=lines[0] == "playing",
                 position=position, duration=duration or None, at=time.monotonic())


def read_mac():
    # Check the process first: AppleScript that names an app that isn't installed
    # opens a "Where is Spotify?" dialog, and osascript itself is slow to start.
    if subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True).returncode != 0:
        return None
    output = subprocess.run(["osascript", "-e", APPLESCRIPT], capture_output=True, text=True, timeout=3).stdout
    return parse_osascript(output)


# ------------------------------------------------------------------ Linux

PLAYERCTL_FORMAT = "{{status}}\t{{title}}\t{{artist}}\t{{position}}\t{{mpris:length}}"


def parse_playerctl(output):
    """'Playing\\tSong\\tArtist\\t12500000\\t215000000' -> Track (times in microseconds)."""
    parts = output.rstrip("\n").split("\t")
    if len(parts) < 5 or not parts[0]:
        return None
    try:
        position = int(parts[3]) / 1e6
        duration = int(parts[4]) / 1e6
    except ValueError:
        position = duration = None
    return Track(title=parts[1], artist=parts[2], playing=parts[0] == "Playing",
                 position=position, duration=duration or None, at=time.monotonic())


def read_linux():
    tool = shutil.which("playerctl")
    if not tool:
        return None
    result = subprocess.run([tool, "--player=spotify", "metadata", "--format", PLAYERCTL_FORMAT],
                            capture_output=True, text=True, timeout=3)
    return parse_playerctl(result.stdout) if result.returncode == 0 else None


# ------------------------------------------------------------------ reader

ERROR_BACKOFF = 10.0        # after a failed read, wait this long before asking again


class SpotifyReader:
    """Throttled now-playing reader for this OS. read() returns a Track or None.

    With threaded=True, Spotify is asked from a background thread and read()
    returns the latest answer at once, so a slow answer (AppleScript can take
    seconds) never holds up drawing the screen.
    """

    def __init__(self, poll_every=1.0, backend=None, clock=time.monotonic, threaded=False):
        self.poll_every = poll_every
        self.clock = clock
        self.media = None
        if backend is not None:
            self.backend, self.name = backend, "custom"
        elif IS_WINDOWS:
            self.media = WindowsMedia()
            if self.media.ok:
                self.backend, self.name = self._windows, "Windows media controls"
            else:
                self.backend, self.name = read_window_title, "Spotify window title"
        elif sys.platform == "darwin":
            self.backend, self.name = read_mac, "AppleScript"
        else:
            self.backend, self.name = read_linux, "playerctl"
        self.track = None
        self.last_poll = None
        self.next_poll = 0.0
        self.thread = None
        if threaded:
            import threading
            self.stopped = threading.Event()
            self.wanted = threading.Event()       # set by read(): someone is looking at Spotify
            self.thread = threading.Thread(target=self._loop, name="spotify", daemon=True)
            self.thread.start()

    def _windows(self):
        # Some Spotify versions don't publish to the media controls: try the title too.
        return self.media.read() or read_window_title()

    def poll(self):
        """Ask Spotify now (unless it failed recently)."""
        now = self.clock()
        if now < self.next_poll:
            return
        self.last_poll = now
        try:
            self.track = self.backend()
        except Exception:
            self.track = None
            self.next_poll = now + ERROR_BACKOFF

    def _loop(self):
        # WindowsMedia's event loop belongs to the thread that made it: make it here.
        if self.media is not None and self.media.ok:
            self.media.close()
            self.media = WindowsMedia()
        while not self.stopped.is_set():
            self.wanted.wait()                    # nobody reading: don't poll at all
            if self.stopped.is_set():
                break
            self.wanted.clear()
            self.poll()
            self.stopped.wait(self.poll_every)

    def read(self):
        if self.thread is not None:
            self.wanted.set()
            return self.track
        now = self.clock()
        if self.last_poll is None or now - self.last_poll >= self.poll_every:
            self.poll()
            self.last_poll = now
        return self.track

    def close(self):
        if self.thread is not None:
            self.stopped.set()
            self.wanted.set()
            self.thread.join(timeout=4)
        if self.media is not None:
            self.media.close()
