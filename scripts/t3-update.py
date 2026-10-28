#!/usr/bin/env python3
"""Refresh the stable T3 source pin and verify it before the system build."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile

PIN = "pkgs/t3code/sources.json"
FAKE_HASH = "sha256-" + base64.b64encode(bytes(32)).decode()


def run(args, repo, timeout=120):
    result = subprocess.run(args, cwd=repo, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-6000:])
    return result.stdout


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError(f"Unexpected stable version: {value!r}")
    return tuple(map(int, value.split(".")))


def valid_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", value):
        raise ValueError("Expected a SHA-256 SRI hash")
    return value


def latest_release(repo):
    release = json.loads(run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "30",
        "https://api.github.com/repos/pingdotgg/t3code/releases/latest",
    ], repo))
    tag = release.get("tag_name", "")
    if release.get("draft") is not False or release.get("prerelease") is not False or not tag.startswith("v"):
        raise ValueError("Expected an official stable T3 release")
    value = tag[1:]
    stable_version(value)
    return value


def atomic_write(path, data, expected):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".t3-update-", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != expected:
            raise ValueError(f"Preserved concurrent edit to {path}; inspect before retrying")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def encoded(pin):
    return (json.dumps(pin, indent=2) + "\n").encode()


def derivation(repo, host, user, dependency=False):
    selector = "p.unwrapped.pnpmDeps.drvPath" if dependency else "p.drvPath"
    apply = ('ps: let matches = builtins.filter (p: (p.pname or "") == "t3code") ps; '
             'in if builtins.length matches != 1 then throw "Expected one T3 package" '
             f'else let p = builtins.head matches; in {selector}')
    value = run([
        "nix", "eval", "--raw", "--no-write-lock-file",
        f"{repo}#nixosConfigurations.{host}.config.home-manager.users.{user}.home.packages",
        "--apply", apply,
    ], repo).strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+\.drv", value):
        raise ValueError("Unexpected T3 derivation path")
    return value


def dependency_hash(output, drv):
    if output.count("hash mismatch in fixed-output derivation") != 1:
        raise ValueError("Expected exactly one pnpm dependency hash mismatch")
    pattern = (r"hash mismatch in fixed-output derivation ['‘]" + re.escape(drv)
               + r"['’]:\s*specified:\s*" + re.escape(FAKE_HASH)
               + r"\s*got:\s*(sha256-[A-Za-z0-9+/]{43}=)")
    match = re.search(pattern, output)
    if not match:
        raise ValueError("Failure did not identify the expected T3 pnpm dependency hash")
    result = valid_hash(match[1])
    if result == FAKE_HASH:
        raise ValueError("Dependency build returned the placeholder hash")
    return result


def update(repo, host, user):
    path = repo / PIN
    dirty = run(["git", "status", "--porcelain", "--", PIN], repo)
    if dirty.strip():
        print(f"T3 Code: {PIN} has local edits; skipping", flush=True)
        return False
    original = path.read_bytes()
    current = json.loads(original)
    current_version = stable_version(current["version"])
    latest = latest_release(repo)
    if stable_version(latest) < current_version:
        raise ValueError("Refusing a T3 downgrade")
    if latest == current["version"]:
        print(f"T3 Code {latest} is current", flush=True)
        return False
    print(f"T3 Code {current['version']} -> {latest}; fetching source", flush=True)
    source = json.loads(run([
        "nix", "store", "prefetch-file", "--unpack", "--json",
        f"https://github.com/pingdotgg/t3code/archive/refs/tags/v{latest}.tar.gz",
    ], repo, 600))
    candidate = dict(current, version=latest, srcHash=valid_hash(source["hash"]), pnpmHash=FAKE_HASH)
    written = original
    try:
        data = encoded(candidate)
        atomic_write(path, data, written)
        written = data
        drv = derivation(repo, host, user, dependency=True)
        print("T3 Code: calculating pnpm dependency hash", flush=True)
        result = subprocess.run([
            "nix", "build", "--no-link", "--log-format", "raw", drv + "^*",
        ], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode == 0:
            raise ValueError("Placeholder dependency hash unexpectedly built successfully")
        try:
            candidate["pnpmHash"] = dependency_hash(result.stdout, drv)
        except ValueError:
            print(result.stdout[-6000:], file=sys.stderr)
            raise
        data = encoded(candidate)
        atomic_write(path, data, written)
        written = data
        print("T3 Code: dependency hash verified; building package", flush=True)
        package = derivation(repo, host, user)
        run(["nix", "build", "--no-link", "--log-format", "raw", package + "^*"], repo, 3600)
        if path.read_bytes() != written:
            raise ValueError("T3 pin changed during validation; inspect before retrying")
    except BaseException:
        if path.read_bytes() == written:
            atomic_write(path, original, written)
            print("T3 Code: update failed; restored the original pin", file=sys.stderr)
        else:
            print("T3 Code: preserved a concurrent pin edit; inspect before retrying", file=sys.stderr)
        raise
    print(f"T3 Code {latest}: package build passed; system activation is still pending", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    for value in (args.host, args.user):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            parser.error("Unexpected host or user name")
    # The Git directory is shared by all invocations of this updater, including
    # one-click runs started from different desktop sessions.
    gitdir = Path(run(["git", "rev-parse", "--absolute-git-dir"], repo).strip())
    with (gitdir / "t3-update.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("T3 Code: another updater is running", file=sys.stderr)
            return 1
        def interrupted(signum, frame):
            raise KeyboardInterrupt(f"Interrupted by signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        try:
            return 3 if update(repo, args.host, args.user) else 0
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            print(f"T3 Code: {error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
