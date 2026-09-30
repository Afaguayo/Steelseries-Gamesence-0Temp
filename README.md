# SteelSeries GameSense Temps

Your SteelSeries OLED screen (Apex Pro, Apex 7, Rival 700, Arctis bases) as a little display of your own, through SteelSeries GameSense:

- **Clock**, **CPU/GPU temperatures**, or **what Spotify is playing**, with a progress bar. It can switch to Spotify by itself while a song plays.
- **Your own designs:** put a GIF or picture behind the text, or a built-in animation (starfield, rain, waves), and choose the font, size, layout and outline.
- The function-key row can shade from green to red as your CPU heats up.
- A settings window with a live preview. It asks if it should start with Windows, then runs quietly from the tray.
- Light on the PC: about 1% of one CPU core for a clock and about 3% for an animated GIF (see [Performance](#performance)).

```text
┌────────────────┐
│ CPU 54°C  23%  │     temperature + load
│ GPU 61°C  40%  │     (RAM use if there's no GPU reading)
└────────────────┘
 F1 F2 F3 ... F12      green at 40°C → red at 90°C
```

## Setup (Windows)

1. Make sure **SteelSeries GG** is running. It usually starts with Windows.
2. Download **`SteelSeriesTemps.exe`** from [Releases](../../releases/latest) and double-click it.
   - Windows asks for **administrator** permission. Reading CPU temperatures needs it; every temperature tool (HWiNFO, LibreHardwareMonitor, Core Temp) asks for the same.
   - The first time, it offers to install **PawnIO**, the small signed driver that lets it read the CPU's temperature sensor. Press **Enter** to install it (it's a one-time step).
   - The app isn't code-signed, so if SmartScreen warns, click **More info → Run anyway**.
3. The settings window opens and the OLED starts updating. The first time, it asks whether to **start automatically when you log in**. If you say yes, it starts quietly in the tray at login, with no permission prompt (it uses a Task Scheduler entry with admin rights). You can change this later on the General tab.
4. The first time you show temperatures, it offers to install **PawnIO**, the small signed driver that lets it read the CPU's temperature sensor. The clock and Spotify screens don't need it.

Closing the window keeps the screen running from the **tray icon**. Right-click it and choose **Open settings** or **Quit**; quitting hands the screen back to GG.

## The settings window

| Tab | What you set |
|---|---|
| **General** | What to show (Clock, Temps or Spotify), switch to Spotify while a song plays, your screen size, key colors by CPU temperature, the animation speed limit, start at login |
| **Clock** | 12/24-hour, seconds, date |
| **Temps** | °C/°F, load % |
| **Spotify** | Artist, progress bar, time (1:23 / 3:45) |

Each screen tab also has its own **design**:

- **Background:** plain black, *Starfield*, *Rain*, *Waves*, or **your own GIF or image**. It's turned into black and white for the OLED; *Dither* gives photos shading, and *Show whole image* fits it without cropping. The file is copied into the app's folder, so moving the original doesn't break anything.
- **Text:** font (the built-in pixel font or a Windows font such as Arial Black, Impact or Consolas), size, alignment, position, and what goes behind it (an outline or a black box, to stay readable over a busy GIF). Lines too long for the screen, like long song titles, scroll sideways.
- **Invert** swaps black and white.

The preview at the top shows the tab you're on, using sample data if nothing is playing, and changes reach the OLED right away. Everything is saved by itself in `%APPDATA%\SteelSeriesTemps\settings.json`.

**Screen size:** the Apex keyboards are 128x40 (the default) and the Rival mice 128x36. If the picture looks cut off or squashed, try another size.

## Spotify

No Spotify account, login or API key is needed. The app reads what's playing the same way the Windows volume pop-up does: the Windows media controls give the song, artist, play/pause and position. If those aren't available, it falls back to Spotify's window title ("Artist - Song"), without the progress bar. `SteelSeriesTemps.exe --diagnose` shows which one it's using and what's playing.

## Performance

The app only works when there's something new to show:

- A clock or temperatures redraw once a second, on the second. An animation or scrolling title redraws only as fast as the GIF's own frame timing, capped by the *animation speed limit* on the General tab (12 fps by default).
- A frame that hasn't changed isn't sent, except every 5 seconds so GG doesn't take the screen back. The connection to GG stays open instead of reconnecting for every frame.
- Temperatures are only read while they're on screen or coloring the keys, and the sensor library isn't even loaded until then. A sensor source that finds nothing (for example, no NVIDIA card) is skipped for 30 seconds instead of being asked every second.
- Spotify is asked from a background thread at most once a second, or every 2 s while another screen is showing, so a slow answer never delays the screen.
- GIFs are decoded and turned black and white once, when you choose them. Text is drawn once per change and stamped onto each frame.
- With the window closed, nothing is drawn for the preview. If GG isn't running, it just checks every 4 seconds.

Measured with the window closed: about **0.7%** of one CPU core for a clock and about **2.6%** for a 12 fps animation. GitHub Actions measures the real .exe on Windows on every build (see the *fake SteelSeries GG* step).

## Command line

**Just want a clock, without the window?** Run `SteelSeriesTemps.exe --clock` (add `--24h` for 24-hour time). It shows the time and date as text in GG's own font and leaves the key lighting alone.

```text
┌────────────────┐
│ 9:05:07 PM     │
│ Tue Sep 29 2026│
└────────────────┘
```

**Temperature not showing?** Run `SteelSeriesTemps.exe --diagnose`. It lists every source it tried and what each one returned, and says what's missing. For example, it tells you if PawnIO isn't installed or if the app isn't running as administrator. `SteelSeriesTemps.exe --install-driver` asks about PawnIO again if you said no the first time.

## Where the readings come from

The app has its own sensor reader built in. It also picks up readings from monitoring apps you may already run:

| Source | What you need | Gives |
|---|---|---|
| **Built in**: [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)'s sensor library, packed into the .exe | Administrator rights + the PawnIO driver (offered on first run) | CPU and GPU temperature and load, RAM |
| **HWiNFO** | HWiNFO running with **Settings → Shared Memory Support** on | CPU and GPU temperature and load, RAM |
| **Core Temp** | Core Temp running (nothing to turn on) | CPU temperature and load |
| **LibreHardwareMonitor app** | Its **Options → Remote Web Server** turned on | CPU and GPU temperature and load, RAM |
| `nvidia-smi` | An NVIDIA GPU driver | GPU temperature and load |

Sources are tried in that order, and missing values are filled in from the next one. A sensor that reports an impossible temperature (0 °C, which some drivers return when they can't read the chip) counts as missing, so the screen shows `--` rather than a wrong number.

**Why a driver?** Windows doesn't give apps CPU temperatures. They have to be read from the processor's registers, which needs a kernel driver. The old one most tools used (WinRing0) is now blocked by Windows Defender as vulnerable. PawnIO is its signed, maintained replacement, and it's what current LibreHardwareMonitor uses.

## Run from source (any OS)

Needs Python 3.9 or newer.

```bash
pip install -r requirements.txt
python3 temps.py --gui              # the settings window (what the .exe opens)
python3 temps.py --background       # run with the saved settings, from the tray
python3 temps.py                    # temperatures as text, until Ctrl+C
python3 temps.py --print            # just print readings; no SteelSeries gear needed
python3 temps.py --once             # print one reading and exit
python3 temps.py --diagnose         # what every temperature source reports
python3 temps.py --fahrenheit       # °F
python3 temps.py --no-rgb           # screen only; don't touch key lighting
python3 temps.py --cool 45 --hot 85 # temperatures for full green / full red
python3 temps.py --ascii            # 54C instead of 54°C, if your screen lacks the ° symbol
python3 temps.py --clock            # time and date instead of temperatures
python3 temps.py --clock --24h      # 24-hour clock
```

On Windows, from source, run `python build.py` once to download the sensor library (or use the .exe). On Linux, CPU temperatures come from psutil. On macOS, install [`smctemp`](https://github.com/narugit/smctemp).

## How it works

- **GameSense** is SteelSeries GG's local API. GG writes its address to `coreProps.json`. The app reads that and registers as **System Temps**. The settings window's screens are full-screen bitmaps: a `SCREEN` event is bound to a bitmap handler for each OLED size (`screened-128x36` ... `screened-128x52`), and every frame carries 1 bit per pixel in `image-data-128x40` (or your size). Key colors are a separate `HEAT` event with a green-to-red gradient on the `function-keys` zone. The command-line modes bind a two-line text screen instead. See [`gamesense.py`](gamesense.py).
- **Drawing:** [`render.py`](render.py) draws each screen with Pillow in 1-bit black and white: background, text, progress bar. [`engine.py`](engine.py) decides what to show and when, and sends it. [`gui.py`](gui.py) is the settings window (tkinter), and [`spotify.py`](spotify.py) reads what's playing.
- **Sensors:** [`sensors.py`](sensors.py) turns every source into the same list of readings and picks the best one for each value: *CPU Package* (Intel) or *Core (Tctl/Tdie)* (AMD) for CPU, *GPU Core* for GPU. The built-in reader loads `LibreHardwareMonitorLib.dll` through pythonnet on the .NET Framework that ships with Windows. HWiNFO and Core Temp are read from the shared memory those apps publish.
- **Staying in sync:** if GG restarts (it picks a new port) or forgets the app, the next update fails, and the app reconnects and registers again by itself. If the app is closed any way, it tells GG to release the screen. If it crashes, GG takes the screen back within 10 seconds.

## Tests

```bash
python3 -m unittest -v
```

A fake GameSense server stands in for SteelSeries GG, so no SteelSeries hardware is needed. HWiNFO and Core Temp are tested with byte-exact copies of their shared-memory layouts, and LibreHardwareMonitor with its current JSON format. Spotify is tested with its window title, AppleScript and playerctl outputs, and the settings window with a real (virtual, on Linux) display.

On Windows, GitHub Actions also loads the real LibreHardwareMonitor library and builds the .exe. It then runs the .exe's `--diagnose`, and runs the .exe against [`ci_fake_gg.py`](ci_fake_gg.py), a fake GG, in tray mode and with the settings window open. That checks every bitmap it sends and prints its CPU use.

LibreHardwareMonitor is licensed under the [MPL-2.0](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/master/LICENSE); its license ships inside the .exe alongside the library.
