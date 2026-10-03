import datetime as dt
import json

import pytest

from winmeh.config import Settings
from winmeh.core import bootstrap
from winmeh.core.assistant import Assistant
from winmeh.core.router import route
from winmeh.system import drivers, gpu, packages, web

WINGET = """   - \r   \\ \r
Name                     Id                         Version  Match        Source
---------------------------------------------------------------------------------
VLC media player         VideoLAN.VLC               3.0.21   Moniker: vlc winget
VLC UWP                  9NBLGGH4VVNH               Unknown               msstore
"""


def test_parse_winget_table():
    p = packages.parse_winget_table(WINGET)
    assert [(x.name, x.id, x.version) for x in p][0] == ("VLC media player", "VideoLAN.VLC", "3.0.21")
    assert len(p) == 2


def test_parse_flatpak():
    p = packages.parse_flatpak_search("VLC\torg.videolan.VLC\t3.0.21\nNo matches found\n")
    assert [(x.name, x.id) for x in p] == [("VLC", "org.videolan.VLC")]


def test_known_package_is_instant(monkeypatch):
    monkeypatch.setattr(packages, "available_managers", lambda: ["winget"])
    assert packages.find("Google Chrome")[0].id == "Google.Chrome"


def test_install_commands_are_noninteractive():
    assert "--silent" in packages.Package("VLC", "VideoLAN.VLC", "winget").install_cmd()
    assert packages.Package("vlc", "vlc", "apt").install_cmd()[:2] == ["pkexec", "apt-get"]


def test_parse_problem_devices_skips_disabled():
    js = json.dumps([{"Name": "Wi-Fi", "ConfigManagerErrorCode": 28, "PNPDeviceID": "PCI\\X"},
                     {"Name": "Old webcam", "ConfigManagerErrorCode": 22}])
    p = drivers.parse_problem_json(js)
    assert [(x.name, x.reason) for x in p] == [("Wi-Fi", "drivers are not installed")]
    assert drivers.parse_problem_json('{"Name":"x","ConfigManagerErrorCode":43}')[0].code == 43
    assert drivers.parse_problem_json("") == []


def test_driver_date_and_vendor():
    assert drivers.parse_driver_date("7-14-2021") == dt.date(2021, 7, 14)
    assert drivers.parse_driver_date("garbage") is None
    assert "nvidia.com" in drivers.vendor_page("NVIDIA GeForce GTX 1050")


def test_lspci_missing_driver():
    out = """00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 620
\tSubsystem: Dell Device 07e6
\tKernel driver in use: i915
02:00.0 Network controller: Broadcom Inc. BCM4360 802.11ac Wireless
\tSubsystem: Apple Inc. Device 0117
\tKernel modules: bcma, wl
"""
    p = drivers.parse_lspci_missing(out)
    assert len(p) == 1 and "BCM4360" in p[0].name


def test_ubuntu_drivers_parse():
    u = drivers.parse_ubuntu_drivers("nvidia-driver-550, (kernel modules provided by linux-modules-nvidia)\n")
    assert u[0].title == "nvidia-driver-550"


def test_linux_gpu_from_sysfs(tmp_path, monkeypatch):
    dev = tmp_path / "card0" / "device"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")
    (dev / "mem_info_vram_total").write_text(str(8 * 1024**3))
    monkeypatch.setattr(gpu.shutil, "which", lambda n: None)
    g = gpu.from_linux(str(tmp_path))
    assert g[0].name == "AMD GPU" and g[0].vram_gb == 8.0


def test_lspci_gpu_names():
    assert gpu.parse_lspci_gpus("01:00.0 VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060] (rev a1)") \
        == ["NVIDIA Corporation GA106 [GeForce RTX 3060]"]


def test_site_urls():
    assert web.site_url("youtube") == "https://www.youtube.com"
    assert web.site_url("example.org") == "https://example.org"
    assert web.site_url("my cat") is None
    assert "search_query=lofi+music" in web.search_url("lofi music", "youtube")


@pytest.mark.parametrize("text,intent", [
    ("check my drivers", "drivers"), ("are my drivers up to date", "drivers"), ("my wifi device not working", "drivers"),
    ("install vlc", "install"), ("download google chrome for me", "install"), ("please install spotify", "install"),
    ("google best budget laptop", "web_search"), ("search youtube for lofi", "web_search"),
    ("look up weather in Dhaka", "web_search"), ("go to github.com", "open_site"), ("2", "pick"),
    ("get me the games list that I can run in this machine", "games"), ("download drivers", "drivers"),
    ("search for my resume", "find_file"),
])
def test_new_routes(text, intent):
    assert route(text).name == intent


def test_llama_asset_choice():
    assets = [{"name": "llama-b6000-bin-win-cuda-12.4-x64.zip"}, {"name": "llama-b6000-bin-win-cpu-x64.zip"},
              {"name": "llama-b6000-bin-ubuntu-x64.zip"}]
    assert bootstrap.pick_asset(assets, ["bin-win-cpu-x64.zip"])["name"].endswith("win-cpu-x64.zip")
    assert bootstrap.pick_asset(assets, ["bin-ubuntu-x64.zip", "bin-ubuntu-x64.tar.gz"])["name"].endswith("ubuntu-x64.zip")


def test_bundled_models_found_first(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "m.gguf").write_bytes(b"x")
    monkeypatch.setattr(bootstrap, "app_dir", lambda: tmp_path)
    assert bootstrap.find_gguf("m.gguf") == tmp_path / "models" / "m.gguf"


def test_install_asks_first_and_offers_alternatives(monkeypatch):
    monkeypatch.setattr(packages, "available_managers", lambda: ["winget"])
    monkeypatch.setattr(packages, "find", lambda n: [packages.Package("Foo", "A.Foo", "winget"),
                                                     packages.Package("Foo Lite", "A.FooLite", "winget")])
    installed = []
    monkeypatch.setattr(packages, "install", lambda p: (installed.append(p.id) or True, f"Installed {p.name}."))
    a = Assistant(Settings(), None)
    out = {}
    a.handle("install foo", lambda k, p: out.__setitem__(k, p))
    assert "act:confirm" in out["html"] and "act:2" in out["html"] and installed == []
    a.handle("2", lambda k, p: out.__setitem__(k, p))
    assert installed == ["A.FooLite"] and "Installed Foo Lite" in out["html"]


def test_llama_asset_fallback_skips_gpu_builds(monkeypatch):
    monkeypatch.setattr(bootstrap, "IS_WINDOWS", True)
    assets = [{"name": "llama-cuda-win-x64-b9000.zip"}, {"name": "llama-win-x64-b9000.zip"}, {"name": "nightly-tag.txt"}]
    assert bootstrap.pick_asset(assets, ["bin-win-cpu-x64.zip"])["name"] == "llama-win-x64-b9000.zip"
    assert bootstrap.pick_asset([{"name": "nightly-tag.txt"}], ["bin-win-cpu-x64.zip"]) is None
