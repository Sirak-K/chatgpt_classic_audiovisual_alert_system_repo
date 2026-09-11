import os
import sys

try:
    import psutil
    from pywinauto import Desktop
except Exception as exc:
    print(f"ERROR importing diagnostics dependencies: {type(exc).__name__}: {exc}")
    raise SystemExit(2)

KEYWORDS = ("chatgpt", "openai", "codex")


def safe_process_info(pid):
    info = {"pid": int(pid), "name": "", "exe": "", "cmdline": ""}
    try:
        p = psutil.Process(int(pid))
    except Exception:
        return info
    try:
        info["name"] = p.name() or ""
    except Exception:
        pass
    try:
        info["exe"] = p.exe() or ""
    except Exception:
        pass
    try:
        info["cmdline"] = " ".join(p.cmdline())
    except Exception:
        pass
    return info


def interesting(*values):
    text = " ".join(str(v or "") for v in values).lower()
    return any(k in text for k in KEYWORDS)


def dump_processes():
    print("\n=== CHATGPT / OPENAI / CODEX PROCESSES ===")
    rows = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(p.info["pid"])
            name = str(p.info.get("name") or "")
        except Exception:
            continue
        info = safe_process_info(pid)
        if interesting(name, info.get("exe"), info.get("cmdline")):
            rows.append(info)
    if not rows:
        print("(none found by process metadata)")
        return
    for info in rows:
        print(f"PID={info['pid']} name={info['name']!r}")
        print(f"  exe={info['exe']!r}")
        if info["cmdline"]:
            print(f"  cmdline={info['cmdline']!r}")


def dump_windows(backend):
    print(f"\n=== TOP-LEVEL WINDOWS ({backend}) ===")
    try:
        wins = Desktop(backend=backend).windows(visible_only=True)
    except Exception as exc:
        print(f"ERROR enumerating {backend}: {type(exc).__name__}: {exc}")
        return

    interesting_rows = []
    other_rows = []
    for win in wins:
        try:
            info = win.element_info
            title = str(getattr(info, "name", "") or "")
            pid = int(getattr(info, "process_id", 0) or 0)
        except Exception:
            continue
        pinfo = safe_process_info(pid) if pid else {"pid": pid, "name": "", "exe": "", "cmdline": ""}
        row = (pid, title, pinfo.get("name", ""), pinfo.get("exe", ""))
        if interesting(title, pinfo.get("name"), pinfo.get("exe"), pinfo.get("cmdline")):
            interesting_rows.append(row)
        else:
            other_rows.append(row)

    rows = interesting_rows if interesting_rows else other_rows[:60]
    if not rows:
        print("(no visible top-level windows)")
        return

    if not interesting_rows:
        print("No keyword-matching windows; showing first 60 visible windows instead.")
    for pid, title, name, exe in rows:
        print(f"PID={pid} process={name!r} title={title!r}")
        print(f"  exe={exe!r}")


def main():
    print("ChatGPT Classic raw Windows probe")
    print(f"Python: {sys.executable}")
    print(f"PID: {os.getpid()}")
    dump_processes()
    dump_windows("uia")
    dump_windows("win32")
    print("\nCopy the complete output back into ChatGPT if the watcher still reports 0 candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
