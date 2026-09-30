"""Read CPU and GPU temperatures and loads, from whatever this machine offers.

Temperatures aren't exposed the same way anywhere:
- Windows has no built-in API for them. This app reads them itself with
  LibreHardwareMonitor's library (bundled in the .exe), which needs the free
  PawnIO driver. It can also pick them up from HWiNFO or Core Temp shared
  memory, or LibreHardwareMonitor's web server, if one of those is running.
- NVIDIA GPUs report through nvidia-smi on Windows and Linux.
- Linux exposes CPU temperatures through psutil (coretemp, k10temp, ...).
- macOS needs a helper tool such as smctemp.

Every source turns its data into Readings, pick() chooses the best CPU/GPU
values from them, and read_stats() merges sources so the display always
shows whatever is available.
"""
import ctypes
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
from collections import namedtuple
from dataclasses import dataclass
from typing import Optional

LHM_URL = "http://127.0.0.1:8085/data.json"
IS_WINDOWS = sys.platform == "win32"

# kind: cpu / gpu / ram; group: temp / load
Reading = namedtuple("Reading", "kind group name value")


@dataclass
class Stats:
    cpu_temp: Optional[float] = None   # °C
    cpu_load: Optional[float] = None   # %
    gpu_temp: Optional[float] = None   # °C
    gpu_load: Optional[float] = None   # %
    ram: Optional[float] = None        # % used

    def merge(self, other):
        """Fill in any missing values from another Stats."""
        for name in self.__dataclass_fields__:
            if getattr(self, name) is None:
                setattr(self, name, getattr(other, name))
        return self


def number(text):
    """'54.0 °C' or '54,0 °C' -> 54.0; None if there's no number."""
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text or ""))
    return float(match.group().replace(",", ".")) if match else None


# Preferred sensor names, best first. Anything else in the same group is used
# only if none of these exist (e.g. a single "Core #1" reading).
PREFERRED = {
    ("cpu", "temp"): ["CPU Package", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)", "Tctl/Tdie", "Tdie", "Tctl",
                      "Core Max", "Core Average", "CPU Cores"],
    ("gpu", "temp"): ["GPU Core", "GPU Temperature", "GPU Hot Spot"],
    ("cpu", "load"): ["CPU Total", "Total CPU Usage"],
    ("gpu", "load"): ["GPU Core", "GPU Core Load", "GPU Utilization", "D3D 3D"],
    ("ram", "load"): ["Memory", "Physical Memory Load"],
}


TEMP_RANGE = (1.0, 150.0)          # °C; anything outside is a sensor that isn't really reading


def pick(readings):
    """Choose one value per stat from a list of Readings."""
    best = {}
    for kind, group, name, value in readings:
        if value is None or value != value:            # skip missing / NaN
            continue
        # A sensor the driver can't read often reports 0 °C instead of nothing
        # (seen with LibreHardwareMonitor on virtual machines); treat
        # implausible temperatures as missing so the screen shows -- not 0°C.
        if group == "temp" and not TEMP_RANGE[0] <= value <= TEMP_RANGE[1]:
            continue
        key = (kind, group)
        names = PREFERRED.get(key, [])
        rank = names.index(name) if name in names else len(names)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, float(value))
    get = lambda key: best[key][1] if key in best else None  # noqa: E731
    return Stats(cpu_temp=get(("cpu", "temp")), cpu_load=get(("cpu", "load")),
                 gpu_temp=get(("gpu", "temp")), gpu_load=get(("gpu", "load")),
                 ram=get(("ram", "load")))


def kind_from_hardware(hardware_type="", hardware_id="", icon=""):
    """LibreHardwareMonitor hardware -> cpu / gpu / ram / None."""
    text = f"{hardware_type} {hardware_id} {icon}".lower()
    if "gpu" in text or any(k in icon.lower() for k in ("nvidia.png", "ati.png", "intel.png")):
        return "gpu"
    if "cpu" in text:
        return "cpu"
    if "memory" in text or "/ram" in text or "ram.png" in text:
        return "ram"
    return None


GROUPS = {"Temperature": "temp", "Temperatures": "temp", "Load": "load"}


# ------------------------------------------- LibreHardwareMonitor web server

def lhm_readings(tree):
    """Readings from LibreHardwareMonitor's data.json.

    Nesting is hardware -> sensor type ("Temperatures", "Load") -> sensor.
    Newer versions tag hardware with HardwareId (/intelcpu/0, /gpu-nvidia/0)
    and sensors with Type; older ones only give an icon per hardware type.
    """
    readings = []

    def walk(node, kind=None, group=None):
        hardware_id = node.get("HardwareId")
        if hardware_id:
            # Trust the id: LHM gives unknown hardware types the CPU icon.
            kind = kind_from_hardware(hardware_id=hardware_id)
        elif "images_icon/" in (node.get("ImageURL") or ""):
            kind = kind_from_hardware(icon=node["ImageURL"]) or kind
        group = GROUPS.get(node.get("Type") or node.get("Text"), group)
        children = node.get("Children") or []
        if not children and kind and group:
            value = node.get("RawValue")
            if not isinstance(value, (int, float)):
                value = number(node.get("Value"))
            readings.append(Reading(kind, group, node.get("Text", ""), value))
        for child in children:
            walk(child, kind, group)

    walk(tree)
    return readings


def parse_lhm(tree):
    return pick(lhm_readings(tree))


def read_lhm(url=LHM_URL, timeout=1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return parse_lhm(json.load(response))
    except (OSError, ValueError):
        return Stats()


# ------------------------------------- built-in LibreHardwareMonitor library

def bundled_lhm_dir():
    """Folder with LibreHardwareMonitorLib.dll: next to the app, or inside the .exe."""
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__))):
        if base and os.path.isfile(os.path.join(base, "lhm", "LibreHardwareMonitorLib.dll")):
            return os.path.join(base, "lhm")
    return None


class BuiltinSensors:
    """Reads sensors in-process with LibreHardwareMonitorLib (Windows only).

    CPU temperatures need administrator rights and the PawnIO driver; GPU
    temperatures and loads usually work without them.
    """

    def __init__(self, lib_dir=None):
        self.error = None
        self.computer = None
        self.hardware_names = []
        lib_dir = lib_dir or bundled_lhm_dir()
        if not IS_WINDOWS:
            self.error = "only available on Windows"
            return
        if not lib_dir:
            self.error = "LibreHardwareMonitorLib.dll not found"
            return
        try:
            from pythonnet import load
            try:
                load("netfx")                     # .NET Framework, built into Windows
            except RuntimeError:
                pass                              # a runtime is already loaded
            import clr
            sys.path.append(lib_dir)
            clr.AddReference(os.path.join(lib_dir, "LibreHardwareMonitorLib.dll"))
            from LibreHardwareMonitor.Hardware import Computer
            computer = Computer()
            computer.IsCpuEnabled = True
            computer.IsGpuEnabled = True
            computer.IsMemoryEnabled = True
            computer.Open()
            self.computer = computer
            self.hardware_names = [f"{h.HardwareType}: {h.Name}" for h in computer.Hardware]
        except Exception as exc:                  # missing .NET, blocked DLL, etc.
            self.error = f"{type(exc).__name__}: {exc}"

    @property
    def ok(self):
        return self.computer is not None

    def readings(self):
        if not self.computer:
            return []
        readings = []

        def collect(hardware):
            hardware.Update()
            kind = kind_from_hardware(hardware_type=str(hardware.HardwareType))
            for sensor in hardware.Sensors:
                group = GROUPS.get(str(sensor.SensorType))
                if kind and group:
                    value = sensor.Value
                    readings.append(Reading(kind, group, str(sensor.Name),
                                            None if value is None else float(value)))
            for sub in hardware.SubHardware:
                collect(sub)

        for hardware in self.computer.Hardware:
            try:
                collect(hardware)
            except Exception:                     # one bad device shouldn't stop the rest
                continue
        return readings

    def read(self):
        return pick(self.readings())

    def close(self):
        if self.computer:
            try:
                self.computer.Close()
            except Exception:
                pass
            self.computer = None


# -------------------------------------------------- Windows shared memory

def read_shared_memory(names, size):
    """Bytes of an existing named file mapping, or None if no app created it."""
    if not IS_WINDOWS:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenFileMappingW.restype = ctypes.c_void_p
    kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.c_uint32, ctypes.c_size_t]
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    FILE_MAP_READ = 0x0004
    for name in names:
        handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not handle:
            continue
        try:
            view = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, size)
            if not view:
                continue
            try:
                return ctypes.string_at(view, size)
            finally:
                kernel32.UnmapViewOfFile(view)
        finally:
            kernel32.CloseHandle(handle)
    return None


# HWiNFO: "Settings > Shared Memory Support" must be on.
HWINFO_NAMES = ["Global\\HWiNFO_SENS_SM2", "HWiNFO_SENS_SM2"]
HWINFO_HEADER = struct.Struct("<4sIIqIIIIII")        # 44 bytes, packed
HWINFO_READING_TYPES = {1: "temp", 7: "load"}          # SENSOR_READING_TYPE: 1 temperature, 7 usage


def _cstr(raw):
    return raw.split(b"\0", 1)[0].decode("latin-1").strip()


def hwinfo_readings(data):
    """Readings from HWiNFO's shared-memory block."""
    if not data or len(data) < HWINFO_HEADER.size:
        return []
    sig, _ver, _rev, _time, off_s, size_s, num_s, off_r, size_r, num_r = HWINFO_HEADER.unpack_from(data)
    if sig not in (b"HWiS", b"SiWH"):
        return []
    sensor_names = []
    for i in range(num_s):
        start = off_s + i * size_s
        # dwSensorID, dwSensorInst, szSensorNameOrig[128], szSensorNameUser[128]
        sensor_names.append(_cstr(data[start + 8:start + 136]))
    readings = []
    for i in range(num_r):
        start = off_r + i * size_r
        if start + 316 > len(data):
            break
        rtype, sensor_index, _rid = struct.unpack_from("<III", data, start)
        label = _cstr(data[start + 12:start + 140])
        value = struct.unpack_from("<d", data, start + 12 + 128 + 128 + 16)[0]
        group = HWINFO_READING_TYPES.get(rtype)
        if not group:
            continue
        sensor = sensor_names[sensor_index] if sensor_index < len(sensor_names) else ""
        text = f"{sensor} {label}".lower()
        if label in ("Physical Memory Load",):
            kind = "ram"
        elif "gpu" in text:
            kind = "gpu"
        elif "cpu" in text or "core" in label.lower() or "tctl" in text or "tdie" in text:
            kind = "cpu"
        else:
            continue
        readings.append(Reading(kind, group, label, value))
    return readings


def read_hwinfo():
    header = read_shared_memory(HWINFO_NAMES, HWINFO_HEADER.size)
    if not header:
        return Stats()
    _s, _v, _r, _t, off_s, size_s, num_s, off_r, size_r, num_r = HWINFO_HEADER.unpack_from(header)
    total = max(off_s + size_s * num_s, off_r + size_r * num_r)
    return pick(hwinfo_readings(read_shared_memory(HWINFO_NAMES, total)))


# Core Temp: shared memory is on by default while it runs.
CORETEMP_NAMES = ["CoreTempMappingObjectEx", "CoreTempMappingObject",
                  "Global\\CoreTempMappingObjectEx", "Global\\CoreTempMappingObject"]
CORETEMP_SIZE = 2686


def coretemp_stats(data):
    """Stats from Core Temp's CORE_TEMP_SHARED_DATA block."""
    if not data or len(data) < CORETEMP_SIZE:
        return Stats()
    loads = struct.unpack_from("<256I", data, 0)
    tjmax = struct.unpack_from("<128I", data, 1024)
    cores, cpus = struct.unpack_from("<II", data, 1536)
    temps = struct.unpack_from("<256f", data, 1544)
    fahrenheit, delta_to_tjmax = data[2684], data[2685]
    count = cores * max(cpus, 1)
    if not 0 < count <= 256:
        return Stats()
    values = []
    for i in range(count):
        t = temps[i]
        if delta_to_tjmax:                    # value is distance below TjMax
            t = tjmax[i // cores if cores else 0] - t
        if fahrenheit:
            t = (t - 32) * 5 / 9
        values.append(t)
    values = [v for v in values if TEMP_RANGE[0] <= v <= TEMP_RANGE[1]]
    return Stats(cpu_temp=max(values) if values else None, cpu_load=sum(loads[:count]) / count)


def read_coretemp():
    return coretemp_stats(read_shared_memory(CORETEMP_NAMES, CORETEMP_SIZE))


# ------------------------------------------------------------------ nvidia

def parse_nvidia_smi(output):
    """'61, 34' (temperature, utilization) -> Stats; first GPU only."""
    first = output.strip().splitlines()[0] if output.strip() else ""
    parts = [number(p) for p in first.split(",")]
    if len(parts) < 2:
        return Stats()
    return Stats(gpu_temp=parts[0], gpu_load=parts[1])


def read_nvidia():
    tool = shutil.which("nvidia-smi")
    if not tool:
        return Stats()
    try:
        output = subprocess.run(
            [tool, "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return Stats()
    return parse_nvidia_smi(output)


# ------------------------------------------------------------------ psutil

CPU_SENSOR_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")


def parse_psutil_temps(temps):
    """Pick the CPU package temperature from psutil.sensors_temperatures()."""
    for name in CPU_SENSOR_NAMES:
        entries = temps.get(name) or []
        for entry in entries:                  # prefer the package / Tctl reading
            if entry.label.lower().startswith(("package", "tctl", "tdie")):
                return entry.current
        if entries:
            return max(e.current for e in entries)
    return None


def read_psutil():
    try:
        import psutil
    except ImportError:
        return Stats()
    stats = Stats(cpu_load=psutil.cpu_percent(interval=None), ram=psutil.virtual_memory().percent)
    if hasattr(psutil, "sensors_temperatures"):    # Linux / FreeBSD only
        try:
            stats.cpu_temp = parse_psutil_temps(psutil.sensors_temperatures())
        except OSError:
            pass
    return stats


# ------------------------------------------------------------------- macOS

def read_mac_tool():
    """CPU temperature from smctemp or osx-cpu-temp, if one is installed."""
    for command in (["smctemp", "-c"], ["osx-cpu-temp"]):
        tool = shutil.which(command[0])
        if not tool:
            continue
        try:
            output = subprocess.run([tool] + command[1:], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        value = number(output)
        if value:
            return Stats(cpu_temp=value)
    return Stats()


# ------------------------------------------------------------------ merging

def sources(builtin=None):
    """(name, reader) pairs for this OS, best first."""
    found = []
    if IS_WINDOWS:
        if builtin is not None and builtin.ok:
            found.append(("built-in (LibreHardwareMonitor library)", builtin.read))
        found += [("HWiNFO shared memory", read_hwinfo), ("Core Temp shared memory", read_coretemp),
                  ("LibreHardwareMonitor web server", read_lhm)]
    if sys.platform == "darwin":
        found.append(("smctemp / osx-cpu-temp", read_mac_tool))
    found += [("nvidia-smi", read_nvidia), ("psutil", read_psutil)]
    return found


def read_stats(builtin=None):
    """Everything we can read on this machine, best sources first."""
    stats = Stats()
    for _name, reader in sources(builtin):
        stats.merge(reader())
        if None not in (stats.cpu_temp, stats.cpu_load, stats.gpu_temp, stats.gpu_load, stats.ram):
            break
    return stats


class SensorReader:
    """read_stats() that remembers which sources came back empty.

    A source that found nothing (no HWiNFO running, no NVIDIA card...) is
    skipped for `retry` seconds instead of being asked every second, which
    saves starting nvidia-smi or opening a connection on every update.
    """

    def __init__(self, builtin=None, retry=30.0, clock=None):
        import time
        self.builtin = builtin
        self.retry = retry
        self.clock = clock or time.monotonic
        self.skip_until = {}

    def read(self):
        now = self.clock()
        stats = Stats()
        for name, reader in sources(self.builtin):
            if self.skip_until.get(name, 0) > now:
                continue
            try:
                reading = reader()
            except Exception:
                reading = Stats()
            if reading == Stats():
                self.skip_until[name] = now + self.retry
            stats.merge(reading)
            if None not in (stats.cpu_temp, stats.cpu_load, stats.gpu_temp, stats.gpu_load, stats.ram):
                break
        return stats


def is_admin():
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def temperature_help():
    """What to do when no CPU temperature can be read on this OS."""
    if IS_WINDOWS:
        return ("CPU temperature needs administrator rights and the free PawnIO driver. Run "
                "SteelSeriesTemps.exe --install-driver, or run HWiNFO (with Shared Memory Support on) "
                "or Core Temp. Run SteelSeriesTemps.exe --diagnose for details.")
    if sys.platform == "darwin":
        return "To show the CPU temperature, install smctemp (https://github.com/narugit/smctemp)."
    return "To show the CPU temperature, load your CPU's sensor driver (try: sudo sensors-detect)."
