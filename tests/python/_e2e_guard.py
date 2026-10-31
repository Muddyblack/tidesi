"""Audit-based accidental-I/O guard for E2E child interpreters.

A fail-closed sitecustomize importer must call install() in every interpreter.
Trusted fake scripts, including Windows batch wrappers, may run native code
and unaudited native descendants. This is not an OS sandbox and does not
protect against malicious Python code, native extensions, inherited sockets,
or changes to the guard/importer files. Audit hooks cannot be removed.
"""

import ipaddress
import os
import shutil
import socket
import subprocess
import sys


class E2EGuardViolation(RuntimeError):
    """An operation was denied by the E2E audit policy."""


_ISOLATION_ENV = frozenset(
    (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "__PYVENV_LAUNCHER__",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "TIDESI_CONFIG",
        "TIDESI_CACHE_DIR",
        "TEMP",
        "TMP",
        "TMPDIR",
    )
)
_SOCKET_EVENTS = {
    "socket.bind": "non-loopback socket bind",
    "socket.connect": "non-loopback socket connect",
    "socket.sendto": "non-loopback socket sendto",
    "socket.sendmsg": "non-loopback socket sendmsg",
}
_DNS_EVENTS = {
    "socket.getaddrinfo",
    "socket.gethostbyname",  # Also emitted by gethostbyname_ex().
    "socket.gethostbyaddr",
    "socket.getnameinfo",
}


def _loopback(host):
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeError:
            return False
    if not isinstance(host, str):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.casefold() in ("localhost", "localhost.")
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def _local_socket(sock, address):
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return False
    if address is None:  # sendmsg() may use an already-connected socket.
        try:
            address = sock.getpeername()
        except OSError:
            return False
    return isinstance(address, tuple) and bool(address) and _loopback(address[0])


def _environment_key(key):
    key = os.fsdecode(key)
    return key.upper() if os.name == "nt" else key


def _environment(env):
    result = {}
    for key, value in env.items():
        key = _environment_key(key)
        value = os.fsdecode(value)
        if key in result and result[key] != value:
            raise ValueError("ambiguous environment")
        result[key] = value
    return result


def _critical_env(key):
    return key in _ISOLATION_ENV or key.startswith(("PYTHON", "TIDESI_E2E_", "LD_", "DYLD_"))


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.fsdecode(os.fspath(path))))


def _executable_path(executable, cwd, env):
    executable = os.fsdecode(os.fspath(executable))
    if os.path.isabs(executable):
        return _canonical(executable)
    # Windows CreateProcess searches independently of the supplied env/cwd;
    # require an absolute executable instead of guessing its actual target.
    if os.name == "nt":
        return None
    cwd = os.path.abspath(os.fsdecode(cwd)) if cwd is not None else os.getcwd()
    if os.path.dirname(executable):
        return _canonical(os.path.join(cwd, executable))
    search_path = os.pathsep.join(os.path.join(cwd, entry) for entry in os.get_exec_path(env))
    resolved = shutil.which(executable, path=search_path)
    return _canonical(resolved) if resolved is not None else None


def _windows_argv(command):
    """Accept only round-trippable list2cmdline output, not shell syntax."""
    argv = []
    index = 0
    while index < len(command):
        while index < len(command) and command[index] in " \t":
            index += 1
        if index == len(command):
            break
        argument = []
        quoted = False
        while index < len(command):
            slashes = 0
            while index < len(command) and command[index] == "\\":
                slashes += 1
                index += 1
            if index < len(command) and command[index] == '"':
                argument.append("\\" * (slashes // 2))
                if slashes % 2:
                    argument.append('"')
                else:
                    quoted = not quoted
                index += 1
            else:
                argument.append("\\" * slashes)
                if index == len(command) or (not quoted and command[index] in " \t"):
                    break
                argument.append(command[index])
                index += 1
        if quoted:
            return None
        argv.append("".join(argument))
    if not argv or not argv[0] or '"' in argv[0] or subprocess.list2cmdline(argv) != command:
        return None
    return argv


def _python_flags_safe(argv):
    """Allow minimal startup flags, stopping at the script/module/code boundary."""
    for argument in argv[1:]:
        argument = os.fsdecode(os.fspath(argument))
        if argument in ("-", "--") or not argument.startswith("-"):
            return True
        for option in argument[1:]:
            if option in "cm":
                return True
            if option not in "BbOqusv":
                return False
    return True


def install(violation_path, bin_dir=None):
    """Install a permanent guard, logging only fixed reason strings on denial.

    Allow loopback INET sockets and AF_UNIX, the current Python executable,
    and executables whose real paths are inside the trusted bin_dir. Shells,
    executable file descriptors, and ambiguous Windows launches are denied.
    Windows requires absolute executable paths and canonical CRT command
    lines: .exe files, plus .cmd/.bat wrappers only inside the trusted fake
    bin. Trusted wrappers and their native descendants are not OS-sandboxed.
    Python startup flags are limited to -B/-b/-O/-q/-u/-s/-v, including
    combined forms. Other flags, especially -W/-X/-S/-I/-E, are denied;
    options after a script, -c, or -m are application arguments.

    Child environments must preserve every PYTHON*/TIDESI_E2E_*/LD_*/DYLD_*
    variable and the launch/isolation variables in _ISOLATION_ENV, including
    absence of a key. Critical os.putenv/os.unsetenv mutations are also denied
    before they change the native environment, even without os.environ updates.
    The caller supplies a writable violation path and a PYTHONPATH containing
    its fail-closed sitecustomize importer. A logging failure still denies the
    operation with E2EGuardViolation, but cannot leave a persistent record.
    """
    log_path = _canonical(violation_path)
    fake_root = _canonical(bin_dir) if bin_dir is not None else None
    interpreter = _canonical(sys.executable)
    original_env = _environment(os.environ)

    def deny(reason):
        try:
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (reason + "\n").encode("ascii"))
            finally:
                os.close(fd)
        except OSError:
            pass
        raise E2EGuardViolation(reason) from None

    def check_process(executable, argv, cwd, supplied_env):
        try:
            env = _environment(os.environ if supplied_env is None else supplied_env)
            keys = original_env.keys() | env.keys()
            if any(original_env.get(key) != env.get(key) for key in keys if _critical_env(key)):
                deny("subprocess guard environment changed")
            if os.name == "nt":
                if isinstance(argv, str):
                    argv = _windows_argv(argv)
                if not isinstance(argv, (list, tuple)) or not argv:
                    deny("ambiguous subprocess command line")
                if executable is None:
                    executable = argv[0]
            resolved = _executable_path(executable, cwd, env)
            is_python = resolved is not None and resolved == interpreter
            is_fake = False
            if resolved is not None and fake_root is not None:
                try:
                    is_fake = resolved != fake_root and os.path.commonpath((resolved, fake_root)) == fake_root
                except ValueError:  # Different Windows drives.
                    pass
            if os.name == "nt":
                extension = os.path.splitext(os.fsdecode(os.fspath(executable)))[1].lower()
                if extension != ".exe" and not (is_fake and extension in (".cmd", ".bat")):
                    deny("unsupported Windows executable")
            if not is_python and not is_fake:
                deny("untrusted subprocess executable")
            if is_python:
                if isinstance(argv, (str, bytes)):
                    argv = [argv]  # POSIX treats a string as a single argv[0].
                if not isinstance(argv, (list, tuple)) or not argv or not _python_flags_safe(argv):
                    deny("unsafe Python startup options")
        except (AttributeError, OSError, TypeError, ValueError):
            deny("unverifiable subprocess launch")

    def audit(event, args):
        if event in _SOCKET_EVENTS:
            if not _local_socket(args[0], args[1]):
                deny(_SOCKET_EVENTS[event])
        elif event in _DNS_EVENTS:
            address = args[0]
            if event == "socket.getnameinfo":
                address = address[0] if isinstance(address, tuple) and address else None
            if not _loopback(address):
                deny("non-loopback name resolution")
        elif event in ("os.putenv", "os.unsetenv"):
            try:
                key = _environment_key(args[0])
                if _critical_env(key):
                    value = os.fsdecode(args[1]) if event == "os.putenv" else None
                    if os.name == "nt" and value == "":
                        value = None  # Windows putenv with an empty value removes the key.
                    if original_env.get(key) != value:
                        deny("guard environment mutation")
            except (TypeError, ValueError):
                deny("unverifiable environment mutation")
        elif event == "subprocess.Popen":
            check_process(args[0], args[1], args[2], args[3])
        elif event in ("os.exec", "os.posix_spawn"):
            check_process(args[0], args[1], None, args[2])
        elif event == "os.spawn":
            check_process(args[1], args[2], None, args[3])
        elif event in ("os.system", "os.startfile", "os.startfile/2"):
            deny("shell or associated application launch")

    sys.addaudithook(audit)
