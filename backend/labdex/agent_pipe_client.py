"""ssh's ProxyCommand for Ansible through the agent: python3 -I pipe.py <socket> <token> <host>.

Standard library only: labdex copies this file into the run's private folder. It connects to the
run's Unix socket (agent_pipes.Run), says which host, and on "ok" moves bytes between ssh (stdin,
stdout) and the socket until either side closes. On "error …" it tells ssh why on stderr.
"""
import os
import socket
import sys
import threading


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: pipe.py <socket> <token> <host>", file=sys.stderr)
        return 2
    path, token, host = sys.argv[1:]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        s.sendall(f"{token} {host}\n".encode())
        answer = b""
        while not answer.endswith(b"\n") and len(answer) < 1024:
            b = s.recv(1)
            if not b:
                break
            answer += b
    except OSError as e:
        print(f"labdex: can't reach the run's pipe socket: {e}", file=sys.stderr)
        return 1
    answer = answer.decode("utf-8", "replace").strip()
    if answer != "ok":
        print(f"labdex: no pipe to {host}: {answer.removeprefix('error ') or 'closed'}", file=sys.stderr)
        return 1

    def up() -> None:
        try:
            while True:
                data = os.read(0, 65536)
                if not data:
                    break
                s.sendall(data)
        except OSError:
            pass
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=up, daemon=True).start()
    try:
        while True:
            data = s.recv(65536)
            if not data:
                break
            view = memoryview(data)
            while view:
                view = view[os.write(1, view):]
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
