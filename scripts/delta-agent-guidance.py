#!/usr/bin/env python3
"""Merge the pinned Delta enrollment guidance during Home Manager activation."""

import argparse
import os
from pathlib import Path
import stat
import tempfile


BEGIN = "<!-- delta-agent-history:enrollment:begin -->"
END = "<!-- delta-agent-history:enrollment:end -->"
MAX_BYTES = 1024 * 1024


def snapshot(path, *, owned=True, missing=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing:
            return None
        raise ValueError("Guidance source is unavailable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or owned and (info.st_uid != os.getuid() or not info.st_mode & stat.S_IWUSR)):
            raise ValueError("Guidance files must be regular, owned and writable by the current user")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Guidance file exceeds its size limit")
        return data, stat.S_IMODE(info.st_mode), (info.st_dev, info.st_ino, info.st_mtime_ns)
    finally:
        os.close(fd)


def merge(old, instruction):
    text = old.decode("utf-8")
    block = BEGIN + "\n" + instruction.rstrip() + "\n" + END
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
            raise ValueError("Existing Delta guidance markers require review")
        return (text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]).encode()
    separator = "\n" if text.endswith("\n") else "\n\n" if text else ""
    return (text + separator + block + "\n").encode()


def install(home, source):
    home = Path(home)
    if not home.is_absolute() or home != home.resolve(strict=True):
        raise ValueError("Use the canonical absolute home directory")
    instruction = snapshot(Path(source), owned=False)[0].decode("utf-8")
    plans = []
    for relative in (".codex/AGENTS.md", ".claude/CLAUDE.md"):
        target = home / relative
        if target.parent.is_symlink():
            raise ValueError("Guidance directories must not be symlinks")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = target.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise ValueError("Guidance directory must be owned and not writable by other users")
        old = snapshot(target, missing=True)
        data = merge(old[0] if old else b"", instruction)
        if len(data) > MAX_BYTES:
            raise ValueError("Merged guidance exceeds its size limit")
        plans.append((target, old, data))
    changed = 0
    for target, old, data in plans:
        if old and old[0] == data:
            continue
        if snapshot(target, missing=True) != old:
            raise ValueError("Guidance changed concurrently; retry activation after review")
        fd, temporary = tempfile.mkstemp(prefix=".delta-guidance-", dir=target.parent)
        try:
            os.fchmod(fd, old[1] if old else 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if snapshot(target, missing=True) != old:
                raise ValueError("Guidance changed concurrently; retry activation after review")
            os.replace(temporary, target)
            changed += 1
        finally:
            Path(temporary).unlink(missing_ok=True)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--instruction", type=Path, required=True)
    args = parser.parse_args()
    try:
        changed = install(args.home, args.instruction)
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit("Delta guidance activation failed: " + type(error).__name__) from None
    print(f"Delta enrollment guidance: {changed} files updated")


if __name__ == "__main__":
    main()
