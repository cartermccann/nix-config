"""Exercise the real v1 server API in an empty profile, without user credentials."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request


def main(binary, version):
    with tempfile.TemporaryDirectory(prefix="opencode1-test-") as directory:
        root = Path(directory)
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
        env.update(
            HOME=str(root),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_STATE_HOME=str(root / "state"),
            OPENCODE_DISABLE_MODELS_FETCH="true",
            OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
            OPENCODE_DISABLE_AUTOUPDATE="true",
        )
        actual = subprocess.check_output(
            [binary, "--version"], env=env, cwd=root, text=True, timeout=20
        ).strip()
        assert actual == version, (actual, version)
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [binary, "serve", "--hostname", "127.0.0.1", "--port", "0"],
                env=env, cwd=root, stdout=log, stderr=log, start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    log.seek(0)
                    output = log.read()
                    match = re.search(r"listening on (http://127\.0\.0\.1:\d+)", output)
                    if match:
                        break
                    if process.poll() is not None:
                        raise AssertionError(f"server exited: {output}")
                    time.sleep(0.1)
                else:
                    raise AssertionError(f"server startup timed out: {output}")
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for path in ("/global/health", "/session"):
                    with opener.open(match[1] + path, timeout=10) as response:
                        assert response.status == 200
                        assert response.headers.get_content_type() == "application/json"
                        body = json.load(response)
                    if path == "/global/health":
                        assert body.get("healthy") is True, body
                        assert body.get("version") == version, body
                    else:
                        assert body == [], body
                print(f"OpenCode {version}: v1 health and session APIs passed")
            finally:
                # Always reap our private server, including on a failed assertion.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except ProcessLookupError:
                    process.wait()


if __name__ == "__main__":
    main(*sys.argv[1:])
