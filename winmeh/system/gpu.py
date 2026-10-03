"""GPU + VRAM detection.

WMI's Win32_VideoController.AdapterRAM is a 32-bit field and caps at 4 GB, so it
is wrong for most modern cards. We read, in order of accuracy:
  1. nvidia-smi (exact, NVIDIA only)
  2. the display-adapter class key in the registry, value
     HardwareInformation.qwMemorySize (64-bit, all vendors, Win10+)
  3. HardwareInformation.MemorySize (32-bit fallback)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass

from . import reg

DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
CREATE_NO_WINDOW = 0x08000000


@dataclass
class GPU:
    name: str
    vram_bytes: int
    source: str
    driver: str = ""

    @property
    def vram_gb(self) -> float:
        return round(self.vram_bytes / 1024**3, 1)

    @property
    def is_integrated(self) -> bool:
        n = self.name.lower()
        return ("intel" in n and "arc" not in n) or "radeon(tm) graphics" in n or n.endswith("radeon graphics")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["vram_gb"] = self.vram_gb
        d["integrated"] = self.is_integrated
        return d


def _as_int(raw) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bytes):
        return int.from_bytes(raw[:8], "little")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def from_nvidia_smi() -> list[GPU]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=CREATE_NO_WINDOW if _win() else 0,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_smi(out)


def parse_nvidia_smi(out: str) -> list[GPU]:
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            gpus.append(GPU(parts[0], int(parts[1]) * 1024**2, "nvidia-smi", parts[2] if len(parts) > 2 else ""))
    return gpus


def from_registry() -> list[GPU]:
    gpus = []
    for sub in reg.subkeys(reg.HKLM, DISPLAY_CLASS):
        if not sub.isdigit():
            continue
        v = reg.values(reg.HKLM, DISPLAY_CLASS + "\\" + sub)
        name = v.get("DriverDesc") or v.get("HardwareInformation.AdapterString")
        if isinstance(name, bytes):
            name = name.decode("utf-16-le", "ignore").rstrip("\x00")
        if not name or "basic display" in str(name).lower() or "virtual" in str(name).lower():
            continue
        mem = _as_int(v.get("HardwareInformation.qwMemorySize")) or _as_int(v.get("HardwareInformation.MemorySize"))
        gpus.append(GPU(str(name), mem, "registry", str(v.get("DriverVersion", ""))))
    return gpus


def detect() -> list[GPU]:
    reg_gpus = from_registry()
    nv = from_nvidia_smi()
    # nvidia-smi is exact: replace registry entries for the same card.
    merged = list(nv)
    for g in reg_gpus:
        if not any(n.name.lower() in g.name.lower() or g.name.lower() in n.name.lower() for n in nv):
            merged.append(g)
    # Dedicated cards first, then by VRAM.
    merged.sort(key=lambda g: (g.is_integrated, -g.vram_bytes))
    return merged


def _win() -> bool:
    import sys
    return sys.platform == "win32"
