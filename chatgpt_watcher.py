import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
    from pywinauto import Desktop
except Exception as exc:
    psutil = None
    Desktop = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "settings.json"
DEFAULT_SETTINGS = {
    "watcher_poll_seconds": 0.45,
    "completion_settle_seconds": 0.9,
    "minimum_generation_seconds": 0.4,
    "window_title_contains": ["ChatGPT"],
    "process_names": ["ChatGPT.exe"],
    "classic_path_contains": ["OpenAI.ChatGPT-Desktop"],
    "generation_button_patterns": [
        r"^stop generating$",
        r"^stop streaming$",
        r"^stop response$",
        r"^stoppa generering$",
        r"^stoppa svar$",
        r"^avbryt generering$"
    ],
    "notify_script": "notify.py",
    "watcher_log_path": "logs/chatgpt_watcher.jsonl",
    "watcher_state_path": "state/chatgpt_watcher_state.json",
    "watcher_pid_path": "state/chatgpt_watcher.pid",
    "max_diagnostic_controls": 220,
    "notify_message": "ChatGPTs svar är klart."
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def resolve_path(value):
    p = Path(str(value))
    return p if p.is_absolute() else ROOT / p


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.is_file():
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)
    return data


def ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def append_log(settings, **entry):
    path = resolve_path(settings["watcher_log_path"])
    ensure_parent(path)
    payload = {"timestamp": utc_now(), **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_pid(settings):
    path = resolve_path(settings["watcher_pid_path"])
    ensure_parent(path)
    path.write_text(str(os.getpid()), encoding="ascii")


def clear_pid(settings):
    path = resolve_path(settings["watcher_pid_path"])
    try:
        if path.is_file() and path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()
    except Exception:
        pass


def stop_existing(settings):
    if psutil is None:
        print(f"ERROR: dependencies missing: {IMPORT_ERROR}")
        return 2
    path = resolve_path(settings["watcher_pid_path"])
    if not path.is_file():
        print("No watcher PID file found.")
        return 0
    try:
        pid = int(path.read_text(encoding="ascii").strip())
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline()).lower()
        if "chatgpt_watcher.py" not in cmd:
            print(f"Refusing to stop PID {pid}: it does not look like this watcher.")
            return 3
        proc.terminate()
        try:
            proc.wait(4)
        except psutil.TimeoutExpired:
            proc.kill()
        print(f"Stopped watcher PID {pid}.")
    except psutil.NoSuchProcess:
        print("Watcher was already stopped.")
    except Exception as exc:
        print(f"ERROR stopping watcher: {exc}")
        return 4
    try:
        path.unlink()
    except Exception:
        pass
    return 0


def process_info(pid):
    if psutil is None:
        return {"pid": int(pid)}
    try:
        p = psutil.Process(int(pid))
        return {"pid": p.pid, "name": p.name(), "exe": p.exe()}
    except Exception:
        return {"pid": int(pid), "name": "", "exe": ""}


def path_looks_classic(exe, settings):
    needles = [str(x).lower() for x in settings.get("classic_path_contains", []) if str(x).strip()]
    if not needles:
        return True
    text = str(exe or "").lower()
    if not text:
        return None
    return any(n in text for n in needles)


def candidate_windows(settings):
    if Desktop is None:
        return []
    names = {str(x).lower() for x in settings.get("process_names", ["ChatGPT.exe"])}
    title_needles = [str(x).lower() for x in settings.get("window_title_contains", ["ChatGPT"]) if str(x).strip()]
    candidates = []
    try:
        windows = Desktop(backend="uia").windows(visible_only=True)
    except Exception:
        return []
    for win in windows:
        try:
            info = win.element_info
            pid = int(info.process_id)
            title = str(info.name or "")
            pinfo = process_info(pid)
            pname = str(pinfo.get("name") or "").lower()
            if names and pname not in names:
                continue
            title_ok = not title_needles or any(x in title.lower() for x in title_needles)
            if not title_ok:
                continue
            classic = path_looks_classic(pinfo.get("exe"), settings)
            # If Windows protects the package path, fall back to title + process name.
            if classic is False:
                continue
            candidates.append((win, {**pinfo, "title": title, "classic_path_match": classic}))
        except Exception:
            continue
    return candidates


def compile_patterns(settings):
    return [re.compile(str(p), re.IGNORECASE) for p in settings.get("generation_button_patterns", [])]


def get_button_snapshot(win, patterns):
    names = []
    active_hits = []
    try:
        controls = win.descendants(control_type="Button")
    except Exception:
        controls = []
    for ctrl in controls:
        try:
            name = str(ctrl.element_info.name or "").strip()
        except Exception:
            continue
        if not name:
            continue
        names.append(name)
        if any(p.search(name) for p in patterns):
            active_hits.append(name)
    return names, active_hits


def window_fingerprint(meta):
    return f"{meta.get('pid','')}|{meta.get('title','')}"


def send_notification(settings, meta, elapsed):
    script = resolve_path(settings.get("notify_script", "notify.py"))
    payload = {
        "event": "chatgpt_response_completed",
        "message": str(settings.get("notify_message") or "ChatGPTs svar är klart."),
        "source": "chatgpt_classic_watcher",
        "window_title": meta.get("title", ""),
        "process_id": meta.get("pid", 0),
        "generation_seconds": round(float(elapsed), 3),
    }
    if not script.is_file():
        raise FileNotFoundError(f"Notifier script missing: {script}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, str(script), json.dumps(payload, ensure_ascii=False)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def diagnose(settings):
    if IMPORT_ERROR is not None:
        print("DEPENDENCIES: MISSING")
        print(f"  {type(IMPORT_ERROR).__name__}: {IMPORT_ERROR}")
        print("Run install.cmd first.")
        return 2
    print("ChatGPT Classic watcher diagnostics")
    print(f"Python: {sys.executable}")
    print(f"Project: {ROOT}")
    wins = candidate_windows(settings)
    print(f"Candidate ChatGPT Classic windows: {len(wins)}")
    patterns = compile_patterns(settings)
    limit = int(settings.get("max_diagnostic_controls", 220))
    for idx, (win, meta) in enumerate(wins, 1):
        print(f"\n[{idx}] PID={meta.get('pid')} process={meta.get('name')} title={meta.get('title')!r}")
        print(f"    exe={meta.get('exe')}")
        print(f"    classic_path_match={meta.get('classic_path_match')}")
        buttons, hits = get_button_snapshot(win, patterns)
        print(f"    buttons={len(buttons)} generation_hits={hits}")
        for name in buttons[:limit]:
            print(f"      BUTTON: {name}")
    if not wins:
        print("\nRESULT: ChatGPT Classic window not found. Open ChatGPT Classic and rerun diagnostics.")
        return 1
    print("\nRESULT: watcher can see ChatGPT Classic through Windows UI Automation.")
    return 0


def self_test(settings):
    meta = {"title": "ChatGPT Classic self-test", "pid": os.getpid()}
    send_notification(settings, meta, 0.0)
    print("Self-test notification dispatched.")
    return 0


def watch(settings):
    if IMPORT_ERROR is not None:
        print(f"Dependencies missing: {IMPORT_ERROR}")
        print("Run install.cmd first.")
        return 2

    pid_path = resolve_path(settings["watcher_pid_path"])
    if pid_path.is_file():
        try:
            old_pid = int(pid_path.read_text(encoding="ascii").strip())
            if psutil.pid_exists(old_pid) and old_pid != os.getpid():
                print(f"Watcher already appears to be running as PID {old_pid}.")
                return 3
        except Exception:
            pass

    write_pid(settings)
    patterns = compile_patterns(settings)
    poll = max(0.15, float(settings.get("watcher_poll_seconds", 0.45)))
    settle = max(0.2, float(settings.get("completion_settle_seconds", 0.9)))
    minimum = max(0.0, float(settings.get("minimum_generation_seconds", 0.4)))

    active = False
    active_since = 0.0
    active_window = ""
    candidate_complete_at = None
    last_meta = {}
    last_hit = []
    last_presence_log = None

    append_log(settings, event="watcher_started", pid=os.getpid(), poll_seconds=poll)
    try:
        while True:
            wins = candidate_windows(settings)
            if not wins:
                if last_presence_log is not False:
                    append_log(settings, event="chatgpt_window_missing")
                    last_presence_log = False
                if active:
                    append_log(settings, event="generation_aborted_window_lost", generation_seconds=round(time.monotonic()-active_since, 3))
                active = False
                candidate_complete_at = None
                time.sleep(poll)
                continue

            last_presence_log = True
            # Prefer a window that currently exposes a generation button; otherwise first candidate.
            selected = None
            for win, meta in wins:
                buttons, hits = get_button_snapshot(win, patterns)
                if hits:
                    selected = (win, meta, buttons, hits)
                    break
                if selected is None:
                    selected = (win, meta, buttons, hits)

            win, meta, _buttons, hits = selected
            now = time.monotonic()
            current_window = window_fingerprint(meta)
            generating = bool(hits)
            last_meta = meta
            last_hit = hits

            if generating:
                candidate_complete_at = None
                if not active or active_window != current_window:
                    active = True
                    active_since = now
                    active_window = current_window
                    append_log(settings, event="generation_started", **meta, generation_controls=hits)
            elif active:
                if candidate_complete_at is None:
                    candidate_complete_at = now
                    append_log(settings, event="completion_candidate", **meta, generation_seconds=round(now-active_since, 3))
                elif now - candidate_complete_at >= settle:
                    elapsed = now - active_since
                    if elapsed >= minimum:
                        try:
                            send_notification(settings, meta, elapsed)
                            append_log(settings, event="response_completed_notified", **meta, generation_seconds=round(elapsed, 3))
                        except Exception as exc:
                            append_log(settings, event="notification_failed", error=f"{type(exc).__name__}: {exc}", **meta)
                    else:
                        append_log(settings, event="completion_ignored_too_fast", **meta, generation_seconds=round(elapsed, 3))
                    active = False
                    active_window = ""
                    candidate_complete_at = None

            time.sleep(poll)
    except KeyboardInterrupt:
        append_log(settings, event="watcher_stopped", reason="keyboard_interrupt")
        return 0
    except Exception as exc:
        append_log(settings, event="watcher_crashed", error=f"{type(exc).__name__}: {exc}", last_meta=last_meta, last_generation_hits=last_hit)
        raise
    finally:
        clear_pid(settings)


def main():
    parser = argparse.ArgumentParser(description="ChatGPT Classic response completion watcher")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--watch", action="store_true")
    group.add_argument("--diagnose", action="store_true")
    group.add_argument("--self-test", action="store_true")
    group.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if args.stop:
        return stop_existing(settings)
    if args.diagnose:
        return diagnose(settings)
    if args.self_test:
        return self_test(settings)
    return watch(settings)


if __name__ == "__main__":
    raise SystemExit(main())
