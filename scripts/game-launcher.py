import argparse
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


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR
CATALOG_DIR = ROOT / "games-rard"
CACHE_DIR = ROOT / "disks" / "catalog"
STATE_PATH = ROOT / "launcher-state.json"
RECENT_LIMIT = 10
PAGE_SIZE = 24

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
KEY_OTHER = "other"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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

    archives = sorted(CATALOG_DIR.rglob("*.rar"), key=lambda p: friendly_name(p).lower())
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
        return {"recent": []}

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"recent": []}

    if not isinstance(state, dict):
        return {"recent": []}

    recent = state.get("recent", [])
    if not isinstance(recent, list):
        recent = []

    return {"recent": [item for item in recent if isinstance(item, dict)]}


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

    return subprocess.Popen(command, cwd=ROOT)


def start_translator():
    command = [sys.executable, str(SCRIPT_DIR / "translate-screenshot.py"), "--watch"]
    creation_flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=ROOT, creationflags=creation_flags)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


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
        }.get(extended, KEY_OTHER)
    return key


def draw_header(title, subtitle=None):
    print(title)
    if subtitle:
        print(subtitle)
    print("")


def pick_from_items(title, items, *, subtitle=None, item_label=None, page_size=PAGE_SIZE):
    if not items:
        clear_screen()
        draw_header(title, subtitle)
        print("No games to show.")
        print("\nPress any key to go back.")
        read_key()
        return None

    selected = 0
    page = 0
    total_pages = max((len(items) + page_size - 1) // page_size, 1)
    label = item_label or (lambda item: item["name"])

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
            or f"{len(items)} item(s) | Page {page + 1}/{total_pages} | Up/Down select | Left/Right page | Enter launch | Esc back",
        )

        for offset, item in enumerate(visible):
            absolute = page_start + offset
            marker = ">" if absolute == selected else " "
            print(f"{marker} {label(item)}")

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
        elif key == KEY_ENTER:
            return items[selected]
        elif key in {KEY_ESC, KEY_BACKSPACE}:
            return None


def prompt_search_query():
    query = ""
    while True:
        clear_screen()
        draw_header("Search Catalog", "Type to filter | Enter search | Esc back")
        print(f"Search: {query}")
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
    print(f"\nPreparing {archive['name']}...")
    cache_dir, extracted = extract_archive(archive)
    print(f"{'Extracted to' if extracted else 'Using cached'}: {cache_dir}")

    all_images, selected_images = find_images(cache_dir)
    if not selected_images:
        raise RuntimeError(
            "No supported PC-98 disk images were found after extraction.\n"
            f"Archive: {archive['path']}\n"
            f"Cache: {cache_dir}"
        )

    print("Mounting:")
    for image in selected_images:
        print(f"  {image.name}")

    extra_count = len(all_images) - len(selected_images)
    if extra_count > 0:
        print(f"{extra_count} additional image(s) remain in the cache for manual swapping.")

    run_powershell_script(SCRIPT_DIR / "run-pc98.ps1", ["-Image", *selected_images])
    print("Starting LM Studio translation watcher...")
    start_translator()
    update_recent(state, archive)
    print("Launched. The translator is running in a separate console window.")
    print("\nPress any key to return to the launcher.")
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
    )
    return selected["archive"] if selected else None


def search_catalog(catalog):
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
        subtitle=f"{len(matches)} match(es) for '{query}' | Up/Down select | Left/Right page | Enter launch | Esc back",
    )


def browse_catalog(catalog):
    return pick_from_items("Full Catalog", catalog)


def main_menu(catalog, state):
    catalog_by_path = {str(archive["path"]): archive for archive in catalog}
    menu_items = [
        ("Recent games", lambda: show_recent(state, catalog_by_path)),
        ("Search catalog", lambda: search_catalog(catalog)),
        ("Browse full catalog", lambda: browse_catalog(catalog)),
        ("Quit", None),
    ]
    selected = 0

    while True:
        clear_screen()
        draw_header(
            "PC-98 Game Launcher",
            f"{len(catalog)} games found | Up/Down select | Enter choose | Esc quit",
        )
        for index, (label, _) in enumerate(menu_items):
            marker = ">" if index == selected else " "
            print(f"{marker} {label}")

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
            print(f"\nCould not launch {archive['name']}:\n{error}\n", file=sys.stderr)
            print("Press any key to return to the launcher.")
            read_key()


def parse_args():
    parser = argparse.ArgumentParser(description="PC-98 RAR catalog launcher.")
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
    args = parse_args()
    catalog = scan_catalog()
    if not catalog:
        print(f"No .rar games found in {CATALOG_DIR}")
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
            print(f"No game matched: {args.launch}")
            return 1
        if args.no_launch:
            cache_dir, extracted = extract_archive(matches[0])
            all_images, selected_images = find_images(cache_dir)
            if not selected_images:
                print("No supported PC-98 disk images were found after extraction.")
                return 1
            update_recent(state, matches[0])
            print(f"{'Extracted' if extracted else 'Cached'}: {matches[0]['name']}")
            print(f"Selected images: {len(selected_images)} of {len(all_images)}")
            return 0
        launch_archive(matches[0], state)
        return 0

    main_menu(catalog, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
