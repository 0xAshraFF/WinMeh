import json

from winmeh.system import apps, cache, files, games, gpu


# ------------------------------------------------------------------ gpu
def test_parse_nvidia_smi():
    g = gpu.parse_nvidia_smi("NVIDIA GeForce RTX 3060, 12288, 560.94\n")
    assert g[0].name == "NVIDIA GeForce RTX 3060" and g[0].vram_gb == 12.0 and not g[0].is_integrated


def test_qword_vram_from_bytes():
    assert gpu._as_int((8 * 1024**3).to_bytes(8, "little")) == 8 * 1024**3


def test_integrated_detection():
    assert gpu.GPU("Intel(R) UHD Graphics 620", 128 * 1024**2, "registry").is_integrated
    assert not gpu.GPU("Intel(R) Arc(TM) A770", 16 * 1024**3, "registry").is_integrated


# ------------------------------------------------------------------ files
def test_parse_query_synonyms_and_kind():
    terms, kind = files.parse_query("where is my wedding photo")
    assert kind == "picture"
    assert "wedding" in terms[0] and "nikah" in terms[0]


def test_index_finds_wedding_by_folder_and_synonym(tmp_path):
    pics = tmp_path / "Pictures"
    (pics / "Wedding 2021").mkdir(parents=True)
    (pics / "Wedding 2021" / "IMG_0042.JPG").write_bytes(b"x")
    (pics / "Nikah_stage.png").write_bytes(b"x")
    (pics / "wedding_invoice.pdf").write_bytes(b"x")       # not a picture
    (pics / "cat.jpg").write_bytes(b"x")
    idx = files.FileIndex(tmp_path / "f.db")
    assert idx.build([str(pics)]) == 4
    hits, src = files.search("wher is my wedding photo", idx)
    names = {h.name for h in hits}
    assert names == {"IMG_0042.JPG", "Nikah_stage.png"}
    assert src == "winmeh-index"
    assert hits[0].name == "Nikah_stage.png"                # term in filename ranks above folder match


def test_index_skips_junk_dirs(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "wedding.jpg").write_bytes(b"x")
    idx = files.FileIndex(tmp_path / "f.db")
    assert idx.build([str(tmp_path)]) == 0 or not files.search("wedding photo", idx)[0]


# ------------------------------------------------------------------ cache
def test_clear_path_keeps_folder_and_reports_size(tmp_path):
    d = tmp_path / "Cache"
    (d / "sub").mkdir(parents=True)
    (d / "a.bin").write_bytes(b"x" * 1000)
    (d / "sub" / "b.bin").write_bytes(b"x" * 500)
    freed, skipped = cache.clear_path(str(d))
    assert freed == 1500 and skipped == 0
    assert d.exists() and not any(d.iterdir())


def test_scan_finds_browser_cache(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    c = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    c.mkdir(parents=True)
    (c / "data_1").write_bytes(b"x" * 2048)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("TEMP", str(tmp_path / "Temp"))
    (tmp_path / "Temp").mkdir()
    found = {t.key: t for t in cache.scan()}
    assert found["chrome"].size == 2048


# ------------------------------------------------------------------ vlc
def test_vlcrc_uncomments_existing_key():
    src = "[qt] # Qt interface\n#qt-updates-notif=1\n#qt-privacy-ask=1\n[core]\nfoo=1\n"
    out = apps.apply_vlcrc(src)
    assert "qt-updates-notif=0" in out and "qt-privacy-ask=0" in out
    assert "#qt-updates-notif" not in out and "foo=1" in out


def test_vlcrc_adds_missing_keys_into_qt_section():
    out = apps.apply_vlcrc("[core]\nfoo=1\n")
    assert "[qt]" in out and "qt-updates-notif=0" in out


def test_disable_vlc_writes_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    rc = tmp_path / "vlc" / "vlcrc"
    rc.parent.mkdir()
    rc.write_text("[qt]\n#qt-updates-notif=1\n")
    ok, msg = apps.disable_vlc_update_popup()
    assert ok and "qt-updates-notif=0" in rc.read_text()
    assert list(rc.parent.glob("vlcrc.winmeh-backup-*"))


# ------------------------------------------------------------------ games
VDF = '''"libraryfolders"
{
    "0" { "path" "%s" "apps" { "570" "1" } }
}'''
ACF = '''"AppState"
{
    "appid"  "%s"
    "name"   "%s"
    "installdir" "%s"
    "SizeOnDisk" "%d"
}'''


def test_steam_discovery(tmp_path):
    root = tmp_path / "Steam"
    sa = root / "steamapps"
    sa.mkdir(parents=True)
    (sa / "libraryfolders.vdf").write_text(VDF % str(root).replace("\\", "\\\\"))
    (sa / "appmanifest_570.acf").write_text(ACF % ("570", "Dota 2", "dota 2 beta", 40 * 1024**3))
    (sa / "appmanifest_228980.acf").write_text(ACF % ("228980", "Steamworks Common Redistributables", "x", 1))
    gs = games.steam_games(str(root))
    assert [g.name for g in gs] == ["Dota 2"] and gs[0].size_gb == 40.0


def test_epic_discovery(tmp_path):
    (tmp_path / "a.item").write_text(json.dumps({"DisplayName": "Fortnite", "InstallLocation": "C:/F",
                                                 "AppCategories": ["public", "games"], "InstallSize": 0}))
    (tmp_path / "b.item").write_text(json.dumps({"DisplayName": "Unreal Engine", "AppCategories": ["engines"]}))
    assert [g.name for g in games.epic_games(str(tmp_path))] == ["Fortnite"]


def test_requirements_parse_and_judge():
    html = ("<strong>Minimum:</strong><ul><li><strong>OS:</strong> Windows 10</li>"
            "<li><strong>Memory:</strong> 12 GB RAM</li>"
            "<li><strong>Graphics:</strong> NVIDIA GTX 1060 6 GB VRAM or AMD RX 580</li>"
            "<li><strong>Storage:</strong> 60 GB available space</li></ul>")
    req = games.parse_requirements(html)
    assert req["ram_gb"] == 12 and req["vram_gb"] == 6 and req["storage_gb"] == 60
    assert games.judge(req, ram_gb=16, vram_gb=8)[0] == "ok"
    verdict, notes = games.judge(req, ram_gb=8, vram_gb=4)
    assert verdict == "below" and any("RAM" in n for n in notes)


def test_gpu_tier():
    assert games.gpu_tier(12, False) == "enthusiast"
    assert games.gpu_tier(6, False) == "mid"
    assert games.gpu_tier(2, True) == "light"


def test_virtual_adapters_are_filtered():
    assert any(v in "microsoft hyper-v video" for v in gpu.VIRTUAL_ADAPTERS)
    assert not any(v in "nvidia geforce rtx 4060 laptop gpu" for v in gpu.VIRTUAL_ADAPTERS)
