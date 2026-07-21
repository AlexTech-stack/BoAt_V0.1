#!/usr/bin/env python3
"""Send 1000 CAN messages with 1ms delay and random 1-8 byte payload."""

import argparse
import random
import time

from boat.frame_node import FrameNode


def main():
    parser = argparse.ArgumentParser(description="Burst CAN frame sender")
    parser.add_argument("--count", type=int, default=1000, help="Number of frames")
    parser.add_argument("--delay", type=float, default=0.001, help="Inter-frame delay (s)")
    parser.add_argument("--iface", default="vcan0", help="CAN interface")
    parser.add_argument("--can-id", type=lambda x: int(x, 0), default=0x100, help="CAN ID")
    parser.add_argument("--address", default="localhost:50051", help="Gateway gRPC address")
    args = parser.parse_args()

    node = FrameNode(args.address)

    sent = 0
    failed = 0
    start = time.perf_counter()

    for i in range(args.count):
        length = random.randint(1, 8)
        data = bytes(random.getrandbits(8) for _ in range(length))
        ok = node.send_can(args.iface, args.can_id, data)
        if ok:
            sent += 1
        else:
            failed += 1
        if i < args.count - 1:
            time.sleep(args.delay)

    elapsed = time.perf_counter() - start
    rate = sent / elapsed if elapsed > 0 else 0
    print(f"Sent {sent}/{args.count} frames on {args.iface} (CAN ID {hex(args.can_id)}) "
          f"in {elapsed:.3f}s ({rate:.1f} msg/s)"
          + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
