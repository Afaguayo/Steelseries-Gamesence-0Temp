"""The PawnIO driver, which LibreHardwareMonitor needs to read CPU temperatures.

PawnIO is a small, signed kernel driver (https://pawnio.eu) that replaced the
old WinRing0 driver, which Windows Defender now blocks. This module checks
whether it's installed and can install it from the official release.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

SETUP_URL = "https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
SETTINGS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SteelSeriesTemps", "settings.json")


def installed_version():
    """PawnIO's version string, or None if it isn't installed (or not on Windows)."""
    if sys.platform != "win32":
        return None
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                return str(winreg.QueryValueEx(key, "DisplayVersion")[0])
        except OSError:
            continue
    return None


def load_settings(path=SETTINGS):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data, path=SETTINGS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def install(url=SETUP_URL, run=subprocess.run):
    """Download the official PawnIO setup and run it. Returns True on success."""
    target = os.path.join(tempfile.gettempdir(), "PawnIO_setup.exe")
    print("Downloading PawnIO setup from the official release...")
    urllib.request.urlretrieve(url, target)
    try:
        print("Running PawnIO setup (Windows will ask for permission)...")
        return run([target, "-install"]).returncode == 0
    finally:
        try:
            os.remove(target)
        except OSError:
            pass


def offer_install(ask=input, settings_path=SETTINGS, installer=install):
    """Ask once whether to install PawnIO. Returns True if it got installed.

    A "no" is remembered so the question isn't repeated on every start;
    `SteelSeriesTemps.exe --install-driver` asks again.
    """
    settings = load_settings(settings_path)
    if settings.get("pawnio_declined"):
        return False
    print("\nCPU temperature needs the free PawnIO driver (the signed driver LibreHardwareMonitor uses).")
    answer = ask("Install PawnIO now? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        settings["pawnio_declined"] = True
        save_settings(settings, settings_path)
        print("OK. Run SteelSeriesTemps.exe --install-driver if you change your mind.")
        return False
    ok = installer()
    print("PawnIO installed." if ok else "PawnIO setup didn't finish; CPU temperature stays unavailable.")
    return ok
