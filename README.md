# SteelSeries GameSense Temps

Live CPU and GPU temperatures on your SteelSeries OLED screen, like the one on the Apex Pro, Apex 7, Rival 700 or the Arctis Nova Pro base station, through SteelSeries GameSense. The function-key row also shades from green to red as your CPU heats up, so you can see a hot CPU mid-game without looking at the screen.

```text
┌────────────────┐
│ CPU 54°C  23%  │     temperature + load
│ GPU 61°C  40%  │     (RAM use if there's no GPU reading)
└────────────────┘
 F1 F2 F3 ... F12      green at 40°C → red at 90°C
```

## Setup (Windows)

1. **SteelSeries GG** must be running. It usually starts with Windows.
2. **LibreHardwareMonitor.** Windows has no built-in way for apps to read CPU temperatures, so this app gets them from [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases) (free, open source):
   - Run it **as administrator**. It needs that to read the CPU sensors.
   - Turn on **Options → Remote Web Server → Run** (default port 8085).
   - Optional: enable **Options → Start Minimized** and **Run On Windows Startup**.
3. **This app:** download `SteelSeriesTemps.exe` from [Releases](../../releases/latest) and double-click it. A small window shows the live readings, and the OLED starts updating. Close the window to stop, and GG takes the screen back.

To start it with Windows, press `Win+R`, type `shell:startup`, and put a shortcut to `SteelSeriesTemps.exe` in that folder.

NVIDIA GPU temperatures also work without LibreHardwareMonitor, through `nvidia-smi` (installed with the NVIDIA driver).

## Run from source (any OS)

Needs Python 3.9 or newer.

```bash
pip install -r requirements.txt
python3 temps.py                    # send to SteelSeries GG until Ctrl+C
python3 temps.py --print            # just print readings; no SteelSeries gear needed
python3 temps.py --fahrenheit       # °F
python3 temps.py --no-rgb           # screen only; don't touch key lighting
python3 temps.py --cool 45 --hot 85 # temperatures for full green / full red
python3 temps.py --ascii            # 54C instead of 54°C, if your screen lacks the ° symbol
python3 temps.py --help
```

| OS | CPU temperature from | GPU temperature from |
|---|---|---|
| Windows | LibreHardwareMonitor | LibreHardwareMonitor, or `nvidia-smi` |
| macOS | [`smctemp`](https://github.com/narugit/smctemp) if installed | none |
| Linux | psutil (`coretemp` / `k10temp`) | `nvidia-smi` |

If there's no temperature source, the screen shows `--` for temperature and the app prints what to install. Load and RAM use always work.

## How it works

- **GameSense** is SteelSeries GG's local API. GG writes its address to `coreProps.json`. The app reads that, registers as **System Temps**, and binds one event to two handlers: a two-line text screen for any OLED device (`screened`) and a green-to-red color gradient on the `function-keys` zone. Every second it sends the event with the text lines and a 0-100 heat value. The code is in [`gamesense.py`](gamesense.py).
- **Sensors:** [`sensors.py`](sensors.py) tries each source for this OS and merges the results. LibreHardwareMonitor's sensor tree is searched for *CPU Package* or *Core (Tctl/Tdie)* (Intel or AMD) and *GPU Core*. Its values use your locale's decimal separator (`63,4 °C`), which is handled.
- **Staying in sync:** if GG restarts (it picks a new port) or forgets the app, the next update fails, and the app reconnects and registers again by itself. If the app is closed any way, it tells GG to release the screen. If it crashes, GG takes the screen back within 10 seconds.

## Tests

```bash
python3 -m unittest -v
```

A fake GameSense server stands in for SteelSeries GG, so the tests need no SteelSeries hardware. They check the exact requests sent to GG, reconnecting after a GG restart, waiting while GG is closed, and parsing real-format LibreHardwareMonitor, `nvidia-smi` and psutil readings. GitHub Actions runs them on Windows and Linux, then builds the `.exe`. Pushing a `v*` tag attaches the `.exe` to a release.
