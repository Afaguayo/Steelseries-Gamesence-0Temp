"""Read CPU and GPU temperatures and loads, from whatever the OS offers.

Temperatures aren't exposed the same way anywhere:
- Windows has no built-in API for them. LibreHardwareMonitor (free, open
  source) reads the chips and can serve them as JSON on localhost:8085.
- Linux exposes them through psutil (coretemp, k10temp and friends).
- macOS needs a helper tool such as smctemp or osx-cpu-temp.
- NVIDIA GPUs report through nvidia-smi on Windows and Linux.

Every reader returns None for what it can't find, and read_stats() merges
the results, so the display always shows whatever is available.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from typing import Optional

LHM_URL = "http://127.0.0.1:8085/data.json"


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


# ------------------------------------------------------ LibreHardwareMonitor

def parse_lhm(tree):
    """Pick CPU/GPU temperature and load and RAM use out of LHM's sensor tree.

    The tree nests hardware -> sensor group ("Temperatures", "Load") ->
    sensor. Hardware is recognized by its icon, which LHM sets per type.
    """
    stats = Stats()
    preferred = {
        ("cpu", "Temperatures"): ["CPU Package", "Core (Tctl/Tdie)", "Core Max", "Core Average"],
        ("gpu", "Temperatures"): ["GPU Core", "GPU Hot Spot"],
        ("cpu", "Load"): ["CPU Total"],
        ("gpu", "Load"): ["GPU Core", "D3D 3D"],
        ("ram", "Load"): ["Memory"],
    }
    found = {}

    def kind_of(node):
        icon = (node.get("ImageURL") or "").lower()
        if "cpu" in icon:
            return "cpu"
        if any(k in icon for k in ("nvidia", "ati", "amd", "intel")) and "cpu" not in icon:
            return "gpu"
        if "ram" in icon:
            return "ram"
        return None

    def walk(node, kind=None, group=None):
        kind = kind_of(node) or kind
        text = node.get("Text", "")
        if text in ("Temperatures", "Load"):
            group = text
        children = node.get("Children") or []
        if not children and kind and group:
            names = preferred.get((kind, group), [])
            value = number(node.get("Value"))
            if value is not None:
                rank = names.index(text) if text in names else len(names)
                key = (kind, group)
                if key not in found or rank < found[key][0]:
                    found[key] = (rank, value)
        for child in children:
            walk(child, kind, group)

    walk(tree)
    get = lambda key: found[key][1] if key in found else None  # noqa: E731
    stats.cpu_temp = get(("cpu", "Temperatures"))
    stats.gpu_temp = get(("gpu", "Temperatures"))
    stats.cpu_load = get(("cpu", "Load"))
    stats.gpu_load = get(("gpu", "Load"))
    stats.ram = get(("ram", "Load"))
    return stats


def read_lhm(url=LHM_URL, timeout=1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return parse_lhm(json.load(response))
    except (OSError, ValueError):
        return Stats()


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


def read_stats():
    """Everything we can read on this machine, best sources first."""
    stats = Stats()
    if sys.platform == "win32":
        stats.merge(read_lhm())
    if sys.platform == "darwin":
        stats.merge(read_mac_tool())
    stats.merge(read_nvidia())
    stats.merge(read_psutil())
    return stats


def temperature_help():
    """What to install when no CPU temperature can be read on this OS."""
    if sys.platform == "win32":
        return ("To show temperatures, run LibreHardwareMonitor (https://github.com/LibreHardwareMonitor/"
                "LibreHardwareMonitor) as administrator and turn on Options > Remote Web Server (port 8085).")
    if sys.platform == "darwin":
        return "To show the CPU temperature, install smctemp (https://github.com/narugit/smctemp)."
    return "To show the CPU temperature, load your CPU's sensor driver (try: sudo sensors-detect)."
