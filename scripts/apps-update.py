#!/usr/bin/env python3
"""Refresh pinned app sources for the shared system updater. Never switch or commit."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile

APPS = ("codex", "t3", "cursor", "granola", "grok", "opencode1")
PINS = {
    "codex": "pkgs/codex/sources.json",
    "t3": "pkgs/t3code/sources.json",
    "cursor": "pkgs/code-cursor/sources.json",
    "granola": "pkgs/granola/default.nix",
    "grok": "pkgs/grok-cli/default.nix",
    "opencode1": "pkgs/opencode1/sources.json",
}


def run(argv, repo=None, timeout=600):
    result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-6000:])
    return result.stdout


def fetch(url, headers=False):
    return run(["curl", "--fail", "--silent", "--show-error", "--max-time", "60",
                *(["--head"] if headers else ["--location"]), url])


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError(f"Unexpected stable version: {value!r}")
    return tuple(map(int, value.split(".")))


def newer(current, latest):
    if version(latest) < version(current):
        raise ValueError(f"Refusing downgrade {current} -> {latest}")
    return latest != current


def prefetch(url):
    value = json.loads(run(["nix", "store", "prefetch-file", "--json", url]))["hash"]
    if not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", value):
        raise ValueError("Unexpected download hash")
    return value


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def atomic_write(path, data, expected):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".apps-update-", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != expected:
            raise ValueError(f"Preserved concurrent edit to {path}; inspect before retrying")
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def helper(repo, name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), repo / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate(repo, app, original):
    if app == "codex":
        codex = helper(repo, "codex-update")
        current = json.loads(original)
        release = json.loads(fetch("https://api.github.com/repos/openai/codex/releases/latest"))
        updated = codex.release_sources(current, release, download=True)
        print(f"Codex CLI {current['version']} -> {updated['version']}", flush=True)
        return encoded(updated)
    if app == "cursor":
        current = json.loads(original)
        latest = json.loads(fetch("https://www.cursor.com/api/download?platform=linux-x64&releaseTrack=stable"))
        if not newer(current["version"], latest["version"]):
            return original
        url = latest["downloadUrl"]
        if not re.fullmatch(r"https://downloads\.cursor\.com/production/[0-9a-f]+/linux/x64/Cursor-" + re.escape(latest["version"]) + r"-x86_64\.AppImage", url):
            raise ValueError("Unexpected Cursor download URL")
        print(f"Cursor {current['version']} -> {latest['version']}", flush=True)
        current["version"] = latest["version"]
        current["sources"]["x86_64-linux"] = {"url": url, "hash": prefetch(url)}
        return encoded(current)
    text = original.decode()
    match = re.search(r'^  version = "([0-9.]+)";', text, re.M)
    if not match:
        raise ValueError(f"Missing {app} version")
    current = match[1]
    if app == "granola":
        headers = fetch("https://api.granola.ai/v1/download-latest", headers=True)
        locations = re.findall(r"^location:\s*(\S+)", headers, re.I | re.M)
        if len(locations) != 1:
            raise ValueError("Expected one Granola download redirect")
        url = locations[0]
        download = re.fullmatch(r"https://dr2v7l5emb758\.cloudfront\.net/([0-9.]+)/Granola-\1-mac-universal\.dmg", url)
        if not download:
            raise ValueError("Unexpected Granola download URL")
        latest = download[1]
    else:
        latest = fetch("https://storage.googleapis.com/grok-build-public-artifacts/cli/stable").strip()
        version(latest)
        url = f"https://storage.googleapis.com/grok-build-public-artifacts/cli/grok-{latest}-linux-x86_64"
    if not newer(current, latest):
        return original
    print(f"{app.title()} {current} -> {latest}", flush=True)
    text = text[:match.start(1)] + latest + text[match.end(1):]
    # Only the src fetchurl block owns the release archive hash. Granola also
    # has a separately pinned native dependency before its package definition.
    pattern = r'(  src = fetchurl \{.*?\n    hash = ")[^"]+(";)'
    text, count = re.subn(pattern, lambda m: m[1] + prefetch(url) + m[2], text, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f"Missing {app} source hash")
    return text.encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pin_record(repo, app):
    return {"sha256": digest((repo / PINS[app]).read_bytes()),
            "flakeLockSha256": digest((repo / "flake.lock").read_bytes())}


def update_one(repo, app, host, user, owned=None):
    path = repo / PINS[app]
    original = path.read_bytes()
    expected_dirty = None
    if run(["git", "status", "--porcelain", "--", PINS[app]], repo).strip():
        previous = (owned or {}).get(PINS[app], {})
        if previous.get("sha256") != digest(original):
            raise ValueError(f"{PINS[app]} has local edits; skipping "
                             "(review and commit or stash this pin before updating it)")
        print(f"{app}: checking upstream from this updater's previous pin change", flush=True)
        expected_dirty = previous["sha256"]
    if app == "opencode1":
        return update_source_package(repo, "opencode-update", host, user, expected_dirty)
    if app == "t3":
        return update_source_package(repo, "t3-update", host, user, expected_dirty)
    data = candidate(repo, app, original)
    if data == original:
        print(f"{app}: current", flush=True)
        return False
    try:
        atomic_write(path, data, original)
        if path.read_bytes() != data:
            raise ValueError(f"{app}: pin changed during update")
    except BaseException:
        if path.read_bytes() == data:
            atomic_write(path, original, data)
        raise
    return True


def update_source_package(repo, name, host, user, expected_dirty=None):
    # Keep each specialist updater's own lock, signal handling and rollback.
    result = subprocess.run([sys.executable, str(repo / "scripts" / f"{name}.py"),
                             "--repo", str(repo), "--host", host, "--user", user,
                             *(["--expected-pin-sha256", expected_dirty] if expected_dirty else [])],
                            cwd=repo, timeout=7200)
    if result.returncode not in (0, 3):
        raise RuntimeError(f"{name} failed (exit {result.returncode})")
    return result.returncode == 3


def save_owned(path, owned):
    if not path.exists():
        path.touch(mode=0o600)
    atomic_write(path, encoded(owned), path.read_bytes())


def update(repo, app, host, user, ledger_path=None):
    owned = json.loads(ledger_path.read_bytes()) if ledger_path and ledger_path.exists() else {}
    if not isinstance(owned, dict) or any(
        key not in PINS.values() or not isinstance(value, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256", "")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("flakeLockSha256", "")))
        for key, value in owned.items()
    ):
        raise ValueError("Invalid app updater pin ledger")
    failed = False
    for name in APPS if app == "all" else (app,):
        if name not in APPS:
            print(f"{name}: handled by the parent system updater", flush=True)
            continue
        try:
            if ledger_path is None:
                update_one(repo, name, host, user)
            else:
                changed = update_one(repo, name, host, user, owned)
                # Record only successful changes or an already-owned OpenCode
                # revalidation against a new lock, never unknown user edits.
                if changed or (name == "opencode1" and PINS[name] in owned):
                    owned[PINS[name]] = pin_record(repo, name)
                    save_owned(ledger_path, owned)
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            print(f"{name}: update failed: {error}", file=sys.stderr, flush=True)
            failed = True
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "nix-config")
    parser.add_argument("--app", choices=("all", *APPS, "delta"), default="all")
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--user", default=os.environ.get("USER", "cjm"))
    args = parser.parse_args()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (args.host, args.user)):
        parser.error("Unexpected host or user name")
    repo = args.repo.expanduser().resolve()
    gitdir = Path(run(["git", "rev-parse", "--absolute-git-dir"], repo).strip())
    with (gitdir / "apps-update.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another app updater is running", file=sys.stderr)
            return 1
        return update(repo, args.app, args.host, args.user, gitdir / "apps-update-owned-pins.json")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as error:
        print(f"apps-update: {error}", file=sys.stderr)
        sys.exit(1)
