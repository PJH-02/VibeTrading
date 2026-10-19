"""Test-copy sitecustomize: no credentials, external sockets, or non-Python child."""
import os
import sys

try:
    import ipaddress
    import json
    import socket
    import weakref

    _listeners = weakref.WeakSet()
    _receipt = os.environ["REFACTOR_GUARD_RECEIPT"]

    def _record(kind, **fields):
        with open(_receipt, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "kind": kind, **fields}) + "\n")

    def _loopback(host):
        try:
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            return host in ("localhost", b"localhost")

    def _audit(event, args):
        if event == "socket.bind":
            sock, address = args
            if isinstance(address, tuple) and _loopback(address[0]) and address[1] == 0:
                _listeners.add(sock)
        elif event == "socket.connect":
            sock, address = args
            allowed = False
            if isinstance(address, tuple) and _loopback(address[0]):
                for listener in list(_listeners):
                    try:
                        local = listener.getsockname()
                        if local[0] == address[0] and local[1] == address[1] and local[1] != 0:
                            allowed = True
                            break
                    except OSError:
                        pass
            if not allowed:
                _record("blocked_socket", address=str(address))
                raise PermissionError("Refactoring offline guard blocked a real network connection")
        elif event == "socket.getaddrinfo":
            if args[0] is not None and not _loopback(args[0]):
                _record("blocked_dns", host=str(args[0]))
                raise PermissionError("Refactoring offline guard blocked external DNS")
        elif event == "subprocess.Popen":
            executable = args[0]
            if executable is None:
                command = args[1]
                if isinstance(command, (list, tuple)):
                    executable = command[0]
                else:
                    command = os.fsdecode(command)
                    prefixes = (sys.executable + " ", '"' + sys.executable + '" ')
                    if any(os.path.normcase(command).startswith(os.path.normcase(prefix)) for prefix in prefixes):
                        executable = sys.executable
                    else:
                        _record("blocked_subprocess", executable="unrecognized command line")
                        raise PermissionError("Refactoring offline guard rejected an unknown executable")
            executable = os.fsdecode(executable)
            if os.path.normcase(os.path.abspath(executable)) != os.path.normcase(os.path.abspath(sys.executable)):
                _record("blocked_subprocess", executable=executable)
                raise PermissionError("Refactoring offline guard permits only the test Python executable")

    sys.addaudithook(_audit)
    sys._vibetrading_offline_guard = True
    _record("guard_loaded")
except BaseException:
    os.write(2, b"Refactoring offline guard failed to initialize\n")
    os._exit(97)
