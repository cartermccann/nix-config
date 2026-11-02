"""Runs real QML from the overlay under the packaged Quickshell, headless
(QT_QPA_PLATFORM=offscreen, no Wayland/X display, a private runtime dir), so
Process callbacks, singletons and Timers behave as in the shell. Set QS to
the Quickshell binary (e.g. <pond-interface>/libexec/pond-interface/bin/qs);
tests are skipped without it. Nothing touches the live session."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

QS = os.environ.get("QS")
OVERLAY_SHELL = Path(__file__).resolve().parents[1] / "overlay" / "shell"


class Harness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pond-qml-")
        self.root = Path(self.tmp.name) / "shell"
        self.root.mkdir()

    def copy(self, relative):
        """Copy an overlay file into the harness at the same relative path."""
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(OVERLAY_SHELL / relative, target)

    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def run(self, env=None, timeout=30):
        """Run shell.qml; return (results, log). `results` maps each
        `console.log("RESULT", key, json)` key to its decoded values."""
        runtime = Path(self.tmp.name) / "run"
        runtime.mkdir(exist_ok=True)
        environment = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        environment.update(QT_QPA_PLATFORM="offscreen", XDG_RUNTIME_DIR=str(runtime),
                           XDG_STATE_HOME=str(Path(self.tmp.name) / "state"),
                           XDG_CACHE_HOME=str(Path(self.tmp.name) / "cache"))
        environment.update(env or {})
        try:
            proc = subprocess.run([QS, "-p", str(self.root / "shell.qml"), "--no-duplicate"], env=environment,
                                  capture_output=True, text=True, timeout=timeout)
            log = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or b"") + (error.stderr or b"")
            raise AssertionError("QML harness timed out:\n"
                                 + (output.decode("utf-8", "replace") if isinstance(output, bytes) else output)[-4000:])
        results = {}
        for line in log.splitlines():
            marker = line.find("RESULT ")
            if marker < 0:
                continue
            key, _, value = line[marker + 7:].partition(" ")
            results.setdefault(key, []).append(json.loads(value))
        if "DONE" not in results:
            raise AssertionError("QML harness did not finish:\n" + log[-4000:])
        return results, log

    def close(self):
        self.tmp.cleanup()
