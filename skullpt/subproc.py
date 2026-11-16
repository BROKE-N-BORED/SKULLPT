"""Cancellable subprocess runs with live output (v1.9, N8/N9).

Before v1.9 the shell ran Blender and sd-cli with subprocess.run and
capture_output: the whole run was a black box until it finished, and
cancelling only set a flag the run checked when the subprocess came
back - a five-minute render could not be stopped at all. This module
runs the child in its own process group, streams its output line by
line, and kills the group on cancel or timeout, so the Bench can
show live progress and the user can actually stop a run.

`run_cancellable` returns (returncode, output) with stdout and
stderr merged, in order - the callers that used to concatenate
proc.stdout and proc.stderr get the same text. It raises
governor.Cancelled when the cancel event is set (the child is
killed first) and subprocess.TimeoutExpired on timeout (the child
is killed, and the partial output rides on the exception).
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time

from . import governor


def run_cancellable(cmd, timeout: float, cancel=None, on_line=None,
                    env=None) -> tuple[int, str]:
    """Run `cmd` (a list), streaming merged stdout/stderr lines.

    `timeout` is seconds; `cancel`, when given, is a threading.Event
    checked while the child runs; `on_line`, when given, is called
    with each output line (without its trailing newline) as it
    arrives. Returns (returncode, output text). Raises
    governor.Cancelled on cancel and subprocess.TimeoutExpired on
    timeout, in both cases after killing the child's whole process
    group - no orphaned Blender or sd-cli keeps running after the
    run that started it is gone.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1, env=env,
        start_new_session=True)
    lines: "queue.Queue[str | None]" = queue.Queue()

    def _reader() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        except (ValueError, OSError):
            # The pipe closed under us (kill raced the read) - the
            # main loop finds out through the process exit.
            pass
        finally:
            lines.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    def _kill_group() -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    def _kill_and_wait() -> None:
        _kill_group()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # SIGKILL cannot be ignored; the reaper catches up.

    def _close_pipe() -> None:
        # Close the pipe we opened: leaving it to the garbage
        # collector prints a ResourceWarning under -W default. The
        # reader thread treats a closed pipe as EOF (ValueError is
        # one of the exceptions it catches).
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass

    collected: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                _kill_and_wait()
                raise governor.Cancelled("run cancelled")
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    _kill_and_wait()
                    raise subprocess.TimeoutExpired(
                        cmd, timeout, output="".join(collected))
                if proc.poll() is not None:
                    # Exited without closing the pipe cleanly; the
                    # reader will deliver the sentinel shortly.
                    continue
                continue
            if line is None:
                break
            collected.append(line)
            if on_line is not None:
                try:
                    on_line(line.rstrip("\n"))
                except Exception:
                    # A failing progress handler must not strand the
                    # child: kill it, then let the error surface.
                    _kill_and_wait()
                    raise
            if time.monotonic() >= deadline:
                _kill_and_wait()
                raise subprocess.TimeoutExpired(
                    cmd, timeout, output="".join(collected))
    except BaseException:
        if proc.poll() is None:
            _kill_and_wait()
        _close_pipe()
        raise
    # The pipe closed (EOF), which normally means the child exited;
    # a child that closes stdout and lingers still gets killed on
    # the deadline rather than hanging the run forever.
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_and_wait()
        _close_pipe()
        raise subprocess.TimeoutExpired(
            cmd, timeout, output="".join(collected))
    _close_pipe()
    return proc.returncode, "".join(collected)
