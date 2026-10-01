"""A small client for SteelSeries GameSense, the local API in SteelSeries GG
that lets apps draw on OLED screens and light up RGB zones.

How GameSense works:
1. GG writes its local server address to coreProps.json.
2. An app registers itself (game_metadata) and binds events to handlers,
   for example "show these two text lines on any OLED screen".
3. The app then sends events with a value (0-100) and a frame of text, and
   GG renders them on every connected device that has a matching zone.

Docs: https://github.com/SteelSeries/gamesense-sdk
"""
import http.client
import json
import os
import sys

GAME = "SYSTEM_TEMPS"          # uppercase A-Z, 0-9, _ and - only
EVENT = "STATS"                # text lines + key colors (command-line modes)
SCREEN_EVENT = "SCREEN"        # full-screen bitmaps (designs, GIFs)
HEAT_EVENT = "HEAT"            # key colors alone, alongside SCREEN
SCREEN_SIZES = ((128, 36), (128, 40), (128, 48), (128, 52))


class GameSenseError(Exception):
    pass


def core_props_paths():
    """Where SteelSeries GG / Engine 3 write coreProps.json on this OS."""
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [os.path.join(base, "SteelSeries", "SteelSeries Engine 3", "coreProps.json"),
                os.path.join(base, "SteelSeries", "GG", "coreProps.json")]
    if sys.platform == "darwin":
        return ["/Library/Application Support/SteelSeries Engine 3/coreProps.json",
                "/Library/Application Support/SteelSeries GG/coreProps.json"]
    return []   # GG doesn't run on Linux


def find_address(paths=None):
    """'127.0.0.1:PORT' of the running GameSense server."""
    paths = paths if paths is not None else core_props_paths()
    if not paths:
        raise GameSenseError("SteelSeries GG doesn't exist for Linux, so the keyboard's screen can't be "
                             "updated here. The preview, Spotify and --print still work.")
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                address = json.load(f).get("address")
        except (OSError, ValueError):
            continue
        if address:
            return address
    raise GameSenseError("SteelSeries GG isn't running (coreProps.json not found). "
                         "Start SteelSeries GG and try again.")


def screen_handler():
    """Two lines of text on every OLED screen (keyboards, mice, headset bases)."""
    return {
        "device-type": "screened",
        "zone": "one",
        "mode": "screen",
        "datas": [{
            "lines": [
                {"has-text": True, "context-frame-key": "line1"},
                {"has-text": True, "context-frame-key": "line2"},
            ],
        }],
    }


def heat_handler():
    """Function-key row fades from green (cool) to red (hot) with the value."""
    return {
        "device-type": "rgb-per-key-zones",
        "zone": "function-keys",
        "mode": "color",
        "color": {"gradient": {
            "zero": {"red": 0, "green": 255, "blue": 60},
            "hundred": {"red": 255, "green": 0, "blue": 0},
        }},
    }


def bitmap_handler(width, height):
    """A full-screen bitmap on OLEDs of this size; the picture comes with each event."""
    return {
        "device-type": f"screened-{width}x{height}",
        "zone": "one",
        "mode": "screen",
        "datas": [{"has-text": False, "image-data": [0] * (width * height // 8)}],
    }


class GameSense:
    def __init__(self, address=None, timeout=2.0):
        self.address = address or find_address()
        self.timeout = timeout
        self.conn = None

    def _connection(self):
        if self.conn is None:
            host, port = self.address.rsplit(":", 1)
            self.conn = http.client.HTTPConnection(host, int(port), timeout=self.timeout)
        return self.conn

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def post(self, endpoint, payload):
        # One connection is kept open and reused: several updates a second
        # would otherwise each open and close a new one.
        body = json.dumps(payload, separators=(",", ":")).encode()
        for attempt in (1, 2):
            try:
                conn = self._connection()
                conn.request("POST", f"/{endpoint}", body, {"Content-Type": "application/json"})
                response = conn.getresponse()
                data = response.read()
                break
            except (OSError, http.client.HTTPException) as exc:
                self.close()               # GG may have closed an idle connection: retry once on a new one
                if attempt == 2:
                    raise GameSenseError(f"Can't reach SteelSeries GG at {self.address}: {exc}") from None
        if response.status >= 400:
            detail = data.decode(errors="replace")
            raise GameSenseError(f"GameSense rejected {endpoint}: {detail or response.reason}")
        return json.loads(data) if data.strip() else {}

    def register_metadata(self):
        self.post("game_metadata", {
            "game": GAME,
            "game_display_name": "System Temps",
            "developer": "Afaguayo",
            # If updates stop (crash, force quit), GG takes the screen back
            # after this long instead of showing frozen numbers.
            "deinitialize_timer_length_ms": 10000,
        })

    def register(self, rgb=True):
        """Tell GG about this app and what its STATS event should do."""
        self.register_metadata()
        handlers = [screen_handler()] + ([heat_handler()] if rgb else [])
        self.post("bind_game_event", {
            "game": GAME,
            "event": EVENT,
            "min_value": 0,
            "max_value": 100,
            "icon_id": 0,
            "value_optional": False,
            "handlers": handlers,
        })

    def register_screens(self, rgb=False):
        """Bitmap screens for every OLED size, plus key colors if wanted."""
        self.register_metadata()
        self.post("bind_game_event", {
            "game": GAME,
            "event": SCREEN_EVENT,
            "value_optional": True,
            "handlers": [bitmap_handler(w, h) for w, h in SCREEN_SIZES],
        })
        if rgb:
            self.post("bind_game_event", {
                "game": GAME,
                "event": HEAT_EVENT,
                "min_value": 0,
                "max_value": 100,
                "icon_id": 0,
                "value_optional": False,
                "handlers": [heat_handler()],
            })

    def send_bitmap(self, size, data, value=0):
        width, height = size
        self.post("game_event", {
            "game": GAME,
            "event": SCREEN_EVENT,
            "data": {"value": int(value), "frame": {f"image-data-{width}x{height}": data}},
        })

    def send_heat(self, value):
        self.post("game_event", {"game": GAME, "event": HEAT_EVENT, "data": {"value": int(value)}})

    def send(self, value, line1, line2):
        self.post("game_event", {
            "game": GAME,
            "event": EVENT,
            "data": {"value": int(value), "frame": {"line1": line1, "line2": line2}},
        })

    def stop(self):
        """Hand the screen and lights back to GG right away."""
        self.post("stop_game", {"game": GAME})

    def remove(self):
        """Unregister completely (it disappears from GG's Apps list)."""
        self.post("remove_game", {"game": GAME})
