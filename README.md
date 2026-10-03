# WinMeh

A small, fast, local-first assistant for Windows 10/11 and Linux. It sits on your desktop as a draggable glass widget. You can type or talk to it, and it learns about your PC: specs, files, installed games and apps. Nothing leaves your machine except optional Steam requirement lookups.

![WinMeh widget](docs/screenshot.png)
<sub>This screenshot was rendered offscreen without the native blur. On Windows 10/11 the panel uses the system acrylic blur, so your wallpaper shows through.</sub>

## What it does

| You say | What happens | Speed |
|---|---|---|
| "how much is my **vram**?" | It reads VRAM from `nvidia-smi` or the 64-bit registry value (`HardwareInformation.qwMemorySize`). WMI's `AdapterRAM` caps at 4 GB, so it isn't used. | instant (no AI) |
| "wher is my **wedding photo**" | It searches the Windows Search index (file names, folder names, tags, titles), then falls back to WinMeh's own file index. It expands synonyms (wedding → marriage, bride, nikah, shaadi, biye, holud, reception…), groups results by folder, and gives you *open* and *show in folder* links. | instant |
| "how to **clear the cache**" | It scans user temp, Windows temp, the thumbnail cache, Chrome, Edge, Brave and Firefox caches and the DNS cache, then shows the sizes. **Nothing is deleted until you confirm.** Only cache *contents* are removed, and files that are in use get skipped. | ~1 s scan |
| "get me the **games** I can run" | It finds installed games from Steam, Epic, GOG, Ubisoft and Xbox, rates your PC's gaming tier, and checks each Steam game's published minimum RAM and VRAM. | instant + ~1 s online |
| "can I run **Elden Ring**?" | It looks up the game's Steam minimum requirements and compares them with your RAM and VRAM. | ~1 s online |
| "stop the **VLC** update message" | It sets `qt-updates-notif=0` (and `qt-privacy-ask=0`) in `%APPDATA%\vlc\vlcrc` and keeps a backup of the old file. | instant |
| "check my **drivers**" | It lists devices with problems (Device Manager error codes), shows how old the graphics driver is with a link to the maker's own page, and asks Windows Update for driver updates. It installs them only after you confirm, behind a normal Windows admin prompt. On Linux it uses `ubuntu-drivers` and `fwupd`. **It never uses third-party "driver updater" sites.** | ~5–60 s |
| "**install** vlc", "download chrome" | It looks the app up in **winget** (Windows) or **flatpak / apt / dnf / pacman** (Linux), shows you what it found and installs after you confirm. Popular apps resolve instantly, and if the match is ambiguous you pick from a list. It never downloads from random websites. | instant + install time |
| "**google** best budget laptop", "search youtube for lofi", "**go to** github.com", "open youtube" | Opens the search or site in your default browser. | instant |
| "open spotify", "what are my specs", "how much space is left", "remember that…", "rescan my pc" | built-in commands | instant |
| anything else | It streams an answer from a small local LLM that is given your machine profile as context. | first token in about 0.1–0.5 s on CPU |

### Why it's fast
1. **No AI for machine questions.** A regex intent router (under 1 ms) sends commands straight to native code. The LLM only handles open-ended chat.
2. **Everything is pre-loaded.** The machine profile is cached, the file index is built in the background, and the speech and language models load and warm up while the widget is already on screen.
3. **Small models.**
   - **LLM:** Qwen2.5-0.5B-Instruct Q4_K_M (~400 MB), served by llama.cpp's `llama-server`. GPU offload is on by default through the Vulkan build. You can switch to Qwen2.5-1.5B with `--model 1.5b`, and any Ollama or LM Studio model is detected automatically.
   - **Speech-to-text:** faster-whisper `tiny.en` with int8 on CPU (~75 MB), greedy decoding, and an energy-based end-of-speech detector that stops 0.7 s after you stop talking.
   - **Text-to-speech:** the built-in Windows SAPI voices. There's nothing to download, and a new reply interrupts the old one.

## Install

### Windows 10 / 11: one click
Download **`WinMeh-Setup.exe`** from the [Releases page](https://github.com/0xAshraFF/WinMeh/releases) and run it. The installer:
- doesn't ask for admin rights,
- needs no Python, Ollama or downloads afterwards, because the chat model (Qwen2.5-0.5B), the llama.cpp engine and the voice model are all inside it,
- starts WinMeh when you sign in (you can untick this).

### Linux (x86-64, glibc 2.35+ e.g. Ubuntu 22.04+, Fedora 36+, Debian 12+)
```bash
curl -fsSL https://raw.githubusercontent.com/0xAshraFF/WinMeh/main/installer/install.sh | bash
```
Or download `WinMeh-linux-x64.tar.gz` from Releases, extract it and run `./install.sh`. It needs no root, adds a menu entry and autostart, and bundles the same models. Remove it with `./install.sh --uninstall`.

Linux has no universal global-hotkey API (Wayland forbids it), so add two shortcuts in your desktop's keyboard settings: `winmeh --talk` and `winmeh --toggle`.

### From source (any OS, for development)
```bash
git clone https://github.com/0xAshraFF/WinMeh && cd WinMeh
python -m pip install -r requirements.txt
python -m winmeh            # on first start it downloads the chat model (~450 MB) by itself
```
Already running **Ollama** with a model pulled? WinMeh uses it automatically.

### Building the installers
`python scripts/build.py --with-models` produces `dist/WinMeh/` with the models bundled. CI then wraps it with Inno Setup (`installer/winmeh.iss`) or as a tarball (`installer/install.sh`). Pushing a `v*` tag publishes both to GitHub Releases.

### Windows 7 / 8
Windows 7 and 8 aren't supported. Qt 6, Python 3.9+, the speech engine and current llama.cpp all need Windows 10. A Win7 "lite" build (Python 3.8 + Qt 5, no voice) is possible but not done yet. Contributions are welcome.

## Using it
- **Drag** the widget by its header. It snaps to screen edges and remembers where you left it.
- **📍 / 📌** switches between living on the desktop layer (like a widget) and staying always on top.
- **Ctrl+Alt+Space** (Windows) or your `winmeh --talk` shortcut (Linux) starts talking, and pressing it again stops early. Recording also ends by itself when you stop speaking.
- **Ctrl+Alt+W** shows or hides the widget. **Esc** hides it.
- **Tray icon:** speak replies on/off, start with Windows, re-learn this PC, quit.
- **Headless:** `winmeh --ask "how much vram do I have"`, `winmeh --selftest`. With the widget running, `winmeh --talk`, `--toggle` and `--quit` control it.

Settings are in `%LOCALAPPDATA%\WinMeh\settings.json`. The data stored next to it is `profile.json` (machine profile), `files.db` (file-name index) and `memory.json` (things you asked it to remember).

## Project layout
```
winmeh/
  core/router.py      instant intent rules
  core/assistant.py   skills + LLM fallback
  core/llm.py         OpenAI-compatible streaming client, manages llama-server
  core/bootstrap.py   finds bundled models, or downloads them on first run
  system/             profile, gpu, files, games, cache, apps, drivers, packages, web
  voice/              stt (faster-whisper), tts (SAPI), hotkey (RegisterHotKey)
  ui/                 glass widget + native acrylic blur
scripts/build.py      PyInstaller build (+ --with-models)
installer/            Inno Setup script (Windows), install.sh (Linux)
tests/                pytest; CI runs them on Windows and Linux, plus a real-Windows self-test and an exe build
```

## Honest limitations
- **Photo search matches names, folders and tags, not image content.** If your wedding photos are all called `IMG_1234.jpg` in a folder named `Camera`, it won't find them yet. Semantic image search with a small CLIP model is the top roadmap item.
- **"Can I run it" only compares RAM and VRAM** against Steam's published minimums. It shows the required GPU model but doesn't benchmark it against yours. Non-Steam games are listed without a check.
- **The 0.5B model is fast but basic.** It's fine for short chat, weak for reasoning. Use `--model 1.5b`, or point `llm_url` at a bigger model.
- **"Live on the desktop" uses the bottom window layer.** Pressing **Win+D** still hides it. Use the 📌 mode or Ctrl+Alt+W to bring it back.
- **Voice is push-to-talk (hotkey or mic button).** There's no always-on wake word yet.
- **Driver updates come from Windows Update only.** If the maker hasn't published a driver there, WinMeh links to the maker's site instead of installing it itself.
- **The Linux build is x86-64 only for now**, and it was tested on GitHub's Ubuntu runners. Other distros should work but haven't been verified.
- Development and CI happen on GitHub's Windows runners. Latency numbers on your own hardware will vary, so please share yours.

## Roadmap
- Semantic photo search (CLIP / SigLIP, on-device)
- Optional wake word ("hey WinMeh", openWakeWord)
- GPU benchmark table for "can I run it"
- More fixes like the VLC one (Adobe/Java updaters, startup apps, notifications)

## Contributing
PRs are welcome. Adding a skill takes three steps: add a rule in `core/router.py`, add a `do_<intent>` method in `core/assistant.py`, and add a test. Run `python -m pytest`.

## License
MIT
