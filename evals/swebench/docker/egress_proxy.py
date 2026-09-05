"""A CONNECT proxy that allows exactly one host:port and refuses everything else.

The agent container is placed on an internal Docker network with no route off
the machine, and this is the only thing on that network that can reach outside.
The model API stays reachable; the public repository holding the fix for the
instance under test does not. Detection after the fact is not enough -- looking
the answer up is the shortest path to a passing grade, so it has to be
impossible rather than merely visible.
"""
import os
import select
import socket
import socketserver
import sys

ALLOWED = {
    tuple(entry.rsplit(":", 1))
    for entry in os.environ.get("ALLOW", "").split(",")
    if entry.strip()
}
ALLOWED = {(h, int(p)) for h, p in ALLOWED}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > 16384:
                    return
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                # Plain HTTP is refused outright: it would let a request reach any
                # host through this proxy without ever naming it in a CONNECT.
                self.request.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                print(f"REFUSED non-CONNECT: {line}", flush=True)
                return
            host, _, port = parts[1].rpartition(":")
            target = (host, int(port or 443))
            if target not in ALLOWED:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                print(f"REFUSED {target[0]}:{target[1]}", flush=True)
                return
            upstream = socket.create_connection(target, timeout=30)
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            print(f"ALLOWED {target[0]}:{target[1]}", flush=True)
            self._pump(self.request, upstream)
        except Exception as exc:                                  # noqa: BLE001
            print(f"error: {type(exc).__name__}: {exc}", flush=True)

    @staticmethod
    def _pump(a, b):
        socks = [a, b]
        try:
            while True:
                readable, _, errored = select.select(socks, [], socks, 60)
                if errored or not readable:
                    return
                for s in readable:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    if not ALLOWED:
        sys.exit("ALLOW is empty: refusing to start a proxy that permits nothing")
    print(f"egress allowlist: {sorted(ALLOWED)}", flush=True)
    Server(("0.0.0.0", 8888), Handler).serve_forever()
