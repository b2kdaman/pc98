import argparse
import base64
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import BOTH, END, RIGHT, Y, Frame, Scrollbar, Text, Tk, TclError

from PIL import ImageGrab
import local_llm


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR
DEFAULT_LOCAL_LLM_BASE_URL = local_llm.DEFAULT_BASE_URL
TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "sourceLanguage": {
            "type": "string",
            "description": "The language of the original text, such as Japanese.",
        },
        "targetLanguage": {
            "type": "string",
            "description": "The language the text was translated into, such as English.",
        },
        "sourceText": {
            "type": "string",
            "description": "The original text detected in the screenshot.",
        },
        "translatedText": {
            "type": "string",
            "description": "The translated text.",
        },
        "notes": {
            "type": "string",
            "description": "Brief notes about context, uncertainty, OCR issues, or cultural nuance.",
        },
        "style": {
            "type": "object",
            "description": "Approximate colors of the source text box in the screenshot.",
            "properties": {
                "backgroundColor": {
                    "type": "string",
                    "description": "Dominant background color behind the source text as #RRGGBB.",
                    "pattern": "^#[0-9A-Fa-f]{6}$",
                },
                "textColor": {
                    "type": "string",
                    "description": "Dominant foreground text color as #RRGGBB.",
                    "pattern": "^#[0-9A-Fa-f]{6}$",
                },
            },
            "required": ["backgroundColor", "textColor"],
            "additionalProperties": False,
        },
    },
    "required": [
        "sourceLanguage",
        "targetLanguage",
        "sourceText",
        "translatedText",
        "style",
    ],
    "additionalProperties": False,
}
DEFAULT_STYLE = {"backgroundColor": "#000000", "textColor": "#FFFFFF"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_RBUTTONDOWN = 0x0204
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class MouseHookStruct(ctypes.Structure):
    _fields_ = [
        ("pt", Point),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_ulong),
        ("pt", Point),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes = [
    ctypes.c_ulong,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
kernel32.GetExitCodeProcess.restype = ctypes.c_bool
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool


class GlobalMouseTriggerHook:
    def __init__(self, title_hint):
        self.event = threading.Event()
        self.hook = None
        self.thread = None
        self.thread_id = ctypes.c_ulong(0)
        self.title_hint = title_hint
        self._callback = LowLevelMouseProc(self._handle_mouse)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        if self.thread_id.value:
            user32.PostThreadMessageW(self.thread_id.value, 0x0012, 0, 0)

    def consume(self):
        if not self.event.is_set():
            return False
        self.event.clear()
        return True

    def _run(self):
        self.thread_id = ctypes.c_ulong(kernel32.GetCurrentThreadId())
        self.hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._callback,
            kernel32.GetModuleHandleW(None),
            0,
        )
        if not self.hook:
            return

        message = Msg()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _handle_mouse(self, code, w_param, l_param):
        if code == HC_ACTION and int(w_param) == WM_RBUTTONDOWN:
            mouse = ctypes.cast(l_param, ctypes.POINTER(MouseHookStruct)).contents
            if point_is_in_emulator_client(mouse.pt.x, mouse.pt.y, self.title_hint):
                self.event.set()
                return 1
        return user32.CallNextHookEx(self.hook, code, w_param, l_param)


def enum_windows():
    windows = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            windows.append((hwnd, title))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def find_emulator_window(title_hint):
    hint = title_hint.lower()
    preferred_terms = [hint, "neko project", "np21", "np2"]
    fallback_terms = ["pc-9801", "pc-9800"]

    for hwnd, title in enum_windows():
        lower = title.lower()
        if "translation" in lower:
            continue
        if any(term and term in lower for term in preferred_terms):
            return hwnd, title

    for hwnd, title in enum_windows():
        lower = title.lower()
        if "translation" in lower:
            continue
        if any(term in lower for term in fallback_terms):
            return hwnd, title

    visible = "\n".join(f"- {title}" for _, title in enum_windows()[:20])
    raise RuntimeError(
        "Could not find the emulator window. Start Neko Project first, or pass "
        f"--title with part of its window title.\n\nVisible windows:\n{visible}"
    )


def find_emulator_hwnd_or_none(title_hint):
    try:
        hwnd, _title = find_emulator_window(title_hint)
        return hwnd
    except RuntimeError:
        return None


def point_is_in_emulator_client(x, y, title_hint):
    hwnd = find_emulator_hwnd_or_none(title_hint)
    if not hwnd:
        return False
    left, top, right, bottom = client_bbox(hwnd)
    return left <= x < right and top <= y < bottom


def process_is_running(pid):
    if not pid:
        return True

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(pid),
    )
    if not handle:
        return False

    exit_code = ctypes.c_ulong(0)
    alive = (
        bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
        and exit_code.value == STILL_ACTIVE
    )
    kernel32.CloseHandle(handle)
    return alive


def emulator_is_running(title_hint, emulator_pid):
    if emulator_pid:
        return process_is_running(emulator_pid)
    return find_emulator_hwnd_or_none(title_hint) is not None


def process_command_line(pid):
    if os.name != "nt":
        return ""

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$p = Get-CimInstance Win32_Process "
            f"-Filter \"ProcessId = {int(pid)}\"; "
            "if ($p) { $p.CommandLine }"
        ),
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    return (result.stdout or "").strip()


def managed_process_is_safe_to_stop(pid):
    command_line = process_command_line(pid).lower()
    if not command_line:
        return False

    model_path = str(local_llm.env_model_path()).lower()
    return "llama-server" in command_line and model_path in command_line


def stop_managed_processes(pids):
    for pid in pids or []:
        if not process_is_running(pid):
            continue
        if not managed_process_is_safe_to_stop(pid):
            print(f"Skipping unmanaged process {pid}.")
            continue

        print(f"Stopping managed process {pid}.")
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass


def window_bbox(hwnd):
    rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read emulator window bounds.")

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("Emulator window is minimized or has no visible size.")

    return rect.left, rect.top, rect.right, rect.bottom


def client_bbox(hwnd):
    rect = Rect()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return window_bbox(hwnd)

    top_left = Point(rect.left, rect.top)
    bottom_right = Point(rect.right, rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
        return window_bbox(hwnd)
    if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        return window_bbox(hwnd)

    width = bottom_right.x - top_left.x
    height = bottom_right.y - top_left.y
    if width <= 0 or height <= 0:
        return window_bbox(hwnd)

    return top_left.x, top_left.y, bottom_right.x, bottom_right.y


def capture_window(title_hint):
    hwnd, title = find_emulator_window(title_hint)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    image = ImageGrab.grab(bbox=client_bbox(hwnd))
    return image, title


def capture_window_image(title_hint):
    hwnd, title = find_emulator_window(title_hint)
    image = ImageGrab.grab(bbox=client_bbox(hwnd))
    return image, title


def image_data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def api_url(base_url, path):
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def request_json(url, body=None, api_key=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Local LLM API error {error.code}:\n{details}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            "Could not reach the local LLM server. The launcher normally starts "
            "llama-server automatically; if you ran this directly, run "
            "`python scripts\\local_llm.py ensure` first.\n"
            f"Endpoint: {url}\n"
            f"Details: {error.reason}"
        ) from error


def choose_local_model(base_url, api_key, requested_model):
    if requested_model:
        return requested_model

    if not local_llm.server_reachable(base_url):
        local_llm.ensure_ready(base_url)

    payload = request_json(api_url(base_url, "/models"), api_key=api_key, timeout=10)
    models = payload.get("data", [])
    if not models:
        raise RuntimeError(
            "llama-server is running, but it did not report any loaded models."
        )

    model_id = models[0].get("id") or models[0].get("model") or models[0].get("name")
    if not model_id:
        raise RuntimeError(f"Could not read a model id from /v1/models: {models[0]}")

    return model_id


def extract_chat_text(payload):
    choices = payload.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()

    chunks = []
    for item in content:
        text = item.get("text") if isinstance(item, dict) else None
        if isinstance(text, str):
            chunks.append(text)
    return "\n".join(chunks).strip()


def valid_hex_color(value):
    return isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is not None


def normalize_style(value):
    if not isinstance(value, dict):
        return DEFAULT_STYLE.copy()

    background = value.get("backgroundColor")
    text = value.get("textColor")
    if not valid_hex_color(background) or not valid_hex_color(text):
        return DEFAULT_STYLE.copy()

    return {
        "backgroundColor": background.upper(),
        "textColor": text.upper(),
    }


def format_translation(raw_text):
    try:
        translation = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"text": raw_text, "style": DEFAULT_STYLE.copy()}

    translated_text = translation.get("translatedText", "")
    return {
        "text": translated_text.strip() or "(No translation returned by the model.)",
        "style": normalize_style(translation.get("style")),
    }


def translate_with_local_llm(image, target_language, model, base_url, api_key):
    selected_model = choose_local_model(base_url, api_key, model)

    prompt = (
        "This is a screenshot from a Japanese PC-98 game. OCR any visible Japanese "
        "text, translate it into {language}, and keep the answer concise. "
        "Also estimate the dominant text-box background color and text foreground "
        "color from the area containing the source text. Use #RRGGBB hex colors. "
        "If there is no readable Japanese text, say that no readable text was found. "
        "Return only JSON that matches the supplied schema."
    ).format(language=target_language)

    body = {
        "model": selected_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url(image)},
                    },
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translation",
                "strict": True,
                "schema": TRANSLATION_SCHEMA,
            },
        },
        "temperature": 0.1,
        "max_tokens": 900,
    }

    payload = request_json(
        api_url(base_url, "/chat/completions"),
        body=body,
        api_key=api_key,
        timeout=120,
    )

    text = extract_chat_text(payload)
    if not text:
        return {"text": "(No text returned by the model.)", "style": DEFAULT_STYLE.copy()}
    return format_translation(text)


def show_translation(result):
    text = result["text"] if isinstance(result, dict) else str(result)
    style = result.get("style", DEFAULT_STYLE) if isinstance(result, dict) else DEFAULT_STYLE
    background = style["backgroundColor"]
    foreground = style["textColor"]

    root = Tk()
    root.title("")
    root.geometry("780x460")
    root.configure(bg=background)
    root.attributes("-topmost", True)

    frame = Frame(root, bg=background)
    frame.pack(fill=BOTH, expand=True)

    scrollbar = Scrollbar(frame)
    scrollbar.pack(side=RIGHT, fill=Y)
    text_box = Text(
        frame,
        wrap="word",
        font=("Segoe UI", 24),
        bg=background,
        fg=foreground,
        insertbackground=foreground,
        relief="flat",
        borderwidth=0,
        padx=16,
        pady=16,
        yscrollcommand=scrollbar.set,
    )
    text_box.pack(fill=BOTH, expand=True)
    scrollbar.configure(command=text_box.yview)
    text_box.insert(END, text)
    text_box.configure(state="disabled")

    root.mainloop()


class LiveTranslationWindow:
    def __init__(self, title_hint):
        self.closed = False
        self.translate_requested = False
        self.title_hint = title_hint
        self.root = Tk()
        self.root.title("")
        self.current_style = DEFAULT_STYLE.copy()
        self.root.configure(bg=self.current_style["backgroundColor"])
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.frame = Frame(self.root, bg=self.current_style["backgroundColor"])
        self.frame.pack(fill=BOTH, expand=True)

        self.scrollbar = Scrollbar(self.frame)
        self.scrollbar.pack(side=RIGHT, fill=Y)
        self.text_box = Text(
            self.frame,
            wrap="word",
            font=("Segoe UI", 24),
            bg=self.current_style["backgroundColor"],
            fg=self.current_style["textColor"],
            insertbackground=self.current_style["textColor"],
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=16,
            yscrollcommand=self.scrollbar.set,
        )
        self.text_box.pack(fill=BOTH, expand=True)
        self.scrollbar.configure(command=self.text_box.yview)
        self.text_box.insert(END, "Right-click the emulator to translate.")
        self.text_box.configure(state="disabled")
        self.text_box.bind("<MouseWheel>", self.scroll_translation)
        self.text_box.bind("<Button-4>", self.scroll_translation)
        self.text_box.bind("<Button-5>", self.scroll_translation)
        self.position_below_emulator()
        self.pump()

    def close(self):
        self.closed = True
        try:
            self.root.destroy()
        except TclError:
            pass

    def pump(self):
        if self.closed:
            return
        try:
            self.root.update_idletasks()
            self.root.update()
        except TclError:
            self.closed = True

    def hide_for_capture(self):
        if self.closed:
            return
        try:
            self.root.withdraw()
            self.pump()
        except TclError:
            self.closed = True

    def show_after_capture(self):
        if self.closed:
            return
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.pump()
        except TclError:
            self.closed = True

    def request_translation(self, _event=None):
        self.translate_requested = True
        return "break"

    def consume_translation_request(self):
        requested = self.translate_requested
        self.translate_requested = False
        return requested

    def scroll_translation(self, event):
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = -1 * int(event.delta / 120) if event.delta else 0
        if delta:
            self.text_box.yview_scroll(delta, "units")
        return "break"

    def apply_style(self, style):
        normalized = normalize_style(style)
        self.current_style = normalized
        background = normalized["backgroundColor"]
        foreground = normalized["textColor"]
        self.root.configure(bg=background)
        self.frame.configure(bg=background)
        self.text_box.configure(
            bg=background,
            fg=foreground,
            insertbackground=foreground,
        )

    def update_text(self, result):
        if self.closed:
            return
        try:
            if isinstance(result, dict):
                text = result.get("text", "")
                style = result.get("style", self.current_style)
            else:
                text = str(result)
                style = self.current_style

            self.root.title("")
            self.apply_style(style)
            self.text_box.configure(state="normal")
            self.text_box.delete("1.0", END)
            self.text_box.insert(END, text)
            self.text_box.configure(state="disabled")
            self.pump()
        except TclError:
            self.closed = True

    def show_waiting(self):
        self.update_text({"text": "...", "style": self.current_style})

    def position_below_emulator(self):
        if self.closed:
            return
        hwnd = find_emulator_hwnd_or_none(self.title_hint)
        if not hwnd:
            self.root.geometry("780x260")
            return

        left, _top, right, bottom = client_bbox(hwnd)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = max(520, right - left)
        height = 260
        x = max(0, min(left, screen_width - width))
        y = bottom + 4

        if y + height > screen_height:
            window_left, window_top, _window_right, _window_bottom = window_bbox(hwnd)
            y = max(0, window_top - height - 4)
            x = max(0, min(window_left, screen_width - width))

        self.root.geometry(f"{width}x{height}+{x}+{y}")


def run_once(args):
    image, title = capture_window(args.title)
    print(f"Captured {title} in memory")
    print("Sending screenshot to local LLM for translation...")
    translated = translate_with_local_llm(
        image,
        args.language,
        args.model,
        args.base_url,
        args.api_key,
    )
    print("\n" + translated["text"] + "\n")

    if not args.no_popup:
        show_translation(translated)


def run_watch(args):
    print("Waiting for right-click events.")
    print("Right-click over the emulator window to translate. The click is consumed.")
    print("The watcher exits automatically when the emulator closes.")
    print("Press Ctrl+C to stop.\n")

    mouse_hook = GlobalMouseTriggerHook(args.title)
    mouse_hook.start()
    display = None if args.no_popup else LiveTranslationWindow(args.title)
    next_layout_check = 0.0
    seen_emulator = args.emulator_pid is not None
    try:
        while True:
            emulator_alive = emulator_is_running(args.title, args.emulator_pid)
            if emulator_alive:
                seen_emulator = True
            elif seen_emulator:
                print("\nEmulator closed; stopping translation watcher.")
                return

            try:
                if display:
                    display.pump()
                    if display.closed:
                        return
                    now = time.monotonic()
                    if now >= next_layout_check:
                        display.position_below_emulator()
                        next_layout_check = now + 2.0
                    should_translate = (
                        mouse_hook.consume() or display.consume_translation_request()
                    )
                else:
                    should_translate = mouse_hook.consume()

                if not should_translate:
                    time.sleep(0.05)
                    continue

                if display:
                    display.position_below_emulator()
                    display.hide_for_capture()
                image, title = capture_window_image(args.title)
                if display:
                    display.position_below_emulator()
                    display.show_after_capture()
                    display.show_waiting()

                print(f"Right-click received in {title}; translating")
                print("Sending screenshot to local LLM for translation...")
                translated = translate_with_local_llm(
                    image,
                    args.language,
                    args.model,
                    args.base_url,
                    args.api_key,
                )
                print("\n" + translated["text"] + "\n")

                if display:
                    display.update_text(translated)
                    next_layout_check = time.monotonic() + 2.0
            except Exception as error:
                print(f"\nError: {error}\n", file=sys.stderr)
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        mouse_hook.stop()
        if display:
            display.close()
        stop_managed_processes(args.managed_process_pid)


def main():
    parser = argparse.ArgumentParser(
        description="Capture the PC-98 emulator window and translate visible Japanese text."
    )
    parser.add_argument("--title", default="Neko", help="Window title hint to capture.")
    parser.add_argument(
        "--language", default="English", help="Target translation language."
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LOCAL_LLM_MODEL"),
        help="Local LLM model identifier. Defaults to the first model returned by /v1/models.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL),
        help="Local llama-server OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LOCAL_LLM_API_KEY"),
        help="Optional local LLM API token, if your server requires authentication.",
    )
    parser.add_argument("--once", action="store_true", help="Capture once and exit.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Open the live overlay and translate when you right-click the emulator.",
    )
    parser.add_argument(
        "--emulator-pid",
        type=int,
        default=None,
        help="Exit the watcher when this emulator process id is no longer running.",
    )
    parser.add_argument(
        "--managed-process-pid",
        type=int,
        action="append",
        default=[],
        help="Process id started by the launcher to stop when the watcher exits.",
    )
    parser.add_argument("--no-popup", action="store_true", help="Print only.")
    args = parser.parse_args()

    if args.watch:
        run_watch(args)
        return

    if args.once:
        run_once(args)
        return

    print("PC-98 live translation wrapper")
    print("Start the emulator, then press Enter here whenever you want a translation.")
    print("Type q and press Enter to quit.\n")

    while True:
        command = input("Translate screenshot? [Enter/q] ").strip().lower()
        if command in {"q", "quit", "exit"}:
            return
        try:
            run_once(args)
        except Exception as error:
            print(f"\nError: {error}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
