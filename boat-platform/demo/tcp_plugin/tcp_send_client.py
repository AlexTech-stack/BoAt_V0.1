"""Send a TCP payload via the BoAt TCP plugin.

Usage:
    sudo python3 demo/tcp_plugin/tcp_send_client.py <iface> <src_ip> <src_port> <dst_ip> <dst_port> [payload_hex]

Examples:
    sudo python3 demo/tcp_plugin/tcp_send_client.py eth0 10.0.0.1 0 192.168.1.100 8080
    sudo python3 demo/tcp_plugin/tcp_send_client.py eth0 10.0.0.1 50000 192.168.1.100 443 AABBCCDDEEFF
"""
from __future__ import annotations

import sys
import os
import random
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 6:
        print("Usage:")
        print(f"  {sys.argv[0]} <iface> <src_ip> <src_port> <dst_ip> <dst_port> [payload_hex]")
        print()
        print("  src_port = 0 for dynamic   dst_ip  = target IP")
        print("  iface    = e.g. eth0        dst_port = target port")
        print("  src_ip   = source IP        payload_hex = hex string (optional)")
        print()
        print("Examples:")
        print(f"  sudo {sys.argv[0]} eth0 10.0.0.1 0 192.168.1.100 8080")
        print(f"  sudo {sys.argv[0]} enx28107b9f2017 120.120.120.1 0 120.120.120.3 1234 AABBCCDDEEFF")
        sys.exit(1)

    iface = sys.argv[1]
    src_ip = sys.argv[2]
    src_port_str = sys.argv[3]
    dst_ip = sys.argv[4]
    dst_port = int(sys.argv[5])
    payload_hex = sys.argv[6] if len(sys.argv) > 6 else "AABBCCDDEEFF"
    payload = bytes.fromhex(payload_hex)

    # Resolve source port
    if src_port_str == "0" or src_port_str.lower() == "dynamic":
        src_port = random.randint(40000, 60000)
    else:
        src_port = int(src_port_str)

    # TCP plugin path
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    # Start plugin with the given interface
    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    connected = threading.Event()

    def on_event(cid: int, event: int) -> None:
        if event == 0:
            print(f"[+] Connected to {dst_ip}:{dst_port}")
            connected.set()
        elif event == 4:
            print("[-] Connection error/timeout")

    cid = tcp.connect(src_ip, src_port, dst_ip, dst_port, on_event=on_event)
    print(f"[~] {iface}  {src_ip}:{src_port} → {dst_ip}:{dst_port} ...")

    if connected.wait(timeout=5):
        time.sleep(0.3)
        ret = tcp.send(cid, payload)
        if ret > 0:
            print(f"[+] Sent {ret} bytes: {payload.hex()}")
        else:
            print(f"[-] Send returned {ret}")
        time.sleep(0.5)
        tcp.close(cid)
        time.sleep(0.3)
        print("[+] Connection closed")
    else:
        print("[-] Connection timed out")


if __name__ == "__main__":
    main()
