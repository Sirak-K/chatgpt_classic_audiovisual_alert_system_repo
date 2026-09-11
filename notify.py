import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
from ctypes import POINTER, byref, create_unicode_buffer, windll
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

try:
    import winsound
except Exception:
    winsound = None

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "settings.json"
DEFAULTS = {
    "title": "ChatGPT svar klart",
    "default_body": "ChatGPTs svar är klart.",
    "sound_path": r"C:\Windows\Media\Speech Sleep.wav",
    "enable_sound": True,
    "sound_sync": True,
    "enable_visual": True,
    "visual_mode": "mshta_popup",
    "popup_timeout_seconds": 10,
    "popup_script_path": "notify_popup.hta",
    "fallback_popup_script_path": "notify_popup.vbs",
    "fallback_mode": "message_beep",
    "debounce_seconds": 4.0,
    "log_path": "logs/notify_events.jsonl",
    "state_path": "state/notify_state.json",
    "max_message_chars": 140,
    "suppress_visual_when_chatgpt_focused": True,
    "suppress_sound_when_chatgpt_focused": True,
    "focus_process_names": ["ChatGPT.exe"],
    "focus_window_title_contains": ["ChatGPT"],
}

_user32 = windll.user32
_kernel32 = windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, POINTER(wintypes.DWORD)]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def resolve_path(value):
    p = Path(str(value))
    return p if p.is_absolute() else ROOT / p


def load_settings():
    data = dict(DEFAULTS)
    try:
        if SETTINGS_PATH.is_file():
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
    except Exception:
        pass
    return data


def ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def append_log(settings, **entry):
    path = resolve_path(settings["log_path"])
    ensure_parent(path)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": utc_now(), **entry}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_state(settings):
    path = resolve_path(settings["state_path"])
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_state(settings, data):
    path = resolve_path(settings["state_path"])
    ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def parse_payload(argv):
    if "--self-test" in argv[1:]:
        return {"event": "self_test", "message": "Självtest: ChatGPT-popup, ljud och logg triggas."}, True
    raw = ""
    for arg in argv[1:]:
        if arg.startswith("--"):
            continue
        raw = arg
        break
    if not raw:
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
        except Exception:
            raw = ""
    if not raw:
        return {}, False
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            return decoded, False
    except Exception:
        pass
    return {"message": raw}, False


def clip(text, limit):
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[: max(0, limit - 3)].rstrip() + "..."


def build_notification(payload, settings, self_test):
    title = str(settings.get("title") or DEFAULTS["title"])
    if self_test:
        title += " (self-test)"
    body = payload.get("message") or payload.get("summary") or payload.get("text") or settings.get("default_body")
    return title, clip(body, int(settings.get("max_message_chars", 140)))


def foreground_info():
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return {}
    title = ""
    try:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
    except Exception:
        pass
    pid = wintypes.DWORD(0)
    try:
        _user32.GetWindowThreadProcessId(hwnd, byref(pid))
    except Exception:
        pass
    name = ""
    exe = ""
    if pid.value:
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(32768)
                buf = create_unicode_buffer(size.value)
                if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
                    exe = buf.value
                    name = Path(exe).name
            except Exception:
                pass
            finally:
                _kernel32.CloseHandle(handle)
    return {"pid": int(pid.value), "process_name": name, "process_path": exe, "window_title": title}


def chatgpt_is_focused(settings, info):
    name = str(info.get("process_name") or "").lower()
    title = str(info.get("window_title") or "").lower()
    allowed_names = {str(x).lower() for x in settings.get("focus_process_names", [])}
    title_needles = [str(x).lower() for x in settings.get("focus_window_title_contains", [])]
    return (not allowed_names or name in allowed_names) and (not title_needles or any(x in title for x in title_needles))


def signature(title, body, payload):
    src = json.dumps({"title": title, "body": body, "event": payload.get("event", "")}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def debounced(settings, sig, self_test):
    if self_test:
        return False
    window = float(settings.get("debounce_seconds", 0) or 0)
    state = load_state(settings)
    if window <= 0:
        return False
    return state.get("last_signature") == sig and time.time() - float(state.get("last_sent_at", 0) or 0) < window


def show_mshta(settings, title, body):
    script = resolve_path(settings.get("popup_script_path", "notify_popup.hta"))
    host = Path(r"C:\Windows\System32\mshta.exe")
    if not script.is_file() or not host.is_file():
        return "mshta_unavailable"
    timeout = max(1, int(settings.get("popup_timeout_seconds", 10)))
    query = urllib.parse.urlencode({"title": title, "body": body, "timeout": str(timeout)})
    target = f"{script.as_uri()}?{query}"
    flags = 0
    for flag in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, flag, 0)
    try:
        subprocess.Popen([str(host), target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        return "mshta_popup_spawned"
    except Exception as exc:
        return f"mshta_failed:{type(exc).__name__}:{exc}"


def show_vbs(settings, title, body):
    script = resolve_path(settings.get("fallback_popup_script_path", "notify_popup.vbs"))
    host = Path(r"C:\Windows\System32\wscript.exe")
    if not script.is_file() or not host.is_file():
        return "wscript_unavailable"
    timeout = max(1, int(settings.get("popup_timeout_seconds", 10)))
    flags = 0
    for flag in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, flag, 0)
    try:
        subprocess.Popen([str(host), "//nologo", str(script), title, body, str(timeout)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        return "wscript_popup_spawned"
    except Exception as exc:
        return f"wscript_failed:{type(exc).__name__}:{exc}"


def show_visual(settings, title, body):
    if not settings.get("enable_visual", True):
        return "visual_disabled"
    mode = str(settings.get("visual_mode") or "mshta_popup").lower()
    if mode == "none":
        return "visual_disabled"
    if mode == "wscript_popup":
        return show_vbs(settings, title, body)
    first = show_mshta(settings, title, body)
    if first == "mshta_popup_spawned":
        return first
    return first + "|" + show_vbs(settings, title, body)


def play_sound(settings):
    if not settings.get("enable_sound", True):
        return "sound_disabled"
    if winsound is None:
        return "winsound_unavailable"
    sound = resolve_path(settings.get("sound_path", DEFAULTS["sound_path"]))
    try:
        if sound.is_file():
            flags = winsound.SND_FILENAME | (0 if settings.get("sound_sync", True) else winsound.SND_ASYNC)
            winsound.PlaySound(str(sound), flags)
            return "wav_played"
    except Exception as exc:
        primary = f"wav_failed:{type(exc).__name__}:{exc}"
    else:
        primary = "sound_file_missing"
    fallback = str(settings.get("fallback_mode") or "message_beep").lower()
    if fallback == "none":
        return primary
    try:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return primary + "|message_beep_played"
    except Exception as exc:
        return primary + f"|message_beep_failed:{type(exc).__name__}:{exc}"


def main(argv=None):
    argv = argv or sys.argv
    settings = load_settings()
    payload, self_test = parse_payload(argv)
    title, body = build_notification(payload, settings, self_test)
    sig = signature(title, body, payload)
    if debounced(settings, sig, self_test):
        append_log(settings, result="suppressed", reason="debounced", payload=payload, title=title, body=body)
        return 0

    focus = {} if self_test else foreground_info()
    focused = False if self_test else chatgpt_is_focused(settings, focus)
    visual_result = "suppressed_focused_chatgpt" if focused and settings.get("suppress_visual_when_chatgpt_focused", True) else show_visual(settings, title, body)
    sound_result = "suppressed_focused_chatgpt" if focused and settings.get("suppress_sound_when_chatgpt_focused", True) else play_sound(settings)

    if not self_test:
        save_state(settings, {"last_sent_at": time.time(), "last_signature": sig})
    append_log(settings, result="notified", visual_result=visual_result, sound_result=sound_result, foreground=focus, payload=payload, title=title, body=body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
