#!/usr/bin/env python3
"""Refresh and validate the pinned OpenCode v1 provider, including dependency drift."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile

PIN = "pkgs/opencode1/sources.json"
HASH = r"sha256-[A-Za-z0-9+/]{43}="


def run(args, repo, timeout=120):
    result = subprocess.run(args, cwd=repo, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-6000:])
    return result.stdout


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"1\.[0-9]+\.[0-9]+", value):
        raise ValueError(f"Expected a stable OpenCode v1 version: {value!r}")
    return tuple(map(int, value.split(".")))


def valid_hash(value):
    if not isinstance(value, str) or not re.fullmatch(HASH, value):
        raise ValueError("Expected a SHA-256 SRI hash")
    return value


def latest_release(repo):
    releases = json.loads(run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "30",
        "https://api.github.com/repos/anomalyco/opencode/releases?per_page=100",
    ], repo))
    versions = [r["tag_name"][1:] for r in releases
                if r.get("draft") is False and r.get("prerelease") is False
                and re.fullmatch(r"v1\.[0-9]+\.[0-9]+", r.get("tag_name", ""))]
    if not versions:
        raise ValueError("No stable v1 release in the upstream release page; refusing a v2 migration")
    return max(versions, key=stable_version)


def atomic_write(path, data, expected):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".opencode-update-", delete=False) as handle:
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


def derivation(repo, host, user, kind="package"):
    selector = {"package": "p.unwrapped.drvPath", "dependency": "p.unwrapped.node_modules.drvPath",
                "server": "p.unwrapped.tests.server.drvPath"}[kind]
    apply = ('ps: let matches = builtins.filter (p: (p.name or "") == "opencode1") ps; '
             'in if builtins.length matches != 1 then throw "Expected one OpenCode v1 package" '
             f'else let p = builtins.head matches; in {selector}')
    value = run([
        "nix", "eval", "--raw", "--no-write-lock-file",
        f"{repo}#nixosConfigurations.{host}.config.home-manager.users.{user}.home.packages",
        "--apply", apply,
    ], repo).strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+\.drv", value):
        raise ValueError("Unexpected OpenCode derivation path")
    return value


def dependency_hash(output, drv, expected):
    if output.count("hash mismatch in fixed-output derivation") != 1:
        raise ValueError("Expected exactly one OpenCode dependency hash mismatch")
    pattern = (r"hash mismatch in fixed-output derivation ['‘]" + re.escape(drv)
               + r"['’]:\s*specified:\s*" + re.escape(expected)
               + r"\s*got:\s*(" + HASH + r")")
    match = re.search(pattern, output)
    if not match:
        raise ValueError("Failure did not identify the expected OpenCode dependency hash")
    actual = valid_hash(match[1])
    if actual == expected:
        raise ValueError("Dependency mismatch returned the existing hash")
    return actual


def update(repo, host, user, expected_pin_sha256=None):
    path = repo / PIN
    original = path.read_bytes()
    if expected_pin_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_pin_sha256):
            raise ValueError("Expected pin SHA-256 must be 64 lowercase hexadecimal characters")
        if hashlib.sha256(original).hexdigest() != expected_pin_sha256:
            raise ValueError(f"{PIN} changed since the trusted updater snapshot")
    if run(["git", "status", "--porcelain", "--", PIN], repo).strip() and expected_pin_sha256 is None:
        raise ValueError(f"{PIN} has local edits; refusing to overwrite them")
    current = json.loads(original)
    stable_version(current["version"])
    valid_hash(current["srcHash"])
    valid_hash(current["nodeModulesHash"])
    latest = latest_release(repo)
    if stable_version(latest) < stable_version(current["version"]):
        raise ValueError("Refusing an OpenCode downgrade")
    candidate = dict(current)
    written = original
    try:
        if latest != current["version"]:
            print(f"OpenCode 1 {current['version']} -> {latest}; fetching source", flush=True)
            source = json.loads(run([
                "nix", "store", "prefetch-file", "--unpack", "--json",
                f"https://github.com/anomalyco/opencode/archive/refs/tags/v{latest}.tar.gz",
            ], repo, 600))
            candidate.update(version=latest, srcHash=valid_hash(source["hash"]))
            data = encoded(candidate)
            atomic_write(path, data, written)
            written = data
        print(f"OpenCode 1 {latest}: validating dependencies against the selected flake inputs", flush=True)
        dependency = derivation(repo, host, user, "dependency")
        result = subprocess.run([
            "nix", "build", "--no-link", "--log-format", "raw", dependency + "^*",
        ], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode:
            try:
                candidate["nodeModulesHash"] = dependency_hash(result.stdout, dependency, candidate["nodeModulesHash"])
            except ValueError:
                print(result.stdout[-6000:], file=sys.stderr)
                raise
            data = encoded(candidate)
            atomic_write(path, data, written)
            written = data
            print("OpenCode 1: dependency output changed; verified the exact new hash", flush=True)
        for kind in ("package", "server"):
            drv = derivation(repo, host, user, kind)
            run(["nix", "build", "--no-link", "--log-format", "raw", drv + "^*"], repo, 3600)
        if path.read_bytes() != written:
            raise ValueError("OpenCode pin changed during validation; inspect before retrying")
    except BaseException:
        if path.read_bytes() == written:
            if written != original:
                atomic_write(path, original, written)
                print("OpenCode 1: update failed; restored original pin", file=sys.stderr)
        else:
            print("OpenCode 1: preserved a concurrent pin edit", file=sys.stderr)
        raise
    print(f"OpenCode 1 {latest}: package and real v1 server API checks passed", flush=True)
    return written != original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--user", required=True)
    parser.add_argument("--expected-pin-sha256", help="Exact SHA-256 of a trusted prior updater pin")
    args = parser.parse_args()
    for value in (args.host, args.user):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            parser.error("Unexpected host or user name")
    if args.expected_pin_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.expected_pin_sha256):
        parser.error("Expected pin SHA-256 must be 64 lowercase hexadecimal characters")
    repo = args.repo.resolve()
    gitdir = Path(run(["git", "rev-parse", "--absolute-git-dir"], repo).strip())
    with (gitdir / "opencode-update.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
        return 3 if update(repo, args.host, args.user, args.expected_pin_sha256) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"opencode-update: {error}", file=sys.stderr)
        sys.exit(1)
