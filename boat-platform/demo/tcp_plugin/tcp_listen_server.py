"""Demo: TCP server that accepts connections and prints received data.

Usage:
    sudo python3 demo/tcp_plugin/tcp_listen_server.py <iface> <bind_ip> <port>

Examples:
    sudo python3 demo/tcp_plugin/tcp_listen_server.py eth0 0.0.0.0 8080
    sudo python3 demo/tcp_plugin/tcp_listen_server.py enx28107b9f2017 120.120.120.1 9999
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage:")
        print(f"  {sys.argv[0]} <iface> <bind_ip> <port>")
        print()
        print("Examples:")
        print(f"  sudo {sys.argv[0]} eth0 0.0.0.0 8080")
        print(f"  sudo {sys.argv[0]} enx28107b9f2017 120.120.120.1 9999")
        sys.exit(1)

    iface = sys.argv[1]
    bind_ip = sys.argv[2]
    bind_port = int(sys.argv[3])

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    # Block kernel RST for our port (raw socket bypasses kernel TCP stack)
    ipt_rule = f"OUTPUT -p tcp --tcp-flags RST RST --sport {bind_port} -j DROP"
    subprocess.run(["iptables", "-C"] + ipt_rule.split(),
                   capture_output=True)
    if subprocess.run(["iptables", "-C"] + ipt_rule.split(),
                      capture_output=True).returncode != 0:
        subprocess.run(["iptables", "-A"] + ipt_rule.split(), check=True)
        print(f"[SRV] Added iptables rule: {ipt_rule}", flush=True)
    def cleanup():
        subprocess.run(["iptables", "-D"] + ipt_rule.split(),
                       capture_output=True)

    stop = threading.Event()

    # Per-connection data tracking
    conn_data: dict[int, bytearray] = {}

    def on_data(cid: int, data: bytes) -> None:
        if cid not in conn_data:
            conn_data[cid] = bytearray()
        conn_data[cid].extend(data)
        print(f"[SRV] conn={cid}  {len(data)} bytes: {data.hex()}", flush=True)

    def on_event(cid: int, event: int) -> None:
        if event == 0:  # TCP_EVENT_CONNECTED
            print(f"[SRV] conn={cid} ACCEPTED", flush=True)
        elif event == 1:  # TCP_EVENT_CLOSED
            total = len(conn_data.pop(cid, bytearray()))
            print(f"[SRV] conn={cid} CLOSED (received {total} bytes)", flush=True)
        elif event == 4:  # TCP_EVENT_ERROR
            print(f"[SRV] conn={cid} ERROR", flush=True)

    lid = tcp.listen(bind_ip, bind_port, on_data=on_data, on_event=on_event)
    print(f"[SRV] Listening on {bind_ip}:{bind_port} (iface={iface}, lid={lid})", flush=True)
    print("[SRV] Press Ctrl+C to stop", flush=True)

    # Run until SIGINT
    def on_sigint(s, f):
        stop.set()
    signal.signal(signal.SIGINT, on_sigint)
    while not stop.is_set():
        time.sleep(0.5)

    cleanup()
    print("[SRV] Stopped")


if __name__ == "__main__":
    main()
