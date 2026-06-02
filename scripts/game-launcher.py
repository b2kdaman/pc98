import argparse
import ctypes
import hashlib
import json
import msvcrt
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import local_llm


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR
CATALOG_DIR = ROOT / "games-rard"
CACHE_DIR = ROOT / "disks" / "catalog"
STATE_PATH = ROOT / "launcher-state.json"
VERSION = "1.0.0"
RECENT_LIMIT = 10
PAGE_SIZE = 24
ARCHIVE_EXTENSIONS = {".rar", ".zip"}

HARD_DISK_EXTENSIONS = {".hdi", ".thd", ".nhd", ".hdd", ".hdn", ".vhd"}
FLOPPY_EXTENSIONS = {
    ".d88",
    ".88d",
    ".d98",
    ".98d",
    ".fdi",
    ".fdd",
    ".2hd",
    ".tfd",
    ".hdm",
    ".xdf",
    ".dup",
    ".flp",
    ".img",
    ".ima",
}
SUPPORTED_IMAGE_EXTENSIONS = HARD_DISK_EXTENSIONS | FLOPPY_EXTENSIONS
KEY_UP = "up"
KEY_DOWN = "down"
KEY_LEFT = "left"
KEY_RIGHT = "right"
KEY_ENTER = "enter"
KEY_ESC = "esc"
KEY_BACKSPACE = "backspace"
KEY_SPACE = "space"
KEY_OTHER = "other"

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
LF_FACESIZE = 32


class ConsoleCoord(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class ConsoleFontInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("nFont", ctypes.c_ulong),
        ("dwFontSize", ConsoleCoord),
        ("FontFamily", ctypes.c_uint),
        ("FontWeight", ctypes.c_uint),
        ("FaceName", ctypes.c_wchar * LF_FACESIZE),
    ]


class Color:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    cyan = "\033[36m"
    bright_cyan = "\033[96m"
    magenta = "\033[95m"
    yellow = "\033[93m"
    green = "\033[92m"
    red = "\033[91m"
    white = "\033[97m"
    selected = "\033[30;103m"


USE_COLOR = False


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def enable_console_style():
    global USE_COLOR
    USE_COLOR = bool(sys.stdout.isatty())
    if os.name != "nt":
        return

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(
                handle,
                mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING,
            )
    except Exception:
        pass


def set_console_font():
    if os.name != "nt":
        return

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        for face_name in ("Cascadia Mono", "Cascadia Code", "Consolas"):
            font = ConsoleFontInfoEx()
            font.cbSize = ctypes.sizeof(ConsoleFontInfoEx)
            font.dwFontSize = ConsoleCoord(0, 20)
            font.FontFamily = 54
            font.FontWeight = 600
            font.FaceName = face_name
            if kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(font)):
                return
    except Exception:
        pass


def paint(text, *codes):
    if not USE_COLOR or not codes:
        return text
    return "".join(codes) + text + Color.reset


def accent(text):
    return paint(text, Color.bright_cyan, Color.bold)


def friendly_name(path):
    return path.stem


def safe_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:90] or "game"


def archive_id(path):
    relative = str(path.relative_to(ROOT)).lower()
    return hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]


def cache_path_for(path):
    return CACHE_DIR / f"{safe_name(friendly_name(path))}-{archive_id(path)}"


def scan_catalog():
    if not CATALOG_DIR.exists():
        return []

    archives = sorted(
        (
            path
            for path in CATALOG_DIR.rglob("*")
            if path.is_file() and path.suffix.lower() in ARCHIVE_EXTENSIONS
        ),
        key=lambda p: (friendly_name(p).lower(), str(p).lower()),
    )
    return [
        {
            "name": friendly_name(path),
            "path": path,
            "relative": str(path.relative_to(ROOT)),
        }
        for path in archives
    ]


def load_state():
    if not STATE_PATH.exists():
        return {"recent": [], "favorites": []}

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"recent": [], "favorites": []}

    if not isinstance(state, dict):
        return {"recent": [], "favorites": []}

    recent = state.get("recent", [])
    if not isinstance(recent, list):
        recent = []

    favorites = state.get("favorites", [])
    if not isinstance(favorites, list):
        favorites = []

    return {
        "recent": [item for item in recent if isinstance(item, dict)],
        "favorites": [item for item in favorites if isinstance(item, str)],
    }


def save_state(state):
    temp_path = STATE_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temp_path.replace(STATE_PATH)


def update_recent(state, archive):
    archive_path = str(archive["path"])
    existing = {}
    for item in state.get("recent", []):
        if item.get("archivePath") == archive_path:
            existing = item
            break

    play_count = int(existing.get("playCount", 0)) + 1 if existing else 1
    new_item = {
        "name": archive["name"],
        "archivePath": archive_path,
        "relativePath": archive["relative"],
        "lastPlayedAt": datetime.now(timezone.utc).isoformat(),
        "playCount": play_count,
    }

    remaining = [
        item
        for item in state.get("recent", [])
        if item.get("archivePath") != archive_path
    ]
    state["recent"] = [new_item] + remaining
    save_state(state)


def archive_path_key(archive):
    return str(archive["path"])


def is_favorite(state, archive):
    return archive_path_key(archive) in set(state.get("favorites", []))


def toggle_favorite(state, archive):
    archive_path = archive_path_key(archive)
    favorites = [item for item in state.get("favorites", []) if isinstance(item, str)]
    if archive_path in favorites:
        favorites = [item for item in favorites if item != archive_path]
    else:
        favorites.append(archive_path)
    state["favorites"] = favorites
    save_state(state)


def recent_archives(state, catalog_by_path):
    results = []
    for item in state.get("recent", []):
        archive = catalog_by_path.get(item.get("archivePath"))
        if archive:
            results.append((archive, item))
        if len(results) >= RECENT_LIMIT:
            break
    return results


def source_signature(path):
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def read_cache_meta(cache_dir):
    meta_path = cache_dir / ".launcher-meta.json"
    if not meta_path.exists():
        return {}

    try:
        with meta_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}


def write_cache_meta(cache_dir, archive):
    meta = {
        "archivePath": str(archive["path"]),
        "relativePath": archive["relative"],
        "signature": source_signature(archive["path"]),
        "extractedAt": datetime.now(timezone.utc).isoformat(),
    }
    with (cache_dir / ".launcher-meta.json").open("w", encoding="utf-8") as file:
        json.dump(meta, file, indent=2, ensure_ascii=False)
        file.write("\n")


def is_cache_current(cache_dir, archive):
    if not cache_dir.exists():
        return False

    meta = read_cache_meta(cache_dir)
    return meta.get("signature") == source_signature(archive["path"])


def remove_cache_dir(cache_dir):
    resolved_cache = cache_dir.resolve()
    resolved_root = CACHE_DIR.resolve()
    if resolved_cache == resolved_root or resolved_root not in resolved_cache.parents:
        raise RuntimeError(f"Refusing to remove unexpected cache path: {resolved_cache}")
    shutil.rmtree(resolved_cache)


def extract_archive(archive):
    cache_dir = cache_path_for(archive["path"])
    if is_cache_current(cache_dir, archive):
        return cache_dir, False

    if cache_dir.exists():
        remove_cache_dir(cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    command = ["tar", "-xf", str(archive["path"]), "-C", str(cache_dir)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        remove_cache_dir(cache_dir)
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Archive extraction failed.\n"
            f"Archive: {archive['path']}\n"
            f"Details: {details or 'tar exited without details.'}"
        )

    write_cache_meta(cache_dir, archive)
    return cache_dir, True


def image_sort_key(path):
    name = path.name.lower()
    disk_match = re.search(r"(?:disk|disc|vol|volume|#)?\s*0*(\d+)", name)
    disk_number = int(disk_match.group(1)) if disk_match else 9999
    return disk_number, name


def find_images(cache_dir):
    images = [
        path
        for path in cache_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    images.sort(key=image_sort_key)

    hard_disks = [path for path in images if path.suffix.lower() in HARD_DISK_EXTENSIONS]
    floppies = [path for path in images if path.suffix.lower() in FLOPPY_EXTENSIONS]

    if hard_disks:
        selected = [hard_disks[0]]
        selected.extend(floppies[:4])
    else:
        selected = floppies[:4]

    return images, selected


def run_powershell_script(script_path, args=None):
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if args:
        command.extend(str(arg) for arg in args)

    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(details or f"PowerShell script failed: {script_path}")
    return result.stdout.strip()


def extract_process_id(output):
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def start_translator(emulator_pid=None, managed_process_pids=None):
    command = [sys.executable, str(SCRIPT_DIR / "translate-screenshot.py"), "--watch"]
    if emulator_pid:
        command.extend(["--emulator-pid", str(emulator_pid)])
    for pid in managed_process_pids or []:
        command.extend(["--managed-process-pid", str(pid)])
    creation_flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=ROOT, creationflags=creation_flags)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def read_key():
    key = msvcrt.getwch()
    if key in ("\r", "\n"):
        return KEY_ENTER
    if key == " ":
        return KEY_SPACE
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
        }.get(extended, KEY_OTHER)
    return key


def draw_header(title, subtitle=None):
    width = 78
    top = "+" + "-" * (width - 2) + "+"
    print(paint(top, Color.magenta))
    title_text = title[: width - 4].ljust(width - 4)
    print(paint("|", Color.magenta) + " " + accent(title_text) + paint(" |", Color.magenta))
    if subtitle:
        subtitle_text = subtitle[: width - 4].ljust(width - 4)
        print(
            paint("|", Color.magenta)
            + " "
            + paint(subtitle_text, Color.yellow)
            + paint(" |", Color.magenta)
        )
    print(paint(top, Color.magenta))
    print("")


def draw_status(text):
    print(paint(text, Color.dim, Color.cyan))


def pick_from_items(
    title,
    items,
    *,
    subtitle=None,
    item_label=None,
    page_size=PAGE_SIZE,
    item_archive=None,
    is_favorite_item=None,
    toggle_favorite_item=None,
    refresh_items=None,
):
    if not items:
        clear_screen()
        draw_header(title, subtitle)
        print(paint("No games to show.", Color.yellow))
        print(paint("\nPress any key to go back.", Color.dim))
        read_key()
        return None

    selected = 0
    page = 0
    total_pages = max((len(items) + page_size - 1) // page_size, 1)
    label = item_label or (lambda item: item["name"])
    archive_for = item_archive or (lambda item: item)

    while True:
        page = min(page, total_pages - 1)
        selected = min(max(selected, page * page_size), len(items) - 1)
        page_start = page * page_size
        page_end = min(page_start + page_size, len(items))
        visible = items[page_start:page_end]

        clear_screen()
        draw_header(
            title,
            subtitle
            or f"{len(items)} game(s) | Page {page + 1}/{total_pages} | Up/Down select | Left/Right page | Space favorite | Enter launch | Esc back",
        )

        for offset, item in enumerate(visible):
            absolute = page_start + offset
            marker = ">" if absolute == selected else " "
            archive = archive_for(item)
            favorite = bool(is_favorite_item and is_favorite_item(archive))
            star = "*" if favorite else " "
            row = f"{marker} {star} {label(item)}"
            if absolute == selected:
                print(paint(row, Color.selected, Color.bold))
            elif favorite:
                print(f"{marker} {paint(star, Color.yellow, Color.bold)} {paint(label(item), Color.white)}")
            else:
                print(paint(row, Color.cyan))

        print("")
        draw_status("Hint: Space toggles the * favorite marker.")

        key = read_key()
        if key == KEY_UP:
            if selected > 0:
                selected -= 1
                page = selected // page_size
        elif key == KEY_DOWN:
            if selected < len(items) - 1:
                selected += 1
                page = selected // page_size
        elif key == KEY_LEFT:
            if page > 0:
                page -= 1
                selected = page * page_size
        elif key == KEY_RIGHT:
            if page < total_pages - 1:
                page += 1
                selected = page * page_size
        elif key == KEY_SPACE:
            if toggle_favorite_item:
                toggle_favorite_item(archive_for(items[selected]))
                if refresh_items:
                    items = refresh_items()
                    if not items:
                        return None
                    total_pages = max((len(items) + page_size - 1) // page_size, 1)
                    selected = min(selected, len(items) - 1)
                    page = min(page, total_pages - 1)
        elif key == KEY_ENTER:
            return items[selected]
        elif key in {KEY_ESC, KEY_BACKSPACE}:
            return None


def prompt_search_query():
    query = ""
    while True:
        clear_screen()
        draw_header("Search Catalog", "Type to filter | Enter search | Esc back")
        print(f"{paint('Search:', Color.magenta, Color.bold)} {paint(query, Color.white)}")
        key = read_key()
        if key == KEY_ENTER:
            return query.strip()
        if key in {KEY_ESC, KEY_BACKSPACE} and not query:
            return ""
        if key == KEY_BACKSPACE:
            query = query[:-1]
        elif len(key) == 1 and key.isprintable():
            query += key


def launch_archive(archive, state):
    print(paint(f"\nPreparing {archive['name']}...", Color.cyan, Color.bold))
    cache_dir, extracted = extract_archive(archive)
    print(
        f"{paint('Extracted to' if extracted else 'Using cached', Color.green)}: "
        f"{paint(str(cache_dir), Color.white)}"
    )

    all_images, selected_images = find_images(cache_dir)
    if not selected_images:
        raise RuntimeError(
            "No supported PC-98 disk images were found after extraction.\n"
            f"Archive: {archive['path']}\n"
            f"Cache: {cache_dir}"
        )

    print(paint("Mounting:", Color.magenta, Color.bold))
    for image in selected_images:
        print(f"  {paint(image.name, Color.yellow)}")

    extra_count = len(all_images) - len(selected_images)
    if extra_count > 0:
        print(
            paint(
                f"{extra_count} additional image(s) remain in the cache for manual swapping.",
                Color.dim,
            )
        )

    print(paint("Preparing local GGUF translation runtime...", Color.cyan))
    local_llm_process = local_llm.ensure_ready()
    emulator_output = run_powershell_script(
        SCRIPT_DIR / "run-pc98.ps1",
        ["-Image", *selected_images],
    )
    emulator_pid = extract_process_id(emulator_output)
    print(paint("Starting local translation watcher...", Color.cyan))
    managed_process_pids = [local_llm_process.pid] if local_llm_process else []
    start_translator(emulator_pid, managed_process_pids)
    update_recent(state, archive)
    print(paint("Launched. The translator is running in a separate console window.", Color.green))
    print(paint("\nPress any key to return to the launcher.", Color.dim))
    read_key()


def show_recent(state, catalog_by_path):
    recent = recent_archives(state, catalog_by_path)
    recent_items = []
    for archive, item in recent:
        play_count = item.get("playCount", 0)
        label = f"{archive['name']} ({play_count} play{'s' if play_count != 1 else ''})"
        recent_items.append({"archive": archive, "label": label})

    selected = pick_from_items(
        "Recent Games",
        recent_items,
        item_label=lambda item: item["label"],
        page_size=RECENT_LIMIT,
        item_archive=lambda item: item["archive"],
        is_favorite_item=lambda archive: is_favorite(state, archive),
        toggle_favorite_item=lambda archive: toggle_favorite(state, archive),
    )
    return selected["archive"] if selected else None


def favorite_archives(state, catalog):
    favorites = set(state.get("favorites", []))
    return [archive for archive in catalog if archive_path_key(archive) in favorites]


def show_favorites(state, catalog):
    return pick_from_items(
        "Favorite Games",
        favorite_archives(state, catalog),
        subtitle="Favorited games | Up/Down select | Left/Right page | Space unfavorite | Enter launch | Esc back",
        is_favorite_item=lambda archive: is_favorite(state, archive),
        toggle_favorite_item=lambda archive: toggle_favorite(state, archive),
        refresh_items=lambda: favorite_archives(state, catalog),
    )


def search_catalog(catalog, state):
    query = prompt_search_query().lower()
    if not query:
        return None

    matches = [
        archive
        for archive in catalog
        if query in archive["name"].lower() or query in archive["relative"].lower()
    ]
    return pick_from_items(
        "Search Results",
        matches,
        subtitle=f"{len(matches)} match(es) for '{query}' | Up/Down select | Left/Right page | Space favorite | Enter launch | Esc back",
        is_favorite_item=lambda archive: is_favorite(state, archive),
        toggle_favorite_item=lambda archive: toggle_favorite(state, archive),
    )


def browse_catalog(catalog, state):
    return pick_from_items(
        "Full Catalog",
        catalog,
        is_favorite_item=lambda archive: is_favorite(state, archive),
        toggle_favorite_item=lambda archive: toggle_favorite(state, archive),
    )


def main_menu(catalog, state):
    catalog_by_path = {str(archive["path"]): archive for archive in catalog}
    menu_items = [
        ("Recent games", lambda: show_recent(state, catalog_by_path)),
        ("Favorite games", lambda: show_favorites(state, catalog)),
        ("Search catalog", lambda: search_catalog(catalog, state)),
        ("Browse full catalog", lambda: browse_catalog(catalog, state)),
        ("Quit", None),
    ]
    selected = 0

    while True:
        clear_screen()
        draw_header(
            f"PC-98 Game Launcher v{VERSION}",
            f"{len(catalog)} games found | Up/Down select | Enter choose | Esc quit",
        )
        for index, (label, _) in enumerate(menu_items):
            marker = ">" if index == selected else " "
            row = f"{marker} {label}"
            if index == selected:
                print(paint(row, Color.selected, Color.bold))
            else:
                print(paint(row, Color.cyan))

        key = read_key()
        if key == KEY_UP:
            selected = max(selected - 1, 0)
            continue
        if key == KEY_DOWN:
            selected = min(selected + 1, len(menu_items) - 1)
            continue
        if key == KEY_ESC:
            return
        if key != KEY_ENTER:
            continue

        label, action = menu_items[selected]
        if action is None:
            return
        archive = action()
        if not archive:
            continue

        try:
            clear_screen()
            launch_archive(archive, state)
        except Exception as error:
            print(
                paint(f"\nCould not launch {archive['name']}:\n{error}\n", Color.red),
                file=sys.stderr,
            )
            print(paint("Press any key to return to the launcher.", Color.dim))
            read_key()


def parse_args():
    parser = argparse.ArgumentParser(description="PC-98 archive catalog launcher.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"PC-98 Game Launcher {VERSION}",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the scanned catalog and exit.",
    )
    parser.add_argument(
        "--find",
        help="Print catalog matches for a search term and exit.",
    )
    parser.add_argument(
        "--launch",
        help="Launch the first exact or partial game-name match without opening the menu.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="With --launch, prepare/extract/update state but do not start emulator or translator.",
    )
    return parser.parse_args()


def main():
    enable_console_style()
    set_console_font()
    args = parse_args()
    catalog = scan_catalog()
    if not catalog:
        print(paint(f"No supported .rar or .zip games found in {CATALOG_DIR}", Color.red))
        return 1

    state = load_state()

    if args.list:
        for archive in catalog:
            print(archive["name"])
        return 0

    if args.find:
        query = args.find.lower()
        for archive in catalog:
            if query in archive["name"].lower() or query in archive["relative"].lower():
                print(archive["name"])
        return 0

    if args.launch:
        query = args.launch.lower()
        matches = [
            archive
            for archive in catalog
            if archive["name"].lower() == query
        ] or [
            archive
            for archive in catalog
            if query in archive["name"].lower()
        ]
        if not matches:
            print(paint(f"No game matched: {args.launch}", Color.red))
            return 1
        if args.no_launch:
            cache_dir, extracted = extract_archive(matches[0])
            all_images, selected_images = find_images(cache_dir)
            if not selected_images:
                print(paint("No supported PC-98 disk images were found after extraction.", Color.red))
                return 1
            update_recent(state, matches[0])
            print(paint(f"{'Extracted' if extracted else 'Cached'}: {matches[0]['name']}", Color.green))
            print(paint(f"Selected images: {len(selected_images)} of {len(all_images)}", Color.cyan))
            return 0
        launch_archive(matches[0], state)
        return 0

    main_menu(catalog, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
