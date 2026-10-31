"""Bounded E2E subprocess capture with best-effort process-tree containment.

POSIX children start a new session; descendants deliberately creating another
session/process group can escape. Windows uses a non-inheritable Job Object,
but Popen starts the child before assignment, so very early descendants can
escape that race. Assignment failure is fatal (including incompatible outer
jobs); no uncontained fallback is used. This is not a security sandbox.

Only direct children can be reaped here. Process creation itself and native
containment API calls are not interruptible by the execution timeout.
"""

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time

_DRAIN_TIMEOUT = 2
_REAP_TIMEOUT = 2
_POLL_INTERVAL = 0.01
_READ_SIZE = 65536


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJob:
    def __init__(self):
        import msvcrt

        self._get_osfhandle = msvcrt.get_osfhandle
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
            "SetInformationJobObject": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            "TerminateJobObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
            "PeekNamedPipe": (
                [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p],
                ctypes.c_int,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._api, name)
            function.argtypes = arguments
            function.restype = result
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        # CPython exposes the process handle, but not the initial thread handle;
        # using CREATE_SUSPENDED would leave no supported way to resume it.
        if not self._api.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if self._handle is not None and not self._api.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self._handle is not None:
            if not self._api.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None

    def read_available(self, pipe):
        available = ctypes.c_uint32()
        handle = self._get_osfhandle(pipe.fileno())
        if not self._api.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232, 233):  # Broken, closing, or disconnected pipe.
                return b""
            raise ctypes.WinError(error)
        if not available.value:
            return None
        return os.read(pipe.fileno(), min(available.value, _READ_SIZE))


def _kill_process_group_members(group_id):
    executable = shutil.which("ps")
    if executable is None:
        raise FileNotFoundError("ps is required to enumerate a denied process group")
    result = subprocess.run(
        [executable, "-axo", "pid=,pgid="],
        capture_output=True,
        text=True,
        timeout=_REAP_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise ChildProcessError("unable to enumerate denied E2E process group")
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            member_pid, member_group = map(int, fields)
        except ValueError:
            continue
        if member_group != group_id or member_pid == os.getpid():
            continue
        try:
            os.kill(member_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _terminate(process, job):
    try:
        if job is not None:
            job.terminate()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                _kill_process_group_members(process.pid)
    finally:
        # Also handles failure to assign the Windows child to its job.
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass


def _capture(process, job, timeout):
    pipes = {0: process.stdout, 1: process.stderr}
    output = [bytearray(), bytearray()]
    if job is None:
        for pipe in pipes.values():
            os.set_blocking(pipe.fileno(), False)
    deadline = None if timeout is None else time.monotonic() + timeout
    drain_deadline = None
    timed_out = False
    while True:
        now = time.monotonic()
        if drain_deadline is None:
            if process.poll() is not None:
                _terminate(process, job)
                drain_deadline = time.monotonic() + _DRAIN_TIMEOUT
            elif deadline is not None and now >= deadline:
                timed_out = True
                _terminate(process, job)
                drain_deadline = time.monotonic() + _DRAIN_TIMEOUT
        progressed = False
        for index, pipe in list(pipes.items()):
            if job is not None:
                chunk = job.read_available(pipe)
            else:
                try:
                    chunk = os.read(pipe.fileno(), _READ_SIZE)
                except BlockingIOError:
                    chunk = None
            if chunk is None:
                continue
            progressed = True
            if chunk:
                output[index].extend(chunk)
            else:
                del pipes[index]
        if not pipes and process.poll() is not None:
            break
        if drain_deadline is not None and time.monotonic() >= drain_deadline:
            if not timed_out:
                raise RuntimeError("E2E process pipes or child did not stop after tree termination")
            break
        if not progressed:
            time.sleep(_POLL_INTERVAL)
    stdout, stderr = (bytes(buffer) for buffer in output)
    if timed_out:
        # Match subprocess's convention: TimeoutExpired retains byte buffers.
        raise subprocess.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr)
    return stdout, stderr


def _cleanup(process, job):
    failure = None
    try:
        _terminate(process, job)
    except OSError as error:
        failure = error
    if job is not None:
        try:
            job.close()
        except OSError as error:
            failure = failure or error
    if process is not None:
        try:
            process.wait(timeout=_REAP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            failure = failure or error
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError as error:
                    failure = failure or error
        if os.name == "nt" and process.returncode is not None:
            try:
                process._handle.Close()
            except OSError as error:
                failure = failure or error
    if failure is not None:
        raise RuntimeError("E2E process cleanup failed") from failure


def run_process(args, *, cwd, env, timeout=30):
    """Run with DEVNULL stdin and UTF-8 captured output (invalid bytes replaced).

    Terminate descendants as soon as the direct child exits, even if they hold
    capture pipes open, and always terminate the tree again in finally. Pipe
    draining and direct-child reaping each have a separate two-second bound.
    On timeout, terminate the tree, drain available output, and re-raise
    TimeoutExpired with byte stdout/stderr, as in subprocess.run(). Windows
    pipe polling avoids communicate() reader threads and their close deadlocks.

    Windows requires CPython's Popen handle and usable Job Object APIs. No
    process-tree escape resistance is promised; see the module limitations.
    With timeout=None only cleanup, not execution, has a time bound.
    """
    process = None
    job = _WindowsJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
        )
        if job is not None:
            job.assign(process)
        stdout, stderr = _capture(process, job, timeout)
        return subprocess.CompletedProcess(
            args, process.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
        )
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            _cleanup(process, job)
        except RuntimeError:
            if not active_error:
                raise
