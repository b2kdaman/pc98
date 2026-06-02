# Agent Notes

## Project Shape

This is a Windows-focused PC-98 launcher and translation wrapper. The root
entrypoint is `game-launcher.bat`; implementation code lives in `scripts/`.

Important local folders:

- `emulator/`: user-provided Neko Project 21/W files.
- `games-rard/`: user-owned `.rar`/`.zip` game catalog.
- `disks/`: extraction cache and user disk images.
- `models/`: local GGUF and mmproj model cache.

Do not commit emulator binaries, BIOS files, commercial game archives, extracted
disk images, screenshots, downloaded models, or local launcher state.

## Main Workflows

- Launcher: `python scripts\game-launcher.py`
- Batch entrypoint: `.\game-launcher.bat`
- Direct emulator runner: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-pc98.ps1`
- Translator: `python scripts\translate-screenshot.py --watch`
- Local LLM bootstrap: `python scripts\local_llm.py status`

The launcher scans `.rar` and `.zip` files under `games-rard/`, extracts
selected archives into `disks\catalog\`, launches Neko Project through
`run-pc98.ps1`, starts a local `llama-server` when needed, and opens the
translation watcher.

## Launcher State

`launcher-state.json` is local runtime state and ignored by git. It stores:

- `recent`: recently launched games with play counts.
- `favorites`: archive paths toggled with `Space`.

Favorites are displayed with ASCII `*`. Keep state loading backward compatible:
missing or invalid keys should degrade to empty lists.

## Translation Runtime

LM Studio is no longer required. `scripts/local_llm.py` manages a local
OpenAI-compatible `llama-server` on `http://127.0.0.1:8080/v1` by default.

The image translation path requires both:

- `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf`
- `mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf`

Both live under ignored `models/` by default. The launcher may stop only the
`llama-server` process it started itself; it should not kill an already-running
server owned by the user.

The launcher should start `llama-server` with GPU offload when a device is
available. Device selection is automatic by default, preferring CUDA, then
NVIDIA/Vulkan, then the first reported device. Respect `LOCAL_LLM_DEVICE`,
`LOCAL_LLM_GPU_LAYERS`, `LOCAL_LLM_SERVER_PATH`, and `LOCAL_LLM_SERVER_ARGS`
overrides. The winget package may expose Vulkan rather than CUDA; do not claim
CUDA is active unless the selected `llama-server` reports CUDA devices.

## UI Conventions

Keep the launcher console-native and dependency-free. The current style uses:

- ANSI colors with a plain-text fallback.
- ASCII-only panels and markers.
- Best-effort Windows console font setup.
- Arrow keys for navigation, Left/Right for pagination, `Space` for favorites,
  `Enter` for launch/select, and Esc/Backspace for back.

## Verification

Before finishing launcher or translator changes, run:

```powershell
python -m py_compile .\scripts\game-launcher.py .\scripts\translate-screenshot.py .\scripts\local_llm.py
```

For launcher-only state changes, also run a small import/state smoke test or an
equivalent non-interactive check that old state files still load.

Use `git status --short` before committing. Keep generated caches and local
runtime artifacts out of commits.
