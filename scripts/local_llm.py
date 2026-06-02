import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR
MODELS_DIR = ROOT / "models"

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL_ID = "local-gemma-gguf"
MODEL_REPO = "HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive"
MODEL_FILENAME = "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
MMPROJ_FILENAME = "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf"
MODEL_URL = (
    "https://huggingface.co/"
    f"{MODEL_REPO}/resolve/main/{MODEL_FILENAME}?download=true"
)
MMPROJ_URL = (
    "https://huggingface.co/"
    f"{MODEL_REPO}/resolve/main/{MMPROJ_FILENAME}?download=true"
)
MODEL_MIN_BYTES = 5_000_000_000
MMPROJ_MIN_BYTES = 900_000_000


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def env_base_url():
    return os.environ.get("LOCAL_LLM_BASE_URL", DEFAULT_BASE_URL)


def env_model_path():
    return Path(os.environ.get("LOCAL_LLM_MODEL_PATH", MODELS_DIR / MODEL_FILENAME))


def env_mmproj_path():
    return Path(os.environ.get("LOCAL_LLM_MMPROJ_PATH", MODELS_DIR / MMPROJ_FILENAME))


def env_model_id():
    return os.environ.get("LOCAL_LLM_MODEL", DEFAULT_MODEL_ID)


def api_url(base_url, path):
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def request_json(url, timeout=10):
    request = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, body, timeout=120):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def server_reachable(base_url=None):
    try:
        request_json(api_url(base_url or env_base_url(), "/models"), timeout=5)
        return True
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def warmup_server(base_url=None):
    base_url = base_url or env_base_url()
    body = {
        "model": env_model_id(),
        "messages": [
            {
                "role": "user",
                "content": "Warm up. Reply with OK.",
            }
        ],
        "max_tokens": 4,
        "temperature": 0,
    }
    print("Warming up local LLM...")
    try:
        post_json(api_url(base_url, "/chat/completions"), body, timeout=180)
        print("Local LLM warmup complete.")
    except Exception as error:
        print(f"Local LLM warmup failed, continuing anyway: {error}")


def find_llama_server():
    found = shutil.which("llama-server")
    if found:
        return found

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Packages")
    for env_name in ["ProgramFiles", "ProgramFiles(x86)"]:
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    for root in candidates:
        if root and root.exists():
            matches = list(root.rglob("llama-server.exe"))
            if matches:
                return str(matches[0])
    return None


def install_llama_cpp():
    if find_llama_server():
        return

    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("winget is required to auto-install llama.cpp, but it was not found.")

    commands = [
        [winget, "install", "llama.cpp", "--accept-package-agreements", "--accept-source-agreements"],
        [winget, "install", "--id", "ggml.llama.cpp", "-e", "--accept-package-agreements", "--accept-source-agreements"],
    ]
    last_error = ""
    for command in commands:
        print("Installing llama.cpp with winget...")
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0 and find_llama_server():
            return
        last_error = (result.stderr or result.stdout or "").strip()

    raise RuntimeError(f"llama.cpp installation did not expose llama-server.\n{last_error}")


def file_is_present(path, min_bytes):
    file_path = Path(path)
    return file_path.exists() and file_path.stat().st_size >= min_bytes


def model_is_present(path=None):
    model_path = Path(path or env_model_path())
    return file_is_present(model_path, MODEL_MIN_BYTES)


def mmproj_is_present(path=None):
    mmproj_path = Path(path or env_mmproj_path())
    return file_is_present(mmproj_path, MMPROJ_MIN_BYTES)


def download_large_file(url, destination, min_bytes, label, dry_run=False):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if file_is_present(destination, min_bytes):
        print(f"{label} already present: {destination}")
        return destination

    if dry_run:
        print(f"Would download {label}:\n  {url}\ninto:\n  {destination}")
        return destination

    partial_path = destination.with_suffix(destination.suffix + ".part")
    existing_size = partial_path.stat().st_size if partial_path.exists() else 0
    headers = {}
    mode = "wb"
    if existing_size:
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"

    print(f"Downloading {label} to {destination}")

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if existing_size and getattr(response, "status", None) == 200:
                existing_size = 0
                mode = "wb"

            output = partial_path.open(mode)
            with output:
                total_header = response.headers.get("Content-Length")
                total_remaining = int(total_header) if total_header and total_header.isdigit() else 0
                downloaded = existing_size
                last_report = time.time()
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_report >= 2:
                        if total_remaining:
                            expected_total = existing_size + total_remaining
                            percent = min(downloaded / expected_total * 100, 100)
                            print(f"Downloaded {downloaded / 1_000_000_000:.2f} GB ({percent:.1f}%)")
                        else:
                            print(f"Downloaded {downloaded / 1_000_000_000:.2f} GB")
                        last_report = now
    except urllib.error.HTTPError as error:
        if existing_size and error.code == 416 and file_is_present(partial_path, min_bytes):
            pass
        else:
            raise

    if partial_path.stat().st_size < min_bytes:
        raise RuntimeError(
            f"Downloaded {label} is unexpectedly small: {partial_path.stat().st_size} bytes"
        )

    partial_path.replace(destination)
    print(f"{label} ready: {destination}")
    return destination


def download_model(dry_run=False):
    return download_large_file(
        os.environ.get("LOCAL_LLM_MODEL_URL", MODEL_URL),
        env_model_path(),
        MODEL_MIN_BYTES,
        "model GGUF",
        dry_run=dry_run,
    )


def download_mmproj(dry_run=False):
    return download_large_file(
        os.environ.get("LOCAL_LLM_MMPROJ_URL", MMPROJ_URL),
        env_mmproj_path(),
        MMPROJ_MIN_BYTES,
        "mmproj GGUF",
        dry_run=dry_run,
    )


def download_assets(dry_run=False):
    model_path = download_model(dry_run=dry_run)
    mmproj_path = download_mmproj(dry_run=dry_run)
    return model_path, mmproj_path


def parse_host_port(base_url):
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080
    return host, port


def start_llama_server(base_url=None):
    base_url = base_url or env_base_url()
    if server_reachable(base_url):
        return None

    install_llama_cpp()
    model_path, mmproj_path = download_assets()
    llama_server = find_llama_server()
    if not llama_server:
        raise RuntimeError("llama-server was not found after installation.")

    host, port = parse_host_port(base_url)
    command = [
        llama_server,
        "-m",
        str(model_path),
        "--mmproj",
        str(mmproj_path),
        "--host",
        host,
        "--port",
        str(port),
        "--alias",
        env_model_id(),
    ]
    creation_flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    process = subprocess.Popen(command, cwd=ROOT, creationflags=creation_flags)

    print("Waiting for llama-server to become ready...")
    deadline = time.time() + 180
    while time.time() < deadline:
        if server_reachable(base_url):
            print(f"llama-server is ready at {base_url}")
            warmup_server(base_url)
            return process
        time.sleep(2)

    raise RuntimeError("llama-server did not become ready within 180 seconds.")


def ensure_ready(base_url=None, dry_run=False):
    base_url = base_url or env_base_url()
    if server_reachable(base_url):
        print(f"Local LLM server already reachable at {base_url}")
        warmup_server(base_url)
        return None

    if dry_run:
        print("Local LLM server is not reachable.")
        print(f"Would ensure llama.cpp, model, and llama-server for {base_url}")
        print(f"Model path: {env_model_path()}")
        print(f"mmproj path: {env_mmproj_path()}")
        return None

    return start_llama_server(base_url)


def main():
    parser = argparse.ArgumentParser(description="Bootstrap local GGUF translation runtime.")
    parser.add_argument("command", choices=["status", "download", "ensure", "start"])
    parser.add_argument("--dry-run", action="store_true", help="Do not install, download, or start anything.")
    parser.add_argument("--base-url", default=env_base_url())
    args = parser.parse_args()

    if args.command == "status":
        print(f"llama-server: {find_llama_server() or 'not found'}")
        print(f"model: {env_model_path()}")
        print(f"model present: {model_is_present()}")
        print(f"mmproj: {env_mmproj_path()}")
        print(f"mmproj present: {mmproj_is_present()}")
        print(f"server reachable: {server_reachable(args.base_url)}")
        return 0
    if args.command == "download":
        download_assets(dry_run=args.dry_run)
        return 0
    if args.command == "ensure":
        ensure_ready(args.base_url, dry_run=args.dry_run)
        return 0
    if args.command == "start":
        if args.dry_run:
            ensure_ready(args.base_url, dry_run=True)
        else:
            start_llama_server(args.base_url)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
