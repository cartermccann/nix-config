import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from agent_tools.runner import run_command


def python(code, *arguments):
    return [sys.executable, "-c", code, *arguments]


def alive(pid):
    try:
        # A killed descendant may remain a zombie until its new parent reaps it.
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return fields[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


class RunnerTests(unittest.TestCase):
    def assert_terminated(self, pid):
        deadline = time.monotonic() + 1
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(alive(pid), f"child process {pid} remains alive")

    def test_literal_argv_has_no_shell_expansion(self):
        arguments = ["", "$HOME", "`printf expanded`", "$(printf expanded)", "a b", "one\ntwo", "semi;colon", "*.txt"]
        result, status = run_command(python("import json,sys;print(json.dumps(sys.argv[1:]))", *arguments))
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(result["stdout"]), arguments)
        self.assertNotIn("argv", result)
        self.assertNotIn("env", result)

    def test_stdout_stderr_and_nonzero_status_are_distinct(self):
        result, status = run_command(python("import sys;print('out');print('err',file=sys.stderr);sys.exit(7)"))
        self.assertEqual((status, result["exit_code"]), (7, 7))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stdout"], "out\n")
        self.assertEqual(result["stderr"], "err\n")
        self.assertIsNone(result["signal"])
        self.assertFalse(result["timed_out"])

    def test_signal_preserves_raw_exit_and_wrapper_convention(self):
        result, status = run_command(python("import os,signal;os.kill(os.getpid(),signal.SIGTERM)"))
        self.assertEqual(result["exit_code"], -signal.SIGTERM)
        self.assertEqual(result["signal"], signal.SIGTERM)
        self.assertEqual(status, 128 + signal.SIGTERM)

    def test_huge_concurrent_output_is_drained_and_bounded(self):
        code = """import os,threading
def write(fd, value):
    for _ in range(512):
        os.write(fd, value * 8192)
a=threading.Thread(target=write,args=(1,b'A'))
b=threading.Thread(target=write,args=(2,b'B'))
a.start();b.start();a.join();b.join()
"""
        result, status = run_command(python(code), timeout=5, max_output_bytes=257)
        self.assertEqual(status, 0)
        self.assertEqual(result["stdout"], "A" * 257)
        self.assertEqual(result["stderr"], "B" * 257)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])

    def test_exact_cap_is_not_marked_truncated(self):
        result, status = run_command(python("import os;os.write(1,b'abc')"), max_output_bytes=3)
        self.assertEqual(status, 0)
        self.assertEqual(result["stdout"], "abc")
        self.assertFalse(result["stdout_truncated"])

    def test_invalid_utf8_uses_replacement(self):
        result, status = run_command(python("import os;os.write(1,b'\\xffabc');os.write(2,b'\\xfe')"))
        self.assertEqual(status, 0)
        self.assertEqual(result["stdout"], "\ufffdabc")
        self.assertEqual(result["stderr"], "\ufffd")

    def test_timeout_kills_leader_and_descendant(self):
        code = """import os,subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
print(str(os.getpid())+' '+str(p.pid),flush=True)
time.sleep(60)
"""
        result, status = run_command(python(code), timeout=0.2)
        self.assertEqual(status, 124)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertLess(result["duration_ms"], 2000)
        leader, child = map(int, result["stdout"].split())
        self.assert_terminated(leader)
        self.assert_terminated(child)

    def test_deadline_covers_descendant_pipes_after_leader_exit(self):
        code = """import subprocess,sys
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
print(p.pid,flush=True)
"""
        result, status = run_command(python(code), timeout=0.2)
        self.assertEqual(status, 124)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["exit_code"], 0)
        self.assert_terminated(int(result["stdout"].strip()))

    def test_sigterm_ignoring_descendant_is_killed(self):
        code = """import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c',
 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'])
print(p.pid,flush=True)
time.sleep(60)
"""
        result, status = run_command(python(code), timeout=0.3)
        self.assertEqual(status, 124)
        self.assertIn("ready", result["stdout"])
        self.assert_terminated(int(result["stdout"].splitlines()[0]))

    def test_normal_completion_cleans_detached_output_descendant(self):
        code = """import subprocess,sys
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(p.pid,flush=True)
"""
        result, status = run_command(python(code), timeout=2)
        self.assertEqual(status, 0)
        self.assertFalse(result["timed_out"])
        self.assert_terminated(int(result["stdout"].strip()))

    def check_interrupt(self, sent_signal):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "pid"
            child_code = "import os,time,pathlib;pathlib.Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid()));time.sleep(60)"
            wrapper_code = ("import json,sys;from agent_tools.runner import run_command;"
                            "result,status=run_command(" + repr(python(child_code)) + ");"
                            "print(json.dumps(result));sys.exit(status)")
            wrapper = subprocess.Popen(python(wrapper_code), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 3
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())
                child_pid = int(pid_path.read_text())
                wrapper.send_signal(sent_signal)
                stdout, stderr = wrapper.communicate(timeout=3)
                self.assertEqual(wrapper.returncode, 130, stderr.decode())
                result = json.loads(stdout)
                self.assertEqual(result["status"], "interrupted")
                self.assertFalse(result["timed_out"])
                self.assert_terminated(child_pid)
            finally:
                if wrapper.poll() is None:
                    wrapper.kill()
                    wrapper.wait()

    def test_interrupt_cleans_child_group_and_returns_130(self):
        self.check_interrupt(signal.SIGINT)

    def test_sigterm_cleans_child_group_and_returns_130(self):
        self.check_interrupt(signal.SIGTERM)

    def test_call_restores_signal_handlers(self):
        before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
        run_command(python("pass"))
        self.assertEqual(before, (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)))

    def test_worker_thread_does_not_try_to_install_signal_handlers(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            result, status = executor.submit(run_command, python("print('thread')")).result(timeout=3)
        self.assertEqual(status, 0)
        self.assertEqual(result["stdout"], "thread\n")

    def test_invalid_inputs_do_not_launch(self):
        cases = [([], {}), ([""], {}), (["bad\0value"], {}), ("echo", {}), ([1], {}),
                 (python("pass"), {"timeout": 0}), (python("pass"), {"timeout": -1}),
                 (python("pass"), {"timeout": float("nan")}),
                 (python("pass"), {"timeout": float("inf")}),
                 (python("pass"), {"timeout": 86401}), (python("pass"), {"timeout": True}),
                 (python("pass"), {"max_output_bytes": 0}),
                 (python("pass"), {"max_output_bytes": 1048577}),
                 (python("pass"), {"max_output_bytes": 1.5}),
                 (python("pass"), {"max_output_bytes": True}),
                 (python("pass"), {"cwd": "/path/that/is/not/a/directory"}),
                 (python("pass"), {"cwd": "bad\0path"})]
        for argv, options in cases:
            with self.subTest(argv=argv, options=options):
                result, status = run_command(argv, **options)
                self.assertEqual(status, 2)
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["exit_code"])
                self.assertEqual(result["stdout"], "")
                self.assertEqual(result["stderr"], "")
                self.assertIn("error_code", result)

    def test_missing_executable_is_127_without_sensitive_echo(self):
        marker = "/nonexistent/SECRET_COMMAND_SENTINEL"
        result, status = run_command([marker, "SECRET_ARGUMENT_SENTINEL"])
        self.assertEqual(status, 127)
        self.assertEqual(result["error_code"], "executable_not_found")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_valid_cwd_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            result, status = run_command(python("import os;print(os.getcwd())"), cwd=directory)
            self.assertEqual(status, 0)
            self.assertEqual(result["stdout"].strip(), directory)


if __name__ == "__main__":
    unittest.main()
