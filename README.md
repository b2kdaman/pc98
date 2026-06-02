# PC-98 Catalog Launcher

Small Windows launcher for a local PC-98 emulator setup. It scans a user-owned
RAR catalog, extracts selected games into a local cache, launches Neko Project
21/W, and opens an LM Studio-powered translation overlay.

This repository intentionally does not include emulator binaries, BIOS ROMs,
commercial game archives, disk images, extracted media, screenshots, or local
play history.

## Local Layout

Create or keep these folders next to the scripts:

```text
emulator/      Neko Project 21/W files, including np21x64w.exe and np21x64w.ini
games-rard/    Your legally obtained .rar game catalog
disks/         Local extraction cache and your own disk images
```

The launcher expects `run-pc98.ps1`, `game-launcher.py`, and
`translate-screenshot.py` to remain in the project root.

## Run

Browse and launch games from `games-rard/`:

```powershell
.\game-launcher.bat
```

Controls:

- Up/Down: move through menus and game lists
- Left/Right: page through game lists
- Enter: select or launch
- Esc/Backspace: go back

The catalog launcher:

- scans `.rar` files recursively under `games-rard/`
- displays names without the `.rar` suffix
- stores recent play history in `launcher-state.json`
- extracts selected archives into `disks\catalog\`
- mounts detected PC-98 disk images through `run-pc98.ps1`
- starts `translate-screenshot.py --watch`

## Translation Overlay

Start LM Studio's local server, load a vision-language model, then launch a
game. The overlay attaches below the emulator window. Scroll over the emulator,
overlay, or anywhere in Windows to translate the current emulator screen.

Screenshots are kept in memory and are not saved to disk.

Optional LM Studio settings:

```powershell
set LMSTUDIO_BASE_URL=http://localhost:1234/v1
set LMSTUDIO_MODEL=your-loaded-vision-model-id
python translate-screenshot.py --watch
```

The LM Studio request uses structured JSON with:

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
.\run-pc98.ps1
```

Launch with a hard disk or floppy image:

```powershell
.\run-pc98.ps1 .\disks\game.hdi
.\run-pc98.ps1 .\disks\disk1.d88 .\disks\disk2.d88
```

If script execution is blocked:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-pc98.ps1
```

Supported image extensions include `.hdi`, `.nhd`, `.hdd`, `.hdn`, `.vhd`,
`.d88`, `.fdi`, `.hdm`, `.xdf`, `.fdd`, `.2hd`, `.img`, and `.ima`.
