"""The settings window's choices, saved as JSON next to the PawnIO answer.

Anything missing from the file (an older version, a hand edit) falls back to
DEFAULTS, so new options never break an existing settings file.
"""
import copy
import os
import shutil
import time

import pawnio

PATH = pawnio.SETTINGS
SCREENS = ("clock", "temps", "spotify")

DEFAULTS = {
    "show": "clock",              # clock | temps | spotify
    "spotify_auto": True,         # switch to the Spotify screen while a song plays
    "screen": "128x40",           # OLED size: Apex Pro/7 128x40, Rival 700 128x36, Arctis 128x48/52
    "rgb": False,                 # function keys green-to-red with CPU temperature
    "cool": 40.0,
    "hot": 90.0,
    "max_fps": 12,                # animation frame-rate cap: lower uses less CPU
    "autostart_asked": False,
    "designs": {
        "clock": {"background": "none", "gif": None, "fit": "fill", "dither": True, "invert": False,
                  "font": "Built-in", "size": "large", "align": "center", "position": "center",
                  "backdrop": "outline", "hour24": False, "seconds": True, "date": True},
        "temps": {"background": "none", "gif": None, "fit": "fill", "dither": True, "invert": False,
                  "font": "Built-in", "size": "medium", "align": "center", "position": "center",
                  "backdrop": "outline", "fahrenheit": False, "load": True},
        "spotify": {"background": "none", "gif": None, "fit": "fill", "dither": True, "invert": False,
                    "font": "Built-in", "size": "medium", "align": "center", "position": "center",
                    "backdrop": "outline", "artist": True, "progress": True, "time": False},
    },
}


def merged(defaults, saved):
    result = copy.deepcopy(defaults)
    for key, value in (saved or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


def load(path=PATH):
    return merged(DEFAULTS, pawnio.load_settings(path))


def save(settings, path=PATH):
    """Save, keeping keys other parts of the app store in the same file."""
    data = pawnio.load_settings(path)
    data.update(settings)
    pawnio.save_settings(data, path)


def screen_size(settings):
    try:
        w, h = (int(n) for n in settings.get("screen", "128x40").split("x"))
        return w, h
    except ValueError:
        return 128, 40


def media_dir(path=PATH):
    return os.path.join(os.path.dirname(path), "media")


def import_media(source, path=PATH):
    """Copy a GIF/image into the app's folder so it keeps working if the original moves."""
    folder = media_dir(path)
    os.makedirs(folder, exist_ok=True)
    name, ext = os.path.splitext(os.path.basename(source))
    target = os.path.join(folder, f"{name}-{int(time.time())}{ext.lower()}")
    shutil.copyfile(source, target)
    return target
