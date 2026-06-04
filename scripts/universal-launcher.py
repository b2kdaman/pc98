import argparse
import ctypes
import msvcrt
import os
import subprocess
import sys
from pathlib import Path

import local_llm


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR
PAGE_SIZE = 18
KEY_UP = "up"
KEY_DOWN = "down"
KEY_LEFT = "left"
KEY_RIGHT = "right"
KEY_ENTER = "enter"
KEY_ESC = "esc"
KEY_BACKSPACE = "backspace"


user32 = ctypes.windll.user32


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rect)]
user32.GetWindowRect.restype = ctypes.c_bool


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def read_key():
    key = msvcrt.getwch()
    if key in ("\r", "\n"):
        return KEY_ENTER
    if key == "\x1b":
        return KEY_ESC
    if key == "\b":
        return KEY_BACKSPACE
    if key in ("\x00", "\xe0"):
        extended = msvcrt.getwch()
        return {
            "H": KEY_UP,
            "P": KEY_DOWN,
            "K": KEY_LEFT,
            "M": KEY_RIGHT,
        }.get(extended, "")
    return key


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def window_size(hwnd):
    rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def enum_visible_windows():
    windows = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        title = window_title(hwnd)
        if not title:
            return True
        lower = title.lower()
        if "translation" in lower or "universal translator launcher" in lower:
            return True

        width, height = window_size(hwnd)
        if width < 160 or height < 120:
            return True

        windows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "size": f"{width}x{height}",
            }
        )
        return True

    user32.EnumWindows(callback, 0)
    windows.sort(key=lambda item: item["title"].lower())
    return windows


def print_window_list():
    for window in enum_visible_windows():
        print(f"0x{window['hwnd']:X}\t{window['size']}\t{window['title']}")


def pick_window():
    selected = 0
    page = 0

    while True:
        windows = enum_visible_windows()
        if not windows:
            clear_screen()
            print("Universal Translator Launcher")
            print("")
            print("No visible windows found.")
            print("Press Esc to quit or any other key to refresh.")
            key = read_key()
            if key == KEY_ESC:
                return None
            continue

        total_pages = max((len(windows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        page = min(page, total_pages - 1)
        selected = min(selected, len(windows) - 1)
        page_start = page * PAGE_SIZE
        page_end = min(page_start + PAGE_SIZE, len(windows))
        visible = windows[page_start:page_end]

        clear_screen()
        print("Universal Translator Launcher")
        print(
            f"{len(windows)} window(s) | Up/Down select | Left/Right page | "
            "R refresh | Enter attach | Esc quit"
        )
        print("")

        for offset, window in enumerate(visible):
            absolute = page_start + offset
            marker = ">" if absolute == selected else " "
            print(
                f"{marker} {window['title']} "
                f"[{window['size']}] hwnd=0x{window['hwnd']:X}"
            )

        key = read_key()
        if key == KEY_UP and selected > 0:
            selected -= 1
            page = selected // PAGE_SIZE
        elif key == KEY_DOWN and selected < len(windows) - 1:
            selected += 1
            page = selected // PAGE_SIZE
        elif key == KEY_LEFT and page > 0:
            page -= 1
            selected = page * PAGE_SIZE
        elif key == KEY_RIGHT and page < total_pages - 1:
            page += 1
            selected = page * PAGE_SIZE
        elif key in {"r", "R"}:
            continue
        elif key == KEY_ENTER:
            return windows[selected]
        elif key in {KEY_ESC, KEY_BACKSPACE}:
            return None


def start_translator(window, managed_process_pids=None):
    command = [
        sys.executable,
        str(SCRIPT_DIR / "translate-screenshot.py"),
        "--watch",
        "--hwnd",
        str(window["hwnd"]),
        "--title",
        window["title"],
    ]
    for pid in managed_process_pids or []:
        command.extend(["--managed-process-pid", str(pid)])
    creation_flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=ROOT, creationflags=creation_flags)


def main():
    parser = argparse.ArgumentParser(
        description="Attach the local translation overlay to any visible window."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print visible target windows and exit.",
    )
    args = parser.parse_args()

    if args.list:
        print_window_list()
        return 0

    window = pick_window()
    if not window:
        return 0

    clear_screen()
    print(f"Attaching translator to: {window['title']}")
    print(f"Window handle: 0x{window['hwnd']:X}")
    print("")
    print("Preparing local GGUF translation runtime...")
    local_llm_process = local_llm.ensure_ready()
    managed_process_pids = [local_llm_process.pid] if local_llm_process else []
    print("Starting translation watcher...")
    start_translator(window, managed_process_pids)
    print("")
    print("Attached. Right-click inside the target window to translate.")
    print("The overlay will close when the target window closes.")
    print("")
    print("Press any key to exit this launcher.")
    read_key()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
