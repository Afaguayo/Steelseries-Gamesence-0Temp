"""Draw OLED screens as 1-bit bitmaps: text over a GIF, image or animation.

SteelSeries OLEDs are 128 pixels wide and 36, 40, 48 or 52 tall, one bit
per pixel. GameSense takes a bitmap as bytes, rows top to bottom, most
significant bit first, which is exactly what Pillow's mode "1" produces.

Everything expensive happens once: a GIF is decoded, scaled and dithered to
black and white when it's chosen, and fonts are loaded once. Each frame is
then a copy of a tiny 128x40 image plus a few lines of text.
"""
import math
import os
import random
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

SIZES = ((128, 36), (128, 40), (128, 48), (128, 52))
MAX_GIF_FRAMES = 400             # a longer GIF is cut short, to bound memory and start-up time
MIN_FRAME_MS = 20
BACKGROUNDS = ("none", "stars", "rain", "waves", "gif")
ANIMATED = ("stars", "rain", "waves")
FONT_PX = {"small": 10, "medium": 15, "large": 24}
ORDER = ("small", "medium", "large")
SCROLL_PX_PER_S = 24             # speed of long lines scrolling sideways
SCROLL_GAP = 24

# Fonts offered in the settings window, if the file exists on this PC.
FONT_FILES = {
    "Built-in": None,
    "Arial Bold": "arialbd.ttf",
    "Arial Black": "ariblk.ttf",
    "Consolas Bold": "consolab.ttf",
    "Courier New Bold": "courbd.ttf",
    "Impact": "impact.ttf",
    "Segoe UI Bold": "segoeuib.ttf",
    "Verdana Bold": "verdanab.ttf",
}


def font_dirs():
    if sys.platform == "win32":
        return [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    if sys.platform == "darwin":
        return ["/Library/Fonts", "/System/Library/Fonts/Supplemental", os.path.expanduser("~/Library/Fonts")]
    return ["/usr/share/fonts/truetype/msttcorefonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts"]


def font_path(name):
    """Full path of a font from FONT_FILES (or a path chosen by the user), or None."""
    if not name or name == "Built-in":
        return None
    if os.path.isfile(name):
        return name
    filename = FONT_FILES.get(name)
    for folder in font_dirs() if filename else []:
        path = os.path.join(folder, filename)
        if os.path.isfile(path):
            return path
    return None


def available_fonts():
    return [name for name in FONT_FILES if name == "Built-in" or font_path(name)]


_font_cache = {}


def load_font(family, size):
    key = (family, size)
    if key not in _font_cache:
        path = font_path(family)
        if path:
            font = ImageFont.truetype(path, FONT_PX[size])
        elif size == "small":
            font = ImageFont.load_default_imagefont()          # crisp pixel font
        else:
            font = ImageFont.load_default(FONT_PX[size])       # Pillow's bundled TrueType
        _font_cache[key] = font
    return _font_cache[key]


# ------------------------------------------------------------ what to draw

@dataclass
class Line:
    text: str
    size: str = "medium"


@dataclass
class Layout:
    lines: List[Line] = field(default_factory=list)
    progress: Optional[float] = None       # 0-1 draws a progress bar under the text


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def clock_layout(now, design):
    if design.get("hour24"):
        text = f"{now:%H:%M:%S}" if design.get("seconds", True) else f"{now:%H:%M}"
    else:
        hour = now.hour % 12 or 12
        clock = f"{hour}:{now:%M:%S}" if design.get("seconds", True) else f"{hour}:{now:%M}"
        text = f"{clock} {'AM' if now.hour < 12 else 'PM'}"
    lines = [Line(text, design.get("size", "large"))]
    if design.get("date", True):
        lines.append(Line(f"{DAYS[now.weekday()]} {MONTHS[now.month - 1]} {now.day}", "small"))
    return Layout(lines)


def fmt_temp(celsius, fahrenheit=False):
    if celsius is None:
        return "--"
    return f"{celsius * 9 / 5 + 32 if fahrenheit else celsius:.0f}°{'F' if fahrenheit else 'C'}"


def fmt_load(percent):
    return "--" if percent is None else f"{percent:.0f}%"


def temps_layout(stats, design):
    fahrenheit, size = design.get("fahrenheit", False), design.get("size", "medium")
    show_load = design.get("load", True)

    def line(label, temp, load):
        return Line(f"{label} {fmt_temp(temp, fahrenheit)}" + (f" {fmt_load(load)}" if show_load else ""), size)

    lines = [line("CPU", stats.cpu_temp, stats.cpu_load)]
    if stats.gpu_temp is not None or stats.gpu_load is not None:
        lines.append(line("GPU", stats.gpu_temp, stats.gpu_load))
    elif stats.ram is not None:
        lines.append(Line(f"RAM {fmt_load(stats.ram)}", size))
    return Layout(lines)


def spotify_layout(track, design, now=None):
    import spotify
    if track is None or not track.title:
        return Layout([Line("Spotify", design.get("size", "medium")),
                       Line("not playing" if track is None else "paused", "small")])
    lines = [Line(track.title, design.get("size", "medium"))]
    if design.get("artist", True) and track.artist:
        lines.append(Line(track.artist, "small"))
    elapsed = track.elapsed(now)
    if design.get("time", False) and elapsed is not None:
        times = f"{spotify.fmt_time(elapsed)} / {spotify.fmt_time(track.duration)}"
        lines.append(Line(times if track.playing else f"paused {times}", "small"))
    elif not track.playing:
        lines.append(Line("paused", "small"))
    progress = track.progress(now) if design.get("progress", True) else None
    return Layout(lines, progress)


# ----------------------------------------------------------- backgrounds

class Background:
    """Frames of a GIF/image, or a generated animation, at one screen size."""

    def __init__(self, kind, size, path=None, fit="fill", dither=True):
        self.kind = kind if kind in BACKGROUNDS else "none"
        self.size = size
        self.frames, self.durations, self.total_ms = [], [], 0
        self.error = None
        if self.kind == "gif":
            try:
                self._load(path, fit, dither)
            except Exception as exc:        # missing or unreadable file: show a plain screen
                self.error = f"{os.path.basename(path or '') or 'no file'}: {exc}"
                self.kind = "none"
        rng = random.Random(7)
        w, h = size
        self.stars = [(rng.uniform(0, w), rng.uniform(0, h), rng.choice((6, 12, 24))) for _ in range(28)]
        self.drops = [(rng.randrange(0, w, 3), rng.uniform(0, h * 2), rng.uniform(20, 45), rng.randint(3, 8))
                      for _ in range(24)]

    def _load(self, path, fit, dither):
        with Image.open(path) as source:
            for index, frame in enumerate(ImageSequence.Iterator(source)):
                if index >= MAX_GIF_FRAMES:
                    break
                image = frame.convert("RGBA")
                flat = Image.new("RGBA", image.size, (0, 0, 0, 255))
                flat.alpha_composite(image)          # transparent parts become black
                gray = flat.convert("L")
                if fit == "fit":
                    gray = ImageOps.pad(gray, self.size, color=0)
                else:
                    gray = ImageOps.fit(gray, self.size)
                self.frames.append(gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG if dither
                                                else Image.Dither.NONE))
                self.durations.append(max(MIN_FRAME_MS, int(frame.info.get("duration") or 100)))
        self.total_ms = sum(self.durations)

    @property
    def animated(self):
        return self.kind in ANIMATED or len(self.frames) > 1

    def next_change(self, t):
        """Seconds until the picture changes (None = never)."""
        if self.kind in ANIMATED:
            return 1 / 30
        if len(self.frames) < 2:
            return None
        ms = int(t * 1000) % self.total_ms
        for duration in self.durations:
            if ms < duration:
                return (duration - ms) / 1000
            ms -= duration
        return MIN_FRAME_MS / 1000

    def image(self, t):
        w, h = self.size
        if self.kind == "gif":
            if len(self.frames) == 1:
                return self.frames[0].copy()
            ms = int(t * 1000) % self.total_ms
            for frame, duration in zip(self.frames, self.durations):
                if ms < duration:
                    return frame.copy()
                ms -= duration
            return self.frames[-1].copy()
        image = Image.new("1", self.size, 0)
        draw = ImageDraw.Draw(image)
        if self.kind == "stars":
            for x, y, speed in self.stars:
                px = (x - speed * t) % w
                draw.point((px, y), 1)
                if speed >= 24:
                    draw.point((px + 1, y), 1)
        elif self.kind == "rain":
            for x, y, speed, length in self.drops:
                top = (y + speed * t) % (h * 2) - h
                draw.line((x, top, x, top + length), 1)
        elif self.kind == "waves":
            for phase, amp in ((0.0, h * 0.18), (2.1, h * 0.10)):
                points = [(x, h * 0.7 + amp * math.sin(x / 11 + t * 2.4 + phase)) for x in range(0, w + 1, 2)]
                draw.line(points, 1)
        return image


# --------------------------------------------------------------- drawing

class Renderer:
    """Draws one screen design at one OLED size."""

    def __init__(self, size=(128, 40)):
        self.size = tuple(size)
        self._backgrounds = {}
        self._sprites = {}

    def background(self, design):
        key = (design.get("background", "none"), design.get("gif"), design.get("fit", "fill"),
               design.get("dither", True))
        if key not in self._backgrounds:
            if len(self._backgrounds) > 6:
                self._backgrounds.clear()
            self._backgrounds[key] = Background(key[0], self.size, key[1], key[2], key[3])
        return self._backgrounds[key]

    def fit(self, layout, family):
        """Shrink text until every line fits the screen height."""
        sizes = [line.size if line.size in ORDER else "medium" for line in layout.lines]
        bar = 5 if layout.progress is not None else 0
        while True:
            heights = [line_height(load_font(family, s)) for s in sizes]
            total = sum(heights) + max(0, len(heights) - 1) + bar
            if total <= self.size[1] or all(s == "small" for s in sizes):
                return sizes, heights
            biggest = max(range(len(sizes)), key=lambda i: ORDER.index(sizes[i]))
            sizes[biggest] = ORDER[ORDER.index(sizes[biggest]) - 1]

    def sprite(self, text, family, size, backdrop):
        """A line of text drawn once: (width, glyph mask, mask of what to clear behind it).

        Text only changes about once a second, while an animation redraws many
        times a second, so each line is drawn with FreeType once and then just
        stamped onto every frame.
        """
        key = (text, family, size, backdrop)
        cached = self._sprites.get(key)
        if cached is not None:
            return cached
        font = load_font(family, size)
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        left, _, right, _ = probe.textbbox((0, 0), text, font=font)
        _, top, _, bottom = probe.textbbox((0, 0), "Hgy|", font=font)     # same baseline for every line
        width, height = max(1, right - left), bottom - top
        origin = (1 - left, 1 - top)
        glyphs = Image.new("1", (width + 2, height + 2), 0)
        pen = ImageDraw.Draw(glyphs)
        pen.fontmode = "1"                        # no anti-aliasing: crisp on a 1-bit screen
        pen.text(origin, text, font=font, fill=1)
        if backdrop == "box":
            clear = Image.new("1", glyphs.size, 1)
        elif backdrop == "outline":
            clear = Image.new("1", glyphs.size, 0)
            pen = ImageDraw.Draw(clear)
            pen.fontmode = "1"
            pen.text(origin, text, font=font, fill=1, stroke_width=1, stroke_fill=1)
        else:
            clear = None
        if len(self._sprites) > 64:
            self._sprites.clear()
        self._sprites[key] = (width, glyphs, clear)
        return self._sprites[key]

    def draw(self, layout, design, t):
        """The finished 1-bit image. `t` is seconds since start (animations, scrolling)."""
        w, h = self.size
        image = self.background(design).image(t)
        family = design.get("font", "Built-in")
        sizes, heights = self.fit(layout, family)
        bar_h = 5 if layout.progress is not None else 0
        block = sum(heights) + max(0, len(heights) - 1) + bar_h
        position = design.get("position", "center")
        y = 1 if position == "top" else h - block - 1 if position == "bottom" else (h - block) // 2
        backdrop = design.get("backdrop", "outline")
        align = design.get("align", "center")
        for line, size, height in zip(layout.lines, sizes, heights):
            width, glyphs, clear = self.sprite(line.text, family, size, backdrop)
            if width > w - 2:                     # too long: scroll sideways
                span = width + SCROLL_GAP
                x = 1 - int(t * SCROLL_PX_PER_S) % span
                xs = [x, x + span]
            else:
                xs = [1 if align == "left" else w - width - 1 if align == "right" else (w - width) // 2]
            for x in xs:
                if clear is not None:
                    image.paste(0, (x - 1, y - 1), clear)
                image.paste(1, (x - 1, y - 1), glyphs)
            y += height + 1
        if layout.progress is not None:
            draw = ImageDraw.Draw(image)
            top = min(y + 1, h - 4)
            draw.rectangle((4, top - 1, w - 5, top + 3), fill=0)
            draw.rectangle((4, top, w - 5, top + 2), outline=1, fill=0)
            filled = 4 + round((w - 9) * layout.progress)
            if filled > 4:
                draw.rectangle((4, top, filled, top + 2), fill=1)
        if design.get("invert"):
            image = ImageOps.invert(image.convert("L")).convert("1")
        return image

    def animated(self, layout, design):
        """Whether this screen moves between whole seconds (animation or scrolling text)."""
        if self.background(design).animated:
            return True
        family = design.get("font", "Built-in")
        sizes, _ = self.fit(layout, family)
        backdrop = design.get("backdrop", "outline")
        return any(self.sprite(line.text, family, s, backdrop)[0] > self.size[0] - 2
                   for line, s in zip(layout.lines, sizes))


_heights = {}


def line_height(font):
    if font not in _heights:
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        _, top, _, bottom = probe.textbbox((0, 0), "Hgy|", font=font)
        _heights[font] = bottom - top
    return _heights[font]


def to_bytes(image):
    """Bitmap bytes for GameSense: rows top to bottom, most significant bit first."""
    return list(image.tobytes())


def preview_rgb(image, scale=3, on=(235, 240, 255), off=(8, 8, 12)):
    """A bigger, colored copy for the settings window's preview."""
    # Color the small bitmap first, then scale: 9x fewer pixels to color at scale 3.
    small = ImageOps.colorize(image.convert("L"), black=off, white=on)
    return small.resize((image.width * scale, image.height * scale), Image.NEAREST)


