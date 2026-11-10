#!/usr/bin/env python3
"""Check staged Delta, recorder and T3 packages without accessing live recorder state."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


PROBE_SOURCE = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from t3_delta.engine import Engine
from t3_delta.delta_sink import DeltaSink

class DiscoveryReached(Exception):
    pass

class Source:
    def environment(self):
        return {"environmentId": "compatibility-fixture", "serverVersion": sys.argv[2]}
    def projects(self):
        raise DiscoveryReached()

engine = Engine.__new__(Engine)
engine.config = {"environment_id": "compatibility-fixture"}
engine.source = Source()
engine.set_progress = lambda stage: None
try:
    engine.poll()
except DiscoveryReached:
    pass
else:
    raise RuntimeError("Recorder did not reach its discovery boundary")
sink = DeltaSink.__new__(DeltaSink)
sink._command = lambda args: json.loads(sys.argv[3])
contract = sink._contract()
print(json.dumps({"t3_version": sys.argv[2], "delta_contract": contract}))
'''


def run(argv, repo=None, timeout=120):
    result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-4000:])
    return result.stdout


def packages(repo, host, user):
    apply = '''ps: let
      one = label: pred: let found = builtins.filter pred ps;
        in if builtins.length found != 1 then throw ("Expected one " + label + " package")
        else let p = builtins.head found; in {path = toString p; drv = p.drvPath;};
    in {
      recorder = one "Delta recorder" (p: (p.pname or "") == "delta-agent-history");
      delta = one "Zed Delta" (p: builtins.match "zed-delta-.*" p.name != null);
    }'''
    values = json.loads(run([
        "nix", "eval", "--json", "--no-write-lock-file",
        f"{repo}#nixosConfigurations.{host}.config.home-manager.users.{user}.home.packages",
        "--apply", apply,
    ], repo))
    for name in ("recorder", "delta"):
        for key, suffix in (("path", ""), ("drv", r"\.drv")):
            if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\n]+" + suffix, values[name][key]):
                raise ValueError(f"Unexpected {name} {key}")
    run(["nix", "build", "--no-link", "--no-write-lock-file",
         values["recorder"]["drv"] + "^*", values["delta"]["drv"] + "^*"], repo, 1800)
    return values


def isolated_contract(binary):
    bubblewrap = shutil.which("bwrap")
    if not bubblewrap:
        raise RuntimeError("Delta compatibility requires bubblewrap; refusing an unisolated CLI probe")
    raw = run([
        bubblewrap, "--unshare-all", "--new-session", "--die-with-parent",
        "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--dir", "/run", "--dir", "/home", "--clearenv",
        "--setenv", "HOME", "/tmp", "--setenv", "XDG_RUNTIME_DIR", "/tmp",
        str(binary), "--json", "thread", "contract",
    ], timeout=20)
    if len(raw.encode()) > 65536:
        raise ValueError("Delta contract response exceeds its budget")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("operation") != "contract":
        raise ValueError("Candidate Delta did not return its CLI contract")
    return value


def probe(recorder, version, contract):
    sites = list(Path(recorder).glob("lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError("Expected one candidate recorder Python package directory")
    return json.loads(run([sys.executable, "-I", "-c", PROBE_SOURCE,
                           str(sites[0]), version, json.dumps(contract)], timeout=20))


def check(repo, host, user, version=None):
    if version is None:
        version = json.loads((repo / "pkgs/t3code/sources.json").read_bytes())["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable candidate T3 version")
    selected = packages(repo, host, user)
    contract = isolated_contract(Path(selected["delta"]["path"]) / "bin/zed-delta")
    result = probe(selected["recorder"]["path"], version, contract)
    print(f"Delta recorder compatibility passed: T3 {version}, CLI contract {result['delta_contract']}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--t3-version")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (args.host, args.user)):
        parser.error("Unexpected host or user name")
    try:
        check(args.repo.resolve(), args.host, args.user, args.t3_version)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Delta recorder compatibility failed; candidate remains unactivated: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
