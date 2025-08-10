# agent_service.py — Local Autonomy Agent (Windows-friendly)
# - Liest Tasks aus agent_inbox/*.json
# - Prüft Policies (Whitelist), erzeugt Diffs/Vorschläge
# - Wartet auf deine Freigabe (approve), wendet Änderungen an
# - Kann Bot/Bridge/Dashboard neu starten
# - Schreibt Status nach agent_outbox/

import json
import time
import subprocess
import difflib
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

ROOT = Path.cwd()
INBOX = ROOT / "agent_inbox"
OUTBOX = ROOT / "agent_outbox"
POLICY_DIR = ROOT / "policies"
CONTROL = ROOT / "bridge_out" / "control"
for p in [INBOX, OUTBOX, POLICY_DIR, CONTROL]:
    p.mkdir(parents=True, exist_ok=True)

# ---- Policies ----
DEFAULT_POLICY = {
    "allowed_paths": [
        "bot.py",
        "dashboard.py",
        "risk_guard.py",
        "coop_bridge.py",
        "strategies/",
        "configs/",
        ".vscode/",
    ],
    "allowed_cmds": ["git", "python"],
    "require_approval": True,
}
POLICY_FILE = POLICY_DIR / "whitelist.json"
if not POLICY_FILE.exists():
    POLICY_FILE.write_text(json.dumps(DEFAULT_POLICY, indent=2), encoding="utf-8")


def load_policy() -> Dict[str, Any]:
    try:
        return json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_POLICY


# ---- Helpers ----
def safe_path(rel: str) -> Path:
    p = (ROOT / rel).resolve()
    if ROOT not in p.parents and p != ROOT:
        raise RuntimeError("Unsafe path outside workspace")
    return p


def is_allowed_path(rel: str, policy) -> bool:
    rel = rel.replace("\\", "/")
    for pat in policy.get("allowed_paths", []):
        if rel == pat or rel.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def write_out(name: str, payload: Dict[str, Any]) -> Path:
    fn = OUTBOX / f"{int(time.time() * 1000)}_{name}.json"
    fn.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fn


def make_diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=path, tofile=path
        )
    )


def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, timeout=300
        )
        return {"cmd": cmd, "code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except Exception as e:
        return {"cmd": cmd, "error": str(e)}


# ---- Actions ----
def action_write_file(task, policy):
    rel = task["path"]
    if not is_allowed_path(rel, policy):
        return write_out("reject", {"task": task, "reason": "path not allowed"})
    p = safe_path(rel)
    before = p.read_text(encoding="utf-8") if p.exists() else ""
    after = task["content"]
    diff = make_diff(before, after, rel)
    proposal = {"type": "write_file", "path": rel, "diff": diff}
    out = write_out("proposal", proposal)
    if not policy.get("require_approval", True):
        approve = True
    else:
        approve = wait_for_approval(out)
    if approve:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(after, encoding="utf-8")
        return write_out("applied", {"task": task, "path": rel})
    return write_out("pending", {"task": task, "path": rel})


def action_patch_file(task, policy):
    rel = task["path"]
    _ = task["diff"]
    if not is_allowed_path(rel, policy):
        return write_out("reject", {"task": task, "reason": "path not allowed"})
    p = safe_path(rel)
    before = p.read_text(encoding="utf-8") if p.exists() else ""
    # naive display, apply by replacement with provided 'after' if given
    after = task.get("after")
    if after is None:
        return write_out("reject", {"task": task, "reason": "after required for safe patch"})
    diff = make_diff(before, after, rel)
    proposal = {"type": "patch_file", "path": rel, "diff": diff}
    out = write_out("proposal", proposal)
    approve = wait_for_approval(out) if policy.get("require_approval", True) else True
    if approve:
        p.write_text(after, encoding="utf-8")
        return write_out("applied", {"task": task, "path": rel})
    return write_out("pending", {"task": task, "path": rel})


def action_run_command(task, policy):
    cmd = task["cmd"]
    base = cmd[0].split()[0]
    if base not in policy.get("allowed_cmds", []):
        return write_out("reject", {"task": task, "reason": "cmd not allowed"})
    res = run_cmd(cmd)
    return write_out("command_result", res)


def action_restart_bot(_task, _policy):
    # simple kill & relaunch through a bootstrapper script
    # You can adapt to your VSCode launch or a Windows service later.
    return write_out(
        "info",
        {"msg": "Restart request received – use VS Code Run Compound to manage both processes."},
    )


def wait_for_approval(proposal_path: Path, timeout_sec: int = 600) -> bool:
    """
    Warte auf agent_inbox/approve.json mit {"proposal":"<filename>", "approve":true}
    """
    deadline = time.time() + timeout_sec
    target = proposal_path.name
    while time.time() < deadline:
        for ap in INBOX.glob("approve*.json"):
            try:
                d = json.loads(ap.read_text(encoding="utf-8"))
                if d.get("proposal") == target:
                    ok = bool(d.get("approve", False))
                    ap.unlink(missing_ok=True)
                    return ok
            except Exception:
                pass
        time.sleep(2)
    return False


ACTIONS = {
    "write_file": action_write_file,
    "patch_file": action_patch_file,
    "run_command": action_run_command,
    "restart_bot": action_restart_bot,
}


# ---- Watcher ----
class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith(".json"):
            return
        time.sleep(0.2)
        self.process(Path(event.src_path))

    def process(self, path: Path):
        try:
            task = json.loads(Path(path).read_text(encoding="utf-8"))
            kind = task.get("type")
            policy = load_policy()
            if kind in ACTIONS:
                ACTIONS[kind](task, policy)
            else:
                write_out("reject", {"task": task, "reason": "unknown type"})
        except Exception as e:
            write_out(
                "error", {"file": path.name, "error": str(e), "trace": traceback.format_exc()}
            )


def main():
    print("[agent] running… watching", INBOX)
    obs = Observer()
    h = InboxHandler()
    obs.schedule(h, str(INBOX), recursive=False)
    obs.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join()


if __name__ == "__main__":
    main()
