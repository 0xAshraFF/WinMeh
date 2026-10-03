"""Driver health: broken devices, pending driver updates, old GPU drivers.

Only official sources: Windows Update on Windows; ubuntu-drivers / fwupd on Linux;
links to NVIDIA/AMD/Intel's own pages for GPUs. Never third-party "driver updater" sites.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field

from . import proc, reg
from .gpu import DISPLAY_CLASS, VIRTUAL_ADAPTERS

IS_WINDOWS = proc.IS_WINDOWS

# Device Manager problem codes people actually hit.
PROBLEM_CODES = {
    1: "not configured correctly", 3: "driver may be corrupted", 10: "device cannot start",
    18: "drivers need reinstalling", 19: "registry problem", 22: "device is disabled",
    24: "device not present or missing drivers", 28: "drivers are not installed",
    31: "Windows can't load the drivers", 39: "driver is corrupted or missing", 43: "device reported a problem",
    45: "device is not connected", 52: "driver isn't digitally signed",
}

GPU_VENDOR_PAGES = {
    "nvidia": "https://www.nvidia.com/Download/index.aspx",
    "amd": "https://www.amd.com/en/support/download/drivers.html",
    "radeon": "https://www.amd.com/en/support/download/drivers.html",
    "intel": "https://www.intel.com/content/www/us/en/support/detect.html",
}


@dataclass
class Problem:
    name: str
    code: int
    device_id: str = ""

    @property
    def reason(self) -> str:
        return PROBLEM_CODES.get(self.code, f"problem code {self.code}")


@dataclass
class DriverUpdate:
    title: str
    size_mb: float = 0.0


@dataclass
class GpuDriver:
    name: str
    version: str
    date: dt.date | None
    vendor_page: str = ""

    @property
    def age_days(self) -> int | None:
        return (dt.date.today() - self.date).days if self.date else None


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    updates: list[DriverUpdate] = field(default_factory=list)
    updates_checked: bool = False
    gpu: list[GpuDriver] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    install_cmd: list[str] | None = None       # Linux: command that installs the updates


# ------------------------------------------------------------------ Windows
PROBLEMS_PS = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 } | "
               "Select-Object Name, ConfigManagerErrorCode, PNPDeviceID | ConvertTo-Json -Compress")


def parse_problem_json(text: str) -> list[Problem]:
    text = text.strip()
    if not text.startswith(("[", "{")):
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        code = int(d.get("ConfigManagerErrorCode") or 0)
        if code == 22:      # disabled on purpose by the user - not a driver problem
            continue
        out.append(Problem(d.get("Name") or d.get("PNPDeviceID") or "Unknown device", code, d.get("PNPDeviceID", "")))
    return out


def windows_problems() -> list[Problem]:
    rc, out = proc.powershell(PROBLEMS_PS, timeout=40)
    return parse_problem_json(out) if rc == 0 else []


def windows_driver_updates(timeout: float = 90) -> tuple[list[DriverUpdate], bool]:
    """Ask Windows Update which driver updates are offered. Returns (updates, finished_in_time)."""
    result: dict = {}

    def work():
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
            pythoncom.CoInitialize()
            searcher = win32com.client.Dispatch("Microsoft.Update.Session").CreateUpdateSearcher()
            searcher.Online = True
            found = searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
            ups = []
            for i in range(found.Updates.Count):
                u = found.Updates.Item(i)
                size = 0.0
                try:
                    size = float(u.MaxDownloadSize) / 1024**2
                except Exception:
                    pass
                ups.append(DriverUpdate(str(u.Title), round(size, 1)))
            result["ups"] = ups
        except Exception as e:
            result["err"] = str(e)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive() or "err" in result:
        return [], False
    return result["ups"], True


def parse_driver_date(raw: str | None) -> dt.date | None:
    """Registry DriverDate is 'M-D-YYYY'."""
    if not raw:
        return None
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", str(raw))
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def vendor_page(name: str) -> str:
    n = name.lower()
    return next((url for key, url in GPU_VENDOR_PAGES.items() if key in n), "")


def windows_gpu_drivers() -> list[GpuDriver]:
    out = []
    for sub in reg.subkeys(reg.HKLM, DISPLAY_CLASS):
        if not sub.isdigit():
            continue
        v = reg.values(reg.HKLM, DISPLAY_CLASS + "\\" + sub)
        name = str(v.get("DriverDesc") or "")
        if not name or any(w in name.lower() for w in VIRTUAL_ADAPTERS):
            continue
        out.append(GpuDriver(name, str(v.get("DriverVersion", "")), parse_driver_date(v.get("DriverDate")),
                             vendor_page(name)))
    return out


INSTALL_PS = r"""
$ErrorActionPreference = 'Continue'
Write-Host 'WinMeh: installing driver updates from Windows Update...' -ForegroundColor Cyan
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
if ($r.Updates.Count -eq 0) { Write-Host 'No driver updates are waiting.'; Read-Host 'Press Enter to close'; exit }
$c = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) { if (-not $u.EulaAccepted) { $u.AcceptEula() }; [void]$c.Add($u); Write-Host (' - ' + $u.Title) }
Write-Host 'Downloading...'
$d = $s.CreateUpdateDownloader(); $d.Updates = $c; [void]$d.Download()
Write-Host 'Installing...'
$i = $s.CreateUpdateInstaller(); $i.Updates = $c; $res = $i.Install()
$codes = @{2='Succeeded'; 3='Succeeded with errors'; 4='Failed'; 5='Aborted'}
Write-Host ('Result: ' + $codes[[int]$res.ResultCode]) -ForegroundColor Green
if ($res.RebootRequired) { Write-Host 'Restart your PC to finish.' -ForegroundColor Yellow }
Read-Host 'Press Enter to close'
"""


def windows_install_updates() -> bool:
    path = os.path.join(tempfile.gettempdir(), "winmeh-driver-updates.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(INSTALL_PS)
    return proc.run_elevated_script(path)


# ------------------------------------------------------------------ Linux
def parse_ubuntu_drivers(text: str) -> list[DriverUpdate]:
    """`ubuntu-drivers list` prints one package per line, e.g. 'nvidia-driver-550, (kernel modules provided by ...)'."""
    out = []
    for line in text.splitlines():
        pkg = line.split(",")[0].strip()
        if re.match(r"^[a-z0-9][a-z0-9.+\-]+$", pkg):
            out.append(DriverUpdate(pkg))
    return out


def parse_lspci_missing(text: str) -> list[Problem]:
    """Graphics/network/audio devices in `lspci -k` output with no 'Kernel driver in use' line."""
    out = []
    for block in re.split(r"\n(?=\S)", text.strip()):
        head = block.splitlines()[0] if block else ""
        if re.search(r"VGA|3D controller|Network|Ethernet|Wireless|Audio", head) and "Kernel driver in use" not in block:
            out.append(Problem(head.split(" ", 1)[-1], 28))
    return out


def linux_report() -> Report:
    r = Report()
    if proc.which("lspci"):
        rc, out = proc.run(["lspci", "-k"], timeout=10)
        if rc == 0:
            r.problems = parse_lspci_missing(out)
    if proc.which("ubuntu-drivers"):
        rc, out = proc.run(["ubuntu-drivers", "list"], timeout=60)
        if rc == 0:
            r.updates = parse_ubuntu_drivers(out)
            r.updates_checked = True
            if r.updates:
                r.install_cmd = ["pkexec", "ubuntu-drivers", "install"]
    if proc.which("fwupdmgr"):
        rc, out = proc.run(["fwupdmgr", "get-updates", "--json"], timeout=60)
        try:
            devs = json.loads(out).get("Devices", []) if rc == 0 else []
        except ValueError:
            devs = []
        for d in devs:
            for rel in d.get("Releases", [])[:1]:
                r.updates.append(DriverUpdate(f"{d.get('Name', 'Device')} firmware {rel.get('Version', '')}".strip()))
        r.updates_checked = True
        if devs and not r.install_cmd:
            r.install_cmd = ["fwupdmgr", "update", "-y"]
    if not r.updates_checked:
        r.notes.append("Your distro's update tool (Software Updater / Discover / dnf) handles driver updates.")
    return r


# ------------------------------------------------------------------ entry
def report(check_updates: bool = True) -> Report:
    if not IS_WINDOWS:
        return linux_report()
    r = Report(problems=windows_problems(), gpu=windows_gpu_drivers())
    if check_updates:
        r.updates, r.updates_checked = windows_driver_updates()
        if not r.updates_checked:
            r.notes.append("Windows Update didn't answer in time - try again later or open Settings > Windows Update.")
    return r


def install(r: Report) -> tuple[bool, str]:
    if IS_WINDOWS:
        ok = windows_install_updates()
        return ok, ("Started - approve the Windows prompt, then a window shows the progress." if ok
                    else "Couldn't start the installer.")
    if r.install_cmd:
        rc, out = proc.run(r.install_cmd, timeout=1800)
        return rc == 0, ("Done. Restart to use the new drivers." if rc == 0 else f"Install failed: {out[-300:]}")
    return False, "Nothing to install."
