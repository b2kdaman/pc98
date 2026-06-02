# PC-98 Catalog Launcher

Small Windows launcher for a local PC-98 emulator setup. It scans a user-owned
RAR/ZIP catalog, extracts selected games into a local cache, launches Neko
Project 21/W, and opens a local GGUF-powered translation overlay.

This repository intentionally does not include emulator binaries, BIOS ROMs,
commercial game archives, disk images, extracted media, screenshots, or local
play history.

## Local Layout

Create or keep these folders next to the scripts:

```text
emulator/      Neko Project 21/W files, including np21x64w.exe and np21x64w.ini
games-rard/    Your legally obtained .rar/.zip game catalog
disks/         Local extraction cache and your own disk images
models/        Local GGUF model cache
```

The root `game-launcher.bat` is the main entrypoint. Implementation scripts live
under `scripts/`.

## Run

Browse and launch games from `games-rard/`:

```powershell
.\game-launcher.bat
```

Controls:

- Up/Down: move through menus and game lists
- Left/Right: page through game lists
- Space: mark/unmark the highlighted game as a favorite
- Enter: select or launch
- Esc/Backspace: go back

The catalog launcher:

- scans `.rar` and `.zip` files recursively under `games-rard/`
- displays names without the archive suffix
- stores recent play history and favorites in `launcher-state.json`
- extracts selected archives into `disks\catalog\`
- mounts detected PC-98 disk images through `scripts\run-pc98.ps1`
- starts `scripts\translate-screenshot.py --watch`

## Translation Overlay

LM Studio is not required. On game launch, the app checks for `llama-server`,
installs `llama.cpp` with `winget` if needed, downloads the Gemma Q4_K_M GGUF
model plus its multimodal projection file into `models/`, starts a local
OpenAI-compatible server, and opens the translation overlay.

The first game launch may download about 6.33 GB:

```text
HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive
Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf
```

The overlay attaches below the emulator window. Right-click over the emulator
window to translate the current emulator screen. The right-click event is
consumed so the emulator does not also receive that click.
The translation overlay has its own scrollbar; scrolling the overlay only moves
the translated text and does not trigger a new translation.

Screenshots are kept in memory and are not saved to disk.

Optional local LLM settings:

```powershell
set LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
set LOCAL_LLM_MODEL=local-gemma-gguf
set LOCAL_LLM_MODEL_PATH=C:\path\to\model.gguf
set LOCAL_LLM_MMPROJ_PATH=C:\path\to\mmproj.gguf
python scripts\translate-screenshot.py --watch
```

Manual runtime checks:

```powershell
python scripts\local_llm.py status
python scripts\local_llm.py download --dry-run
python scripts\local_llm.py ensure
```

The local LLM request uses structured JSON with:

- `sourceLanguage`
- `targetLanguage`
- `sourceText`
- `translatedText`
- `style.backgroundColor`
- `style.textColor`
- `notes`

The translation overlay applies the detected text-box colors when the model
returns valid `#RRGGBB` values.

## Direct Emulator Launch

Launch the emulator with no image:

```powershell
.\scripts\run-pc98.ps1
```

Launch with a hard disk or floppy image:

```powershell
.\scripts\run-pc98.ps1 .\disks\game.hdi
.\scripts\run-pc98.ps1 .\disks\disk1.d88 .\disks\disk2.d88
```

If script execution is blocked:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-pc98.ps1
```

Supported image extensions include `.hdi`, `.nhd`, `.hdd`, `.hdn`, `.vhd`,
`.d88`, `.fdi`, `.hdm`, `.xdf`, `.fdd`, `.2hd`, `.img`, and `.ima`.
