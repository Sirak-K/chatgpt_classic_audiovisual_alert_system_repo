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
    "watcher_poll_seconds": 0.12,
    "completion_settle_seconds": 0.35,
    "minimum_generation_seconds": 0.0,
    "window_title_contains": ["ChatGPT Classic"],
    "process_names": ["ChatGPT Classic.exe"],
    "classic_path_contains": ["OpenAI.ChatGPT-Desktop"],
    "watch_hidden_windows": True,
    "generation_button_patterns": [
        r"^stop.*$",
        r"^stoppa.*$",
        r"^avbryt.*$",
    ],
    "completion_marker_patterns": [
        r"^kopiera svar$",
        r"^copy response$",
        r"^copy answer$",
    ],
    "user_message_marker_patterns": [
        r"^redigera meddelande$",
        r"^edit message$",
    ],
    "notify_script": "notify.py",
    "watcher_log_path": "logs/chatgpt_watcher.jsonl",
    "watcher_state_path": "state/chatgpt_watcher_state.json",
    "watcher_pid_path": "state/chatgpt_watcher.pid",
    "max_diagnostic_controls": 220,
    "notify_message": "ChatGPTs svar är klart.",
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


def console_event(event, **fields):
    try:
        suffix = " ".join(f"{k}={v}" for k, v in fields.items() if v not in (None, "", []))
        print(f"[{event}]" + (f" {suffix}" if suffix else ""), flush=True)
    except Exception:
        pass


def log_event(settings, event, **fields):
    append_log(settings, event=event, **fields)
    if event in {
        "watcher_started",
        "chatgpt_window_found",
        "chatgpt_window_missing",
        "user_message_observed",
        "generation_started",
        "completion_marker_observed",
        "completion_candidate",
        "response_completed_notified",
        "notification_failed",
    }:
        console_event(event, **fields)


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
    names = {str(x).lower() for x in settings.get("process_names", []) if str(x).strip()}
    title_needles = [str(x).lower() for x in settings.get("window_title_contains", []) if str(x).strip()]
    candidates = []
    visible_only = not bool(settings.get("watch_hidden_windows", True))
    try:
        windows = Desktop(backend="uia").windows(visible_only=visible_only)
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
            if classic is False:
                continue
            candidates.append((win, {**pinfo, "title": title, "classic_path_match": classic}))
        except Exception:
            continue
    return candidates


def compile_patterns(values):
    return [re.compile(str(p), re.IGNORECASE) for p in values or []]


def get_button_names(win):
    names = []
    try:
        controls = win.descendants(control_type="Button")
    except Exception:
        controls = []
    for ctrl in controls:
        try:
            name = str(ctrl.element_info.name or "").strip()
        except Exception:
            continue
        if name:
            names.append(name)
    return names


def matching_names(names, patterns):
    return [name for name in names if any(p.search(name) for p in patterns)]


def snapshot_window(win, generation_patterns, completion_patterns, user_patterns):
    buttons = get_button_names(win)
    generation_hits = matching_names(buttons, generation_patterns)
    completion_hits = matching_names(buttons, completion_patterns)
    user_hits = matching_names(buttons, user_patterns)
    return {
        "buttons": buttons,
        "generation_hits": generation_hits,
        "completion_hits": completion_hits,
        "user_hits": user_hits,
        "completion_count": len(completion_hits),
        "user_count": len(user_hits),
    }


def choose_window(wins, generation_patterns, completion_patterns, user_patterns):
    best = None
    for win, meta in wins:
        snap = snapshot_window(win, generation_patterns, completion_patterns, user_patterns)
        score = (
            100000 if snap["generation_hits"] else 0,
            snap["completion_count"] + snap["user_count"],
            len(snap["buttons"]),
        )
        if best is None or score > best[0]:
            best = (score, win, meta, snap)
    if best is None:
        return None
    return best[1], best[2], best[3]


def send_notification(settings, meta, elapsed=0.0, detection="unknown"):
    script = resolve_path(settings.get("notify_script", "notify.py"))
    payload = {
        "event": "chatgpt_response_completed",
        "message": str(settings.get("notify_message") or "ChatGPTs svar är klart."),
        "source": "chatgpt_classic_watcher",
        "window_title": meta.get("title", ""),
        "process_id": meta.get("pid", 0),
        "generation_seconds": round(float(elapsed), 3),
        "detection": detection,
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
    generation_patterns = compile_patterns(settings.get("generation_button_patterns"))
    completion_patterns = compile_patterns(settings.get("completion_marker_patterns"))
    user_patterns = compile_patterns(settings.get("user_message_marker_patterns"))
    limit = int(settings.get("max_diagnostic_controls", 220))
    for idx, (win, meta) in enumerate(wins, 1):
        snap = snapshot_window(win, generation_patterns, completion_patterns, user_patterns)
        print(f"\n[{idx}] PID={meta.get('pid')} process={meta.get('name')} title={meta.get('title')!r}")
        print(f"    exe={meta.get('exe')}")
        print(f"    classic_path_match={meta.get('classic_path_match')}")
        print(
            "    buttons={} generation_hits={} user_messages={} completed_responses={}".format(
                len(snap["buttons"]),
                snap["generation_hits"],
                snap["user_count"],
                snap["completion_count"],
            )
        )
        for name in snap["buttons"][:limit]:
            print(f"      BUTTON: {name}")
    if not wins:
        print("\nRESULT: ChatGPT Classic window not found. Open ChatGPT Classic and rerun diagnostics.")
        return 1
    print("\nRESULT: watcher can see ChatGPT Classic through Windows UI Automation.")
    return 0


def self_test(settings):
    meta = {"title": "ChatGPT Classic self-test", "pid": os.getpid()}
    send_notification(settings, meta, 0.0, detection="self_test")
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
    generation_patterns = compile_patterns(settings.get("generation_button_patterns"))
    completion_patterns = compile_patterns(settings.get("completion_marker_patterns"))
    user_patterns = compile_patterns(settings.get("user_message_marker_patterns"))
    poll = max(0.05, float(settings.get("watcher_poll_seconds", 0.12)))
    settle = max(0.1, float(settings.get("completion_settle_seconds", 0.35)))

    previous_user_count = None
    previous_completion_count = None
    pending_user_messages = 0
    generation_active = False
    generation_started_at = 0.0
    completion_candidate_at = None
    marker_notified_for_generation = False
    last_presence = None
    last_pid = None
    last_meta = {}

    log_event(settings, "watcher_started", pid=os.getpid(), poll_seconds=poll)
    try:
        while True:
            wins = candidate_windows(settings)
            if not wins:
                if last_presence is not False:
                    log_event(settings, "chatgpt_window_missing")
                last_presence = False
                time.sleep(poll)
                continue

            selected = choose_window(wins, generation_patterns, completion_patterns, user_patterns)
            if selected is None:
                time.sleep(poll)
                continue
            win, meta, snap = selected
            last_meta = meta

            if last_presence is not True or last_pid != meta.get("pid"):
                log_event(
                    settings,
                    "chatgpt_window_found",
                    pid=meta.get("pid"),
                    process=meta.get("name"),
                    title=meta.get("title"),
                )
            last_presence = True
            last_pid = meta.get("pid")

            user_count = snap["user_count"]
            completion_count = snap["completion_count"]
            generating = bool(snap["generation_hits"])
            now = time.monotonic()

            # First observation is a baseline only; old messages never alert.
            if previous_user_count is None or previous_completion_count is None:
                previous_user_count = user_count
                previous_completion_count = completion_count
                generation_active = generating
                if generating:
                    generation_started_at = now
                    log_event(settings, "generation_started", generation_controls=snap["generation_hits"])
                time.sleep(poll)
                continue

            user_delta = user_count - previous_user_count
            completion_delta = completion_count - previous_completion_count

            # A newly submitted user message creates one pending response. This is
            # what lets very short answers be detected even if the stop button is
            # never observed between two polls.
            if user_delta > 0:
                pending_user_messages += user_delta
                log_event(
                    settings,
                    "user_message_observed",
                    delta=user_delta,
                    pending=pending_user_messages,
                    user_count=user_count,
                )
            elif user_delta < 0:
                # Conversation/navigation changed. Rebaseline; never alert merely
                # because historical messages appeared/disappeared.
                pending_user_messages = 0

            if generating:
                completion_candidate_at = None
                if not generation_active:
                    generation_active = True
                    generation_started_at = now
                    marker_notified_for_generation = False
                    # If UIA saw generation but missed the user-marker increment,
                    # arm one pending completion anyway.
                    if pending_user_messages <= 0:
                        pending_user_messages = 1
                    log_event(settings, "generation_started", generation_controls=snap["generation_hits"])
            elif generation_active and completion_candidate_at is None:
                completion_candidate_at = now
                log_event(
                    settings,
                    "completion_candidate",
                    generation_seconds=round(now - generation_started_at, 3),
                )

            # PRIMARY completion signal: a new finalized assistant action cluster.
            # "Kopiera svar" / "Copy response" exists once per completed assistant
            # message, so it is independent of response duration.
            if completion_delta > 0:
                log_event(
                    settings,
                    "completion_marker_observed",
                    delta=completion_delta,
                    pending=pending_user_messages,
                    completed_responses=completion_count,
                )
                notifications = min(completion_delta, pending_user_messages)
                for _ in range(max(0, notifications)):
                    try:
                        elapsed = now - generation_started_at if generation_active else 0.0
                        send_notification(settings, meta, elapsed, detection="completion_marker")
                        log_event(
                            settings,
                            "response_completed_notified",
                            detection="completion_marker",
                            generation_seconds=round(max(0.0, elapsed), 3),
                        )
                    except Exception as exc:
                        log_event(settings, "notification_failed", error=f"{type(exc).__name__}: {exc}")
                if notifications > 0:
                    pending_user_messages = max(0, pending_user_messages - notifications)
                    marker_notified_for_generation = True
                    generation_active = False
                    completion_candidate_at = None

            # FALLBACK: if a stop/generating control was observed but the finalized
            # response marker is unavailable in a future app build, use the classic
            # generating -> stable idle transition.
            if (
                not generating
                and generation_active
                and completion_candidate_at is not None
                and now - completion_candidate_at >= settle
            ):
                elapsed = now - generation_started_at
                if not marker_notified_for_generation:
                    try:
                        send_notification(settings, meta, elapsed, detection="generation_transition")
                        log_event(
                            settings,
                            "response_completed_notified",
                            detection="generation_transition",
                            generation_seconds=round(elapsed, 3),
                        )
                    except Exception as exc:
                        log_event(settings, "notification_failed", error=f"{type(exc).__name__}: {exc}")
                    pending_user_messages = max(0, pending_user_messages - 1)
                generation_active = False
                completion_candidate_at = None
                marker_notified_for_generation = False

            previous_user_count = user_count
            previous_completion_count = completion_count
            time.sleep(poll)
    except KeyboardInterrupt:
        append_log(settings, event="watcher_stopped", reason="keyboard_interrupt")
        return 0
    except Exception as exc:
        append_log(settings, event="watcher_crashed", error=f"{type(exc).__name__}: {exc}", last_meta=last_meta)
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
