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

1. Make sure **SteelSeries GG** is running. It usually starts with Windows.
2. Download **`SteelSeriesTemps.exe`** from [Releases](../../releases/latest) and double-click it.
   - Windows asks for **administrator** permission. Reading CPU temperatures needs it; every temperature tool (HWiNFO, LibreHardwareMonitor, Core Temp) asks for the same.
   - The first time, it offers to install **PawnIO**, the small signed driver that lets it read the CPU's temperature sensor. Press **Enter** to install it (it's a one-time step).
   - The app isn't code-signed, so if SmartScreen warns, click **More info → Run anyway**.
3. That's it. A small window shows the live readings and the OLED starts updating. Close the window to stop, and GG takes the screen back.

**Start it with Windows:** run `SteelSeriesTemps.exe --autostart on` once (and `--autostart off` to undo). It creates a Task Scheduler entry that starts it at login with admin rights, so there's no permission prompt every time.

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
python3 temps.py                    # send to SteelSeries GG until Ctrl+C
python3 temps.py --print            # just print readings; no SteelSeries gear needed
python3 temps.py --once             # print one reading and exit
python3 temps.py --diagnose         # what every temperature source reports
python3 temps.py --fahrenheit       # °F
python3 temps.py --no-rgb           # screen only; don't touch key lighting
python3 temps.py --cool 45 --hot 85 # temperatures for full green / full red
python3 temps.py --ascii            # 54C instead of 54°C, if your screen lacks the ° symbol
```

On Windows, from source, run `python build.py` once to download the sensor library (or use the .exe). On Linux, CPU temperatures come from psutil. On macOS, install [`smctemp`](https://github.com/narugit/smctemp).

## How it works

- **GameSense** is SteelSeries GG's local API. GG writes its address to `coreProps.json`. The app reads that, registers as **System Temps**, and binds one event to two handlers: a two-line text screen for any OLED device (`screened`) and a green-to-red color gradient on the `function-keys` zone. Every second it sends the event with the text lines and a 0-100 heat value. See [`gamesense.py`](gamesense.py).
- **Sensors:** [`sensors.py`](sensors.py) turns every source into the same list of readings and picks the best one for each value: *CPU Package* (Intel) or *Core (Tctl/Tdie)* (AMD) for CPU, *GPU Core* for GPU. The built-in reader loads `LibreHardwareMonitorLib.dll` through pythonnet on the .NET Framework that ships with Windows. HWiNFO and Core Temp are read from the shared memory those apps publish.
- **Staying in sync:** if GG restarts (it picks a new port) or forgets the app, the next update fails, and the app reconnects and registers again by itself. If the app is closed any way, it tells GG to release the screen. If it crashes, GG takes the screen back within 10 seconds.

## Tests

```bash
python3 -m unittest -v
```

A fake GameSense server stands in for SteelSeries GG, so no SteelSeries hardware is needed. HWiNFO and Core Temp are tested with byte-exact copies of their shared-memory layouts, and LibreHardwareMonitor with its current JSON format. On Windows, GitHub Actions also loads the real LibreHardwareMonitor library, builds the .exe, and runs its `--diagnose`.

LibreHardwareMonitor is licensed under the [MPL-2.0](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/master/LICENSE); its license ships inside the .exe alongside the library.
