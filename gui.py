"""The settings window: pick what the OLED shows and design each screen.

Every change is applied to the OLED right away and saved automatically.
Closing the window keeps the screen running from the tray icon (where there
is one); "Quit" in the tray menu stops it and hands the screen back to GG.
"""
import copy
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import render
import settings as settings_mod
import startup

APP_NAME = "SteelSeries Temps"
FOOTER = "made by frijolito trash with love"
PREVIEW_SCALE = 3
PREVIEW_FPS = 10
SIZE_CHOICES = {
    "128x40": "128 x 40  (Apex keyboards)",
    "128x36": "128 x 36  (Rival mice)",
    "128x48": "128 x 48",
    "128x52": "128 x 52  (Arctis / GameDAC)",
}
BACKGROUND_NAMES = {"none": "Plain black", "stars": "Starfield", "rain": "Rain",
                    "waves": "Waves", "gif": "My GIF / image"}
SCREEN_TITLES = {"clock": "Clock", "temps": "Temps", "spotify": "Spotify"}
EXTRA_OPTIONS = {
    "clock": [("hour24", "24-hour time"), ("seconds", "Show seconds"), ("date", "Show the date")],
    "temps": [("fahrenheit", "°F instead of °C"), ("load", "Show load %")],
    "spotify": [("artist", "Show the artist"), ("progress", "Progress bar"), ("time", "Show time (1:23 / 3:45)")],
}
CHOICES = {
    "size": ("small", "medium", "large"),
    "align": ("left", "center", "right"),
    "position": ("top", "center", "bottom"),
    "backdrop": ("none", "outline", "box"),
}
CHOICE_LABELS = {"size": "Text size", "align": "Align", "position": "Position",
                 "backdrop": "Behind the text"}


class App:
    def __init__(self, root, engine, settings, path=settings_mod.PATH, tray=True, startup_mod=startup,
                 stats=None):
        self.root = root
        self.stats = stats                   # temps.LazyStats, reset after installing PawnIO
        self.driver_offered = False
        self.engine = engine
        self.settings = settings
        self.path = path
        self.startup = startup_mod
        self.save_job = None
        self.preview_job = None
        self.photo = None
        self.last_preview = None
        self.events = queue.Queue()
        self.tray = None
        self.vars = []                       # keep tk variables alive

        root.title(APP_NAME)
        root.minsize(440, 0)
        root.protocol("WM_DELETE_WINDOW", self.close_window)
        self.build()
        if tray:
            self.tray = make_tray(self.events)
        self.root.after(200, self.poll_events)

    # --------------------------------------------------------------- layout
    def build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        screen = tk.Frame(outer, bg="#111", padx=6, pady=6)
        screen.pack()
        self.preview_label = tk.Label(screen, bg="#111", bd=0)
        self.preview_label.pack()
        self.status = ttk.Label(outer, text="", foreground="#666")
        self.status.pack(pady=(4, 6))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.add(self.general_tab(self.notebook), text="General")
        for name in settings_mod.SCREENS:
            self.notebook.add(self.screen_tab(self.notebook, name), text=SCREEN_TITLES[name])
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self.refresh_preview(force=True))

        tk.Label(outer, text=FOOTER, font=("TkDefaultFont", 7), fg="#9a9a9a").pack(pady=(8, 0))

    def var(self, kind, value, on_change):
        variable = kind(value=value)
        variable.trace_add("write", lambda *_: on_change(variable.get()))
        self.vars.append(variable)
        return variable

    def general_tab(self, parent):
        s = self.settings
        tab = ttk.Frame(parent, padding=10)
        ttk.Label(tab, text="Show on the screen").grid(row=0, column=0, sticky="w")
        show = self.var(tk.StringVar, s["show"], lambda v: self.set(("show",), v))
        row = ttk.Frame(tab)
        row.grid(row=0, column=1, sticky="w")
        for name in settings_mod.SCREENS:
            ttk.Radiobutton(row, text=SCREEN_TITLES[name], value=name, variable=show).pack(side="left", padx=(0, 8))

        auto = self.var(tk.BooleanVar, s["spotify_auto"], lambda v: self.set(("spotify_auto",), v))
        ttk.Checkbutton(tab, text="Switch to Spotify while a song is playing", variable=auto).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Label(tab, text="Screen size").grid(row=2, column=0, sticky="w", pady=2)
        size = self.var(tk.StringVar, SIZE_CHOICES.get(s["screen"], SIZE_CHOICES["128x40"]),
                        lambda v: self.set(("screen",), next(k for k, t in SIZE_CHOICES.items() if t == v)))
        ttk.Combobox(tab, textvariable=size, values=list(SIZE_CHOICES.values()), state="readonly",
                     width=28).grid(row=2, column=1, sticky="w")

        rgb = self.var(tk.BooleanVar, s["rgb"], lambda v: self.set(("rgb",), v))
        ttk.Checkbutton(tab, text="Color the function keys by CPU temperature", variable=rgb).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Label(tab, text="Animation speed limit").grid(row=4, column=0, sticky="w", pady=2)
        fps_row = ttk.Frame(tab)
        fps_row.grid(row=4, column=1, sticky="w")
        fps_text = ttk.Label(fps_row, text=f"{s['max_fps']} fps", width=7)
        fps = self.var(tk.IntVar, s["max_fps"], lambda v: (fps_text.configure(text=f"{v} fps"),
                                                           self.set(("max_fps",), int(v))))
        ttk.Scale(fps_row, from_=4, to=30, variable=fps,
                  command=lambda v: fps.set(int(float(v)))).pack(side="left")
        fps_text.pack(side="left", padx=4)
        ttk.Label(tab, text="Lower uses less CPU. Clock and temps only update once a second anyway.",
                  foreground="#666", wraplength=380).grid(row=5, column=0, columnspan=2, sticky="w")

        self.autostart = self.var(tk.BooleanVar, self.startup.is_enabled(), self.set_autostart)
        ttk.Checkbutton(tab, text="Start automatically when I log in", variable=self.autostart).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 2))
        tab.columnconfigure(1, weight=1)
        return tab

    def screen_tab(self, parent, name):
        d = self.settings["designs"][name]
        tab = ttk.Frame(parent, padding=10)

        ttk.Label(tab, text="Background").grid(row=0, column=0, sticky="w")
        names = list(BACKGROUND_NAMES.values())
        bg = self.var(tk.StringVar, BACKGROUND_NAMES.get(d["background"], names[0]),
                      lambda v: self.set_background(name, v))
        ttk.Combobox(tab, textvariable=bg, values=names, state="readonly", width=16).grid(row=0, column=1, sticky="w")
        pick = ttk.Frame(tab)
        pick.grid(row=1, column=0, columnspan=4, sticky="w", pady=2)
        file_label = ttk.Label(pick, text=os.path.basename(d.get("gif") or "") or "no file chosen",
                               foreground="#666")
        ttk.Button(pick, text="Choose GIF / image...",
                   command=lambda: self.choose_gif(name, bg, file_label)).pack(side="left")
        file_label.pack(side="left", padx=6)

        flags = ttk.Frame(tab)
        flags.grid(row=2, column=0, columnspan=4, sticky="w", pady=2)
        for key, text in (("fit", "Show whole image"), ("dither", "Dither (photos)"), ("invert", "Invert")):
            value = d[key] == "fit" if key == "fit" else d[key]
            convert = (lambda v: "fit" if v else "fill") if key == "fit" else bool
            v = self.var(tk.BooleanVar, value, lambda v, k=key, c=convert: self.set(("designs", name, k), c(v)))
            ttk.Checkbutton(flags, text=text, variable=v).pack(side="left", padx=(0, 8))

        ttk.Separator(tab).grid(row=3, column=0, columnspan=4, sticky="ew", pady=6)
        ttk.Label(tab, text="Font").grid(row=4, column=0, sticky="w")
        fonts = render.available_fonts()
        font = self.var(tk.StringVar, d["font"] if d["font"] in fonts else "Built-in",
                        lambda v: self.set(("designs", name, "font"), v))
        ttk.Combobox(tab, textvariable=font, values=fonts, state="readonly", width=16).grid(row=4, column=1, sticky="w")
        for i, key in enumerate(CHOICES):
            r, c = 4 + (i + 1) // 2, ((i + 1) % 2) * 2
            ttk.Label(tab, text=CHOICE_LABELS[key]).grid(row=r, column=c, sticky="w", pady=2)
            v = self.var(tk.StringVar, d[key], lambda v, k=key: self.set(("designs", name, k), v))
            ttk.Combobox(tab, textvariable=v, values=CHOICES[key], state="readonly", width=10).grid(
                row=r, column=c + 1, sticky="w", padx=(0, 8))

        ttk.Separator(tab).grid(row=8, column=0, columnspan=4, sticky="ew", pady=6)
        extras = ttk.Frame(tab)
        extras.grid(row=9, column=0, columnspan=4, sticky="w")
        for key, text in EXTRA_OPTIONS[name]:
            v = self.var(tk.BooleanVar, d[key], lambda v, k=key: self.set(("designs", name, k), bool(v)))
            ttk.Checkbutton(extras, text=text, variable=v).pack(side="left", padx=(0, 8))
        return tab

    # -------------------------------------------------------------- changes
    def set(self, keys, value):
        target = self.settings
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        if keys in (("show",), ("rgb",)) and value in ("temps", True):
            self.root.after_idle(self.offer_driver)
        self.engine.apply(copy.deepcopy(self.settings))
        self.refresh_preview(force=True)
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(600, self.save)

    def save(self):
        self.save_job = None
        settings_mod.save(self.settings, self.path)

    def set_background(self, screen, label):
        kind = next(k for k, t in BACKGROUND_NAMES.items() if t == label)
        self.set(("designs", screen, "background"), kind)

    def choose_gif(self, screen, bg_var, file_label):
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose a GIF or image",
            filetypes=[("GIFs and images", "*.gif *.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return
        try:
            stored = settings_mod.import_media(path, self.path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Couldn't copy that file: {exc}", parent=self.root)
            return
        file_label.configure(text=os.path.basename(path))
        self.settings["designs"][screen]["gif"] = stored
        bg_var.set(BACKGROUND_NAMES["gif"])          # also applies and saves
        self.set(("designs", screen, "gif"), stored)
        with self.engine.draw_lock:
            error = self.engine.renderer.background(self.settings["designs"][screen]).error
        if error:
            messagebox.showerror(APP_NAME, f"Couldn't read that file ({error}).", parent=self.root)

    def offer_driver(self):
        """Windows: CPU temperature needs PawnIO. Ask once, the first time temperatures are used."""
        if self.driver_offered or self.stats is None or sys.platform != "win32":
            return
        self.driver_offered = True
        import pawnio
        if pawnio.installed_version() or self.settings.get("pawnio_declined"):
            return
        if messagebox.askyesno(
                APP_NAME, "Reading the CPU temperature needs PawnIO, a small free signed driver "
                          "(the one LibreHardwareMonitor uses).\n\nInstall it now?", parent=self.root):
            self.root.configure(cursor="watch")
            self.root.update()
            try:
                ok = pawnio.install()
            except OSError:
                ok = False
            finally:
                self.root.configure(cursor="")
            if ok:
                self.stats.reset()
            else:
                messagebox.showerror(APP_NAME, "PawnIO setup didn't finish.", parent=self.root)
        else:
            self.settings["pawnio_declined"] = True
            self.save()

    def set_autostart(self, enable):
        if not self.startup.set_enabled(bool(enable)):
            messagebox.showerror(APP_NAME, "Couldn't change the startup setting.", parent=self.root)

    def ask_autostart(self):
        """First launch only: ask whether to start at login."""
        if self.settings.get("autostart_asked"):
            return
        answer = messagebox.askyesno(
            APP_NAME, "Start SteelSeries Temps automatically when you log in?\n\n"
                      "It runs quietly in the background. You can change this later in General.",
            parent=self.root)
        self.settings["autostart_asked"] = True
        self.save()
        if answer:
            self.autostart.set(True)

    # -------------------------------------------------------------- preview
    def current_tab(self):
        index = self.notebook.index(self.notebook.select())
        return None if index == 0 else settings_mod.SCREENS[index - 1]

    def refresh_preview(self, force=False):
        if self.preview_job is not None:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        if self.root.state() == "withdrawn":
            return                                # hidden: don't draw at all
        screen = self.current_tab()
        if screen is None:
            image = self.engine.image                     # the frame the OLED has: nothing extra to draw
            if image is None:
                image, _ = self.engine.preview(self.settings["show"])
            moving = self.engine.animated
            now_showing = self.engine.screen or self.settings["show"]
            text = f"Now showing: {SCREEN_TITLES[now_showing]}  -  {self.engine.status}"
        else:
            image, sample = self.engine.preview(screen)
            moving = self.engine.preview_animated
            text = f"Preview of the {SCREEN_TITLES[screen]} screen" + (" (sample data)" if sample else "")
        data = image.tobytes()
        if force or data != self.last_preview:
            from PIL import ImageTk
            picture = render.preview_rgb(image, PREVIEW_SCALE)
            if self.photo is not None and (self.photo.width(), self.photo.height()) == picture.size:
                self.photo.paste(picture)                 # reuse the Tk image instead of making a new one
            else:
                self.photo = ImageTk.PhotoImage(picture)
                self.preview_label.configure(image=self.photo)
            self.last_preview = data
        if self.status.cget("text") != text:
            self.status.configure(text=text)
        # Still screens only change once a second: check 4 times a second, not 15.
        delay = int(1000 / PREVIEW_FPS) if moving else 250
        self.preview_job = self.root.after(delay, self.refresh_preview)

    # --------------------------------------------------------- window, tray
    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.refresh_preview(force=True)

    def close_window(self):
        if self.tray is not None:
            self.root.withdraw()                  # keep running from the tray
            if self.preview_job is not None:
                self.root.after_cancel(self.preview_job)
                self.preview_job = None
        else:
            self.quit()

    def quit(self):
        if self.save_job:
            self.root.after_cancel(self.save_job)
            self.save()
        self.engine.stop()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def poll_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "open":
                self.show_window()
            elif event == "quit":
                self.quit()
                return
        self.root.after(250, self.poll_events)


def tray_icon_image():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 12, 62, 52), radius=8, fill=(20, 20, 24, 255), outline=(255, 120, 0, 255), width=3)
    draw.rectangle((12, 24, 52, 28), fill=(235, 240, 255, 255))
    draw.rectangle((12, 34, 40, 38), fill=(235, 240, 255, 255))
    return image


def make_tray(events):
    """A tray icon with Open / Quit, or None where the system has no tray support."""
    if sys.platform == "darwin":                  # pystray needs the main thread on macOS
        return None
    try:
        import pystray
    except ImportError:
        return None
    menu = pystray.Menu(pystray.MenuItem("Open settings", lambda: events.put("open"), default=True),
                        pystray.MenuItem("Quit", lambda: events.put("quit")))
    icon = pystray.Icon("SteelSeriesTemps", tray_icon_image(), APP_NAME, menu)
    try:
        icon.run_detached()
    except Exception:
        return None
    return icon


def hide_console():
    """Hide the console window the .exe opens, when nothing else is using it."""
    if sys.platform != "win32":
        return
    import ctypes
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    processes = (ctypes.c_uint * 4)()
    if kernel32.GetConsoleProcessList(processes, 4) <= 2:     # just us (and the bootloader)
        window = kernel32.GetConsoleWindow()
        if window:
            user32.ShowWindow(window, 0)


def main(engine, background=False, path=settings_mod.PATH, stats=None):
    settings = settings_mod.load(path)
    engine.apply(copy.deepcopy(settings))
    thread = threading.Thread(target=engine.run, name="oled", daemon=True)
    thread.start()
    root = tk.Tk()
    try:
        from PIL import ImageTk
        root.iconphoto(True, ImageTk.PhotoImage(tray_icon_image()))
    except Exception:
        pass
    app = App(root, engine, settings, path, stats=stats)
    self_test = os.environ.get("SST_EXIT_AFTER")        # CI: run a few seconds, report CPU use, quit
    if background and app.tray is not None:
        root.withdraw()
    else:
        app.refresh_preview(force=True)
        if not self_test:
            root.after(400, app.ask_autostart)
            if settings["show"] == "temps" or settings["rgb"]:
                root.after(600, app.offer_driver)
    if self_test:
        root.after(2000, lambda: report_cpu(app, float(self_test) - 2, background))
    try:
        root.mainloop()
    finally:
        engine.stop()
        thread.join(timeout=3)


def report_cpu(app, seconds, background):
    """Measure this process's CPU use for `seconds`, print it, and quit (used by CI)."""
    import time
    cpu, wall = time.process_time(), time.monotonic()

    def done():
        used = 100 * (time.process_time() - cpu) / (time.monotonic() - wall)
        mode = "background" if background else "window open"
        print(f"Self-test ({mode}, {app.engine.screen} screen): CPU {used:.1f}% of one core. "
              f"Status: {app.engine.status}", flush=True)
        app.quit()
    app.root.after(int(seconds * 1000), done)
