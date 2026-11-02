#!/usr/bin/env python3
"""Read-only sidebar telemetry, plus the stdin-lifetime keep-awake child.

One watcher per shell, shared by all outputs. CPU/memory/disk every five
seconds, GPU at most every ten, recorder state every second. No services are
changed here; missing hardware is null, never an invented zero.
"""
import csv
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time


def recorder_active(path, proc=Path('/proc')):
    try:
        pid = int(path.read_text().strip())
        if pid <= 0:
            return False
        return (proc / str(pid) / 'comm').read_text().strip() == 'pw-record'
    except (OSError, ValueError):
        return False


def dictation(home, proc=Path('/proc')):
    state = home / '.local/state/parakeet-dictation'
    return {'live': recorder_active(state / 'recorder.pid', proc),
            'batch': recorder_active(state / 'batch-recorder.pid', proc),
            'liveAvailable': os.access(home / '.local/bin/toggle-dictation.sh', os.X_OK),
            'batchAvailable': os.access(home / '.local/bin/toggle-dictation-batch.sh', os.X_OK)}


def cpu_sample(proc):
    # guest/guest_nice are already included in user/nice; don't double count.
    values = [int(v) for v in (proc / 'stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def cpu_usage(before, after):
    if before is None or after[0] <= before[0]:
        return None
    return round(max(0, min(100, 100 * (1 - (after[1] - before[1]) / (after[0] - before[0])))), 1)


def memory(proc):
    values = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in (proc / 'meminfo').read_text().splitlines() if ':' in line}
    total, available = values['MemTotal'], values['MemAvailable']
    used = max(0, total - available)
    return {'used': used, 'total': total, 'percent': round(used / total * 100, 1)}


def gpu(run=subprocess.run):
    if not shutil.which('nvidia-smi'):
        return None
    try:
        result = run(['nvidia-smi', '--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total',
                      '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2)
        if result.returncode:
            return None
        name, busy, temp, used, total = next(csv.reader(result.stdout.splitlines(), skipinitialspace=True))
        def number(value):
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) else None
            except ValueError:
                return None
        return {'name': name, 'percent': number(busy), 'temperature': number(temp),
                'usedMiB': number(used), 'totalMiB': number(total)}
    except (OSError, subprocess.TimeoutExpired, ValueError, StopIteration):
        return None


def emit(message):
    print(json.dumps(message, allow_nan=False), flush=True)


def watch(proc=Path('/proc'), home=None):
    home = home or Path.home()
    previous = None
    last_dictation = None
    hardware_at = gpu_at = 0
    gpu_state = None
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin, selectors.EVENT_READ)
    while True:
        now = time.monotonic()
        current = dictation(home, proc)
        if current != last_dictation:
            emit({'type': 'dictation', **current})
            last_dictation = current
        if now >= hardware_at:
            result = {'type': 'hardware', 'cpu': None, 'memory': None, 'disk': None, 'gpu': None}
            try:
                sample = cpu_sample(proc)
                result['cpu'] = cpu_usage(previous, sample)
                previous = sample
            except (OSError, ValueError, IndexError):
                previous = None
            try:
                result['memory'] = memory(proc)
            except (OSError, ValueError, KeyError, ZeroDivisionError):
                pass
            try:
                disk = shutil.disk_usage(home)
                result['disk'] = {'used': disk.used, 'total': disk.total,
                                  'percent': round(disk.used / disk.total * 100, 1)}
            except (OSError, ZeroDivisionError):
                pass
            if now >= gpu_at:
                gpu_state = gpu()
                gpu_at = now + 10
            result['gpu'] = gpu_state
            emit(result)
            hardware_at = now + 5
        # EOF closes the watcher even after a shell crash, without a leaked
        # background sampler. No data is ever sent to its stdin.
        if selector.select(timeout=1):
            if not sys.stdin.buffer.read(1):
                return


def hold():
    # systemd-inhibit only starts us after it has acquired the idle inhibitor.
    emit({'ready': True})
    # Explicit stop or EOF (including a killed shell) releases the inhibitor.
    sys.stdin.readline()


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('watch', 'hold'):
        sys.exit('usage: control.py watch|hold')
    try:
        watch() if sys.argv[1] == 'watch' else hold()
    except BrokenPipeError:
        pass
