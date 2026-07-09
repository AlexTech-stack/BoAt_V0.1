"""TCP relay: listens on a socket, forwards received payloads to another server.

Usage:
    sudo python3 demo/tcp_plugin/tcp_relay.py <iface> <listen_ip> <listen_port> <relay_dst_ip> <relay_dst_port>

Example:
    sudo python3 demo/tcp_plugin/tcp_relay.py enx28107b9f2017 120.120.120.1 9999 120.120.120.3 1234
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
    if len(sys.argv) < 6:
        print("Usage:")
        print(f"  {sys.argv[0]} <iface> <listen_ip> <listen_port> <relay_dst_ip> <relay_dst_port>")
        print()
        print("Examples:")
        print(f"  sudo {sys.argv[0]} eth0 0.0.0.0 9999 192.168.1.100 8080")
        print(f"  sudo {sys.argv[0]} enx28107b9f2017 120.120.120.1 9999 120.120.120.3 1234")
        sys.exit(1)

    iface = sys.argv[1]
    listen_ip = sys.argv[2]
    listen_port = int(sys.argv[3])
    relay_dst_ip = sys.argv[4]
    relay_dst_port = int(sys.argv[5])

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    # Block kernel RST for our listen port (raw socket bypasses kernel TCP stack)
    ipt_rule = f"OUTPUT -p tcp --tcp-flags RST RST --sport {listen_port} -j DROP"
    subprocess.run(["iptables", "-C"] + ipt_rule.split(), capture_output=True)
    if subprocess.run(["iptables", "-C"] + ipt_rule.split(), capture_output=True).returncode != 0:
        subprocess.run(["iptables", "-A"] + ipt_rule.split(), check=True)
        print(f"[RELAY] Added iptables rule: {ipt_rule}", flush=True)

    def cleanup():
        subprocess.run(["iptables", "-D"] + ipt_rule.split(), capture_output=True)

    stop = threading.Event()
    in_to_out: dict[int, int] = {}

    def on_data(cid: int, data: bytes) -> None:
        out_cid = in_to_out.get(cid)
        if out_cid is not None:
            ret = tcp.send(out_cid, data)
            if ret > 0:
                print(f"[RELAY] {cid} -> {out_cid}  forwarded {ret} bytes: {data.hex()}", flush=True)
            else:
                print(f"[RELAY] {cid} -> {out_cid}  send returned {ret}", flush=True)
        else:
            print(f"[RELAY] {cid}  no outbound connection, dropping {len(data)} bytes", flush=True)

    def on_event(cid: int, event: int) -> None:
        if event == 0:  # TCP_EVENT_CONNECTED
            out_cid = tcp.connect(listen_ip, 0, relay_dst_ip, relay_dst_port)
            in_to_out[cid] = out_cid
            print(f"[RELAY] inbound conn={cid} ACCEPTED → outbound conn={out_cid}  {listen_ip}:* -> {relay_dst_ip}:{relay_dst_port}", flush=True)

        elif event == 1:  # TCP_EVENT_CLOSED
            out_cid = in_to_out.pop(cid, None)
            if out_cid is not None:
                tcp.close(out_cid)
                print(f"[RELAY] inbound conn={cid} CLOSED, closed outbound conn={out_cid}", flush=True)
            else:
                print(f"[RELAY] inbound conn={cid} CLOSED", flush=True)

        elif event == 4:  # TCP_EVENT_ERROR
            out_cid = in_to_out.pop(cid, None)
            if out_cid is not None:
                tcp.abort(out_cid)
                print(f"[RELAY] inbound conn={cid} ERROR, aborted outbound conn={out_cid}", flush=True)
            else:
                print(f"[RELAY] inbound conn={cid} ERROR", flush=True)

    lid = tcp.listen(listen_ip, listen_port, on_data=on_data, on_event=on_event)
    print(f"[RELAY] Listening on {listen_ip}:{listen_port} (iface={iface}, lid={lid})", flush=True)
    print(f"[RELAY] Relaying to {relay_dst_ip}:{relay_dst_port}", flush=True)
    print("[RELAY] Press Ctrl+C to stop", flush=True)

    def on_sigint(s, f):
        stop.set()
    signal.signal(signal.SIGINT, on_sigint)

    while not stop.is_set():
        time.sleep(0.5)

    cleanup()
    print("[RELAY] Stopped")


if __name__ == "__main__":
    main()
