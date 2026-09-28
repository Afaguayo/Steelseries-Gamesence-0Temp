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
import json
import os
import sys
import urllib.error
import urllib.request

GAME = "SYSTEM_TEMPS"          # uppercase A-Z, 0-9, _ and - only
EVENT = "STATS"


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
    for path in paths if paths is not None else core_props_paths():
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


class GameSense:
    def __init__(self, address=None, timeout=2.0):
        self.address = address or find_address()
        self.timeout = timeout

    def post(self, endpoint, payload):
        request = urllib.request.Request(
            f"http://{self.address}/{endpoint}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise GameSenseError(f"GameSense rejected {endpoint}: {detail or exc.reason}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise GameSenseError(f"Can't reach SteelSeries GG at {self.address}: {exc}") from None
        return json.loads(body) if body.strip() else {}

    def register(self, rgb=True):
        """Tell GG about this app and what its STATS event should do."""
        self.post("game_metadata", {
            "game": GAME,
            "game_display_name": "System Temps",
            "developer": "Afaguayo",
            # If updates stop (crash, force quit), GG takes the screen back
            # after this long instead of showing frozen numbers.
            "deinitialize_timer_length_ms": 10000,
        })
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
