"""Private, cross-process Gmail admission control; no tool is executed here.

Unsupported tools bypass this guard. Callers must reserve immediately before a
real invocation and finish it exactly once, with an explicit success/error
outcome, including transport failures. A denial is final for that invocation:
this module never sleeps, retries a tool, or replays a mutation. The caller must
not put arguments/results in logs. Duplicate reservations, including those with
the same owner, are denied while a lease is active. An abandoned lease expires, so callers
must terminate an invocation before the configured lease duration expires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import stat
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote
import uuid


MAX_ARGUMENT_BYTES = 65_536
_SEPARATORS = re.compile(r"[_.-]+")
_RATE_ERROR = re.compile(r"(?<![A-Za-z0-9_])(?:429|RATE_LIMIT_EXCEEDED)(?![A-Za-z0-9_])", re.I)
_READ_WORDS = {"get", "list", "search", "fetch", "read", "batchread"}
_MONDAY_CURRENT = {
    "mcp__codex_apps__monday_com_get_updates",
    "codex_apps.monday_com.get_updates",
}
_GMAIL_CURRENT_PREFIXES = ("mcp__codex_apps__gmail_", "codex_apps.gmail.")
_GMAIL_CURRENT_REQUIRED = {
    "read_email": "message_id",
    "batch_read_email": "message_ids",
}
_ISO_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?$")


@dataclass(frozen=True)
class Policy:
    min_spacing_seconds: float = 0.75
    lease_seconds: float = 120.0
    cooldown_base_seconds: float = 60.0
    cooldown_cap_seconds: float = 900.0
    jitter_fraction: float = 0.1

    def __post_init__(self) -> None:
        values = (self.min_spacing_seconds, self.lease_seconds,
                  self.cooldown_base_seconds, self.cooldown_cap_seconds,
                  self.jitter_fraction)
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid guard policy")
        if self.lease_seconds <= 0 or self.cooldown_cap_seconds < self.cooldown_base_seconds:
            raise ValueError("Invalid guard policy")


def _decision(allowed: bool, reason: str, retry: float = 0.0,
              lease_id: str | None = None) -> dict[str, Any]:
    return {"allowed": allowed, "reason": reason,
            "retry_after_seconds": max(0.0, retry), "lease_id": lease_id}


def is_gmail(tool_name: str) -> bool:
    """Match namespace tokens, never a substring or any argument content."""
    return isinstance(tool_name, str) and "gmail" in _SEPARATORS.split(tool_name.lower())


def _is_read(tool_name: str) -> bool:
    tokens = _SEPARATORS.split(tool_name.lower())
    try:
        action = tokens[tokens.index("gmail") + 1:]
    except ValueError:
        return False
    return bool(action and (action[0] in _READ_WORDS or action[:2] == ["batch", "read"]))


def _hash(*values: str) -> str:
    # Length-prefixing prevents ambiguous concatenations of owner identifiers.
    digest = hashlib.sha256()
    for value in values:
        data = value.encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _type_ok(value: Any, kind: str) -> bool:
    return {
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool)
                          and math.isfinite(value),
        "array": lambda: isinstance(value, list),
        "object": lambda: isinstance(value, dict),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }.get(kind, lambda: False)()


def _matches_schema(arguments: dict[str, Any], schema: Mapping[str, Any]) -> bool:
    """Validate the deliberately small manifest contract, failing closed on errors."""
    if not isinstance(schema, Mapping):
        return False
    try:
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(props, dict) or not isinstance(required, list):
            return False
        if any(not isinstance(key, str) or key not in arguments for key in required):
            return False
        if schema.get("additionalProperties") is False and any(key not in props for key in arguments):
            return False
        for key, value in arguments.items():
            if key not in props:
                continue
            rule = props[key]
            if not isinstance(rule, dict):
                return False
            kind = rule.get("type")
            if kind is not None and not _type_ok(value, kind):
                return False
            if "enum" in rule and value not in rule["enum"]:
                return False
            if "minItems" in rule and (not isinstance(value, list) or len(value) < rule["minItems"]):
                return False
            if "maxItems" in rule and (not isinstance(value, list) or len(value) > rule["maxItems"]):
                return False
            if "minimum" in rule and (not _type_ok(value, "number") or value < rule["minimum"]):
                return False
            if "maximum" in rule and (not _type_ok(value, "number") or value > rule["maximum"]):
                return False
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _iso_timestamp(value: Any) -> datetime | None:
    """Normalize ISO dates/times to UTC; a date or naive time means UTC."""
    if not isinstance(value, str) or not _ISO_DATE_TIME.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def validate_arguments(tool_name: str, arguments: Any,
                       manifest: Mapping[str, Mapping[str, Any]] | None = None) -> bool:
    """Unknown and write tools are intentionally outside schema validation."""
    if not isinstance(tool_name, str):
        return False
    read = is_gmail(tool_name) and _is_read(tool_name)
    monday = tool_name in _MONDAY_CURRENT
    if not (read or monday):
        return True
    if not isinstance(arguments, dict):
        return False
    if manifest is not None and tool_name in manifest:
        if not _matches_schema(arguments, manifest[tool_name]):
            return False
    operation = next((tool_name[len(prefix):] for prefix in _GMAIL_CURRENT_PREFIXES
                      if tool_name.startswith(prefix)), None)
    thread_read = operation in ("read_email_thread", "batch_read_email_threads")
    for key in ("link_id", "message_id"):
        if thread_read and key == "message_id" and arguments.get(key) is None:
            continue
        if key in arguments and (not isinstance(arguments[key], str) or not arguments[key].strip()):
            return False
    if read and tool_name.startswith(_GMAIL_CURRENT_PREFIXES):
        if not isinstance(arguments.get("link_id"), str) or not arguments["link_id"].strip():
            return False
        for prefix in _GMAIL_CURRENT_PREFIXES:
            if tool_name.startswith(prefix):
                required = _GMAIL_CURRENT_REQUIRED.get(tool_name[len(prefix):])
                if required is not None and required not in arguments:
                    return False
                break
    if "query" in arguments and not isinstance(arguments["query"], str):
        return False
    if "max_results" in arguments and (not _type_ok(arguments["max_results"], "integer")
                                       or arguments["max_results"] <= 0):
        return False
    if "message_ids" in arguments:
        ids = arguments["message_ids"]
        if thread_read and ids is None:
            pass
        elif (not isinstance(ids, list) or not 1 <= len(ids)
                or (not thread_read and len(ids) > 100)
                or any(not isinstance(value, str) or not value.strip() for value in ids)):
            return False
    if thread_read:
        singular = operation == "read_email_thread"
        keys = ("message_id", "thread_id") if singular else ("message_ids", "thread_ids")
        for key in keys:
            value = arguments.get(key)
            if value is None:
                continue
            if singular:
                if not isinstance(value, str) or not value.strip():
                    return False
            elif (not isinstance(value, list)
                  or any(not isinstance(item, str) or not item.strip() for item in value)):
                return False
        if not any(arguments.get(key) for key in keys):
            return False
    if monday:
        if (not isinstance(arguments.get("objectId"), str) or not arguments["objectId"].strip()
                or arguments.get("objectType") not in ("Item", "Board")
                or not isinstance(arguments.get("link_id"), str) or not arguments["link_id"].strip()
                or "objectIds" in arguments):
            return False
        for key, maximum in (("page", None), ("limit", 100)):
            if key in arguments and (not _type_ok(arguments[key], "integer")
                                     or arguments[key] < 1
                                     or (maximum is not None and arguments[key] > maximum)):
                return False
        for key in ("includeAssets", "includeItemUpdates", "includeReplies"):
            if key in arguments and not isinstance(arguments[key], bool):
                return False
        if "fromDate" in arguments or "toDate" in arguments:
            if (arguments["objectType"] != "Board" or "fromDate" not in arguments
                    or "toDate" not in arguments
                    or not isinstance(arguments["fromDate"], str)
                    or not isinstance(arguments["toDate"], str)):
                return False
            start = _iso_timestamp(arguments["fromDate"])
            end = _iso_timestamp(arguments["toDate"])
            if start is None or end is None or start > end:
                return False
    return True


def _bounded_arguments(arguments: Any) -> bool:
    try:
        # Encoding is for size validation only; it is never persisted or logged.
        size = 0
        for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(arguments):
            size += len(chunk)
            if size > MAX_ARGUMENT_BYTES:
                return False
        return True
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _rate_limited(error: Any, status_code: Any) -> bool:
    if status_code in (429, "429"):
        return True
    if isinstance(error, str):
        return len(error) <= MAX_ARGUMENT_BYTES and bool(_RATE_ERROR.search(error))
    if isinstance(error, dict):
        # Only error/status fields qualify; response bodies and success text do not.
        for key in ("code", "status", "status_code", "type", "message", "error"):
            value = error.get(key)
            if isinstance(value, (str, int)) and value in (429, "429"):
                return True
            if isinstance(value, str) and len(value) <= MAX_ARGUMENT_BYTES and _RATE_ERROR.search(value):
                return True
        nested = error.get("error")
        if isinstance(nested, dict):
            return any(nested.get(key) in (429, "429", "RATE_LIMIT_EXCEEDED")
                       for key in ("code", "status", "type"))
    return False


def _retry_delay(value: Any, now: float) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0
    except (ValueError, TypeError, OverflowError):
        pass
    if not isinstance(value, str) or len(value) > 128:
        return 0.0
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return 0.0


class Guard:
    def __init__(self, db_path: str | os.PathLike[str], *, policy: Policy | None = None,
                 clock: Callable[[], float] = time.time,
                 random_value: Callable[[], float] = random.random,
                 manifest: Mapping[str, Mapping[str, Any]] | None = None):
        self.path = Path(os.path.abspath(os.fspath(db_path)))
        self.policy = policy or Policy()
        self.clock = clock
        self.random_value = random_value
        self.manifest = manifest

    def _connect(self) -> sqlite3.Connection:
        # Walk using dirfds so symlink components are never followed. The final
        # private directory also protects SQLite's journal and lock sidecars.
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = self.path.parent.parts[1:]
            if not parts:
                raise OSError("Unsafe state directory")
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
                if index == len(parts) - 1:
                    info = os.fstat(directory)
                    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                        raise OSError("Unsafe state directory")
            filename = self.path.name
            descriptor = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.geteuid() or info.st_nlink != 1):
                    raise OSError("Unsafe state file")
            finally:
                os.close(descriptor)
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    info = os.stat(filename + suffix, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.geteuid() or info.st_nlink != 1):
                    raise OSError("Unsafe state sidecar")
            uri = "file:/proc/self/fd/" + str(directory) + "/" + quote(filename, safe="") + "?mode=rw"
            connection = sqlite3.connect(uri, uri=True, timeout=0.25, isolation_level=None)
            try:
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS state (scope TEXT PRIMARY KEY, cooldown_until REAL NOT NULL DEFAULT 0, strikes INTEGER NOT NULL DEFAULT 0, last_start REAL NOT NULL DEFAULT 0)")
                connection.execute("CREATE TABLE IF NOT EXISTS lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1), lease_id TEXT NOT NULL, owner_hash TEXT NOT NULL, account_hash TEXT NOT NULL, expires REAL NOT NULL, started REAL NOT NULL)")
                if "started" not in {row[1] for row in connection.execute("PRAGMA table_info(lease)")}:
                    connection.execute("ALTER TABLE lease ADD COLUMN started REAL NOT NULL DEFAULT 0")
                return connection
            except Exception:
                connection.close()
                raise
        finally:
            os.close(directory)

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value):
            raise ValueError("Invalid clock")
        return value

    @staticmethod
    def _owner(tool_name: str, session_id: str, call_id: str) -> str:
        if any(not isinstance(value, str) or not value or len(value) > 4096
               for value in (tool_name, session_id, call_id)):
            raise ValueError("Invalid invocation identity")
        return _hash("owner", tool_name, session_id, call_id)

    def reserve(self, tool_name: str, session_id: str, call_id: str,
                arguments: Any) -> dict[str, Any]:
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 4096:
            return _decision(False, "invalid_arguments")
        gmail = is_gmail(tool_name)
        known = gmail or tool_name in _MONDAY_CURRENT
        if not known:
            return _decision(True, "unsupported_tool")
        validates = not gmail or _is_read(tool_name)
        if validates and (not _bounded_arguments(arguments) or not validate_arguments(tool_name, arguments, self.manifest)):
            return _decision(False, "invalid_arguments")
        if not gmail:
            return _decision(True, "validated")
        connection = None
        try:
            owner = self._owner(tool_name, session_id, call_id)
            account = "global"
            if isinstance(arguments, dict):
                account_value = arguments.get("link_id") or arguments.get("account_id")
                if isinstance(account_value, str) and account_value:
                    account = _hash("account", account_value)
            connection = self._connect()
            now = self._now()
            connection.execute("INSERT OR IGNORE INTO state(scope, last_start) VALUES ('global', ?)", (now - self.policy.min_spacing_seconds,))
            if account != "global":
                connection.execute("INSERT OR IGNORE INTO state(scope) VALUES (?)", (account,))
            connection.execute("DELETE FROM lease WHERE expires <= ?", (now,))
            lease = connection.execute("SELECT expires FROM lease WHERE singleton=1").fetchone()
            cooldown = connection.execute("SELECT MAX(cooldown_until) FROM state WHERE scope IN ('global', ?)", (account,)).fetchone()[0]
            last_start = connection.execute("SELECT last_start FROM state WHERE scope='global'").fetchone()[0]
            spacing = last_start + self.policy.min_spacing_seconds - now
            if cooldown > now:
                result = _decision(False, "rate_limit_cooldown", max(cooldown - now, spacing))
            elif lease is not None:
                result = _decision(False, "gmail_in_flight", max(lease[0] - now, spacing))
            elif spacing > 0:
                result = _decision(False, "gmail_spacing", spacing)
            else:
                lease_id = uuid.uuid4().hex
                connection.execute("INSERT INTO lease VALUES (1, ?, ?, ?, ?, ?)",
                                   (lease_id, owner, account, now + self.policy.lease_seconds, now))
                connection.execute("UPDATE state SET last_start=? WHERE scope='global'", (now,))
                result = _decision(True, "reserved", lease_id=lease_id)
            connection.commit()
            return result
        except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
            return _decision(False, "state_unavailable")
        finally:
            if connection is not None:
                connection.close()

    def active_lease(self) -> dict[str, Any] | None:
        """Private reconciliation snapshot; callers must fence finish by lease_id."""
        connection = None
        try:
            connection = self._connect()
            row = connection.execute("SELECT lease_id, owner_hash, started, expires FROM lease WHERE singleton=1").fetchone()
            connection.commit()
            if row is None or row[3] <= self._now():
                return None
            return {"lease_id": row[0], "owner_hash": row[1], "started": row[2] or row[3] - self.policy.lease_seconds, "expires": row[3]}
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            if connection is not None:
                connection.close()

    def finish(self, tool_name: str, session_id: str, call_id: str, *,
               outcome: str, lease_id: str | None = None, error: Any = None,
               status_code: Any = None, retry_after: Any = None,
               completed_at: float | None = None) -> dict[str, Any]:
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 4096:
            return _decision(False, "invalid_arguments")
        if not is_gmail(tool_name):
            return _decision(True, "unsupported_tool")
        if outcome not in ("success", "error"):
            return _decision(False, "invalid_outcome")
        connection = None
        try:
            owner = self._owner(tool_name, session_id, call_id)
            connection = self._connect()
            now = self._now()
            row = connection.execute("SELECT lease_id, owner_hash, account_hash, started, expires FROM lease WHERE singleton=1").fetchone()
            if row is None or row[1] != owner or (lease_id is not None and row[0] != lease_id):
                connection.rollback()
                return _decision(False, "lease_mismatch")
            acquired = row[3] or row[4] - self.policy.lease_seconds
            if completed_at is not None and (isinstance(completed_at, bool) or not isinstance(completed_at, (int, float)) or not math.isfinite(completed_at) or completed_at < acquired):
                connection.rollback()
                return _decision(False, "lease_mismatch")
            connection.execute("DELETE FROM lease WHERE singleton=1")
            if outcome == "error" and _rate_limited(error, status_code):
                scopes = ["global"] if row[2] == "global" else ["global", row[2]]
                delay = _retry_delay(retry_after, now)
                jitter = float(self.random_value())
                if not math.isfinite(jitter):
                    jitter = 0.0
                jitter = min(1.0, max(0.0, jitter))
                for scope in scopes:
                    strikes = connection.execute("SELECT strikes FROM state WHERE scope=?", (scope,)).fetchone()[0]
                    base = min(self.policy.cooldown_cap_seconds,
                               self.policy.cooldown_base_seconds * (2 ** min(strikes, 30)))
                    backoff = min(self.policy.cooldown_cap_seconds, base * (1 + self.policy.jitter_fraction * jitter))
                    connection.execute("UPDATE state SET cooldown_until=MAX(cooldown_until, ?), strikes=MIN(strikes+1, 31) WHERE scope=?",
                                       (now + max(delay, backoff), scope))
                until = connection.execute("SELECT MAX(cooldown_until) FROM state WHERE scope IN ('global', ?)", (row[2],)).fetchone()[0]
                result = _decision(True, "rate_limit_recorded", until - now)
            else:
                if outcome == "success":
                    connection.execute("UPDATE state SET strikes=0 WHERE scope IN ('global', ?)", (row[2],))
                result = _decision(True, "released")
            connection.commit()
            return result
        except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
            return _decision(False, "state_unavailable")
        finally:
            if connection is not None:
                connection.close()
