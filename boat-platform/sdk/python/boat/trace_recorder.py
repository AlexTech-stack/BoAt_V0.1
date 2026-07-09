"""Python SDK client for the BoAt Trace Recorder daemon.

The recorder daemon (demo/recorder.py) must be running on the same or a
reachable host.  This class wraps its REST API so Python nodes and scripts
can start and stop recording sessions programmatically.

Quick example::

    from boat.trace_recorder import TraceRecorder

    rec = TraceRecorder()
    session = rec.start(
        buses=["vcan0", "vcan1"],
        fmt="asc",
        include_signals=True,
        name="my_run",
    )
    print("Recording to:", session["files"])

    # ... do stuff ...

    rec.stop(session["session_id"])
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class TraceRecorderError(RuntimeError):
    pass


class TraceRecorder:
    """Client for the BoAt recorder daemon REST API.

    Args:
        recorder_url: Base URL of the recorder daemon.
                      Defaults to ``http://localhost:8083``.
        gateway:      gRPC address of the BoAt gateway, forwarded to the
                      recorder so it knows which gateway to subscribe to.
    """

    def __init__(
        self,
        recorder_url: str = "http://localhost:8083",
        gateway: str = "localhost:50051",
    ) -> None:
        self.recorder_url = recorder_url.rstrip("/")
        self.gateway      = gateway

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(
        self,
        buses:           Optional[List[str]] = None,
        eth_ifaces:      Optional[List[str]] = None,
        include_signals: bool  = True,
        fmt:             str   = "asc",
        output_dir:      str   = "traces",
        name:            str   = "",
    ) -> Dict[str, Any]:
        """Start a new recording session.

        Args:
            buses:           CAN interface names to record (e.g. ``["vcan0"]``).
                             An empty list records *all* registered CAN buses.
            eth_ifaces:      Ethernet interface names to record (PCAP only).
            include_signals: Whether to record BoAt bus signals to a ``.jsonl`` sidecar.
            fmt:             Output format — ``"asc"``, ``"blf"``, or ``"pcap"``.
            output_dir:      Directory where trace files are written.
            name:            Optional human-readable label for the session.

        Returns:
            Session dict as returned by the recorder (includes ``session_id`` and
            ``files`` list).

        Raises:
            TraceRecorderError: If the recorder daemon is unreachable or returns
            an error.
        """
        body = {
            "gateway":         self.gateway,
            "name":            name,
            "format":          fmt,
            "buses":           buses or [],
            "eth_ifaces":      eth_ifaces or [],
            "include_signals": include_signals,
            "output_dir":      output_dir,
        }
        return self._post("/api/sessions", body)

    def stop(self, session_id: str) -> Dict[str, Any]:
        """Stop a running recording session.

        Args:
            session_id: The ``session_id`` returned by :meth:`start`.

        Returns:
            Final session dict with frame counts and file info.
        """
        return self._delete(f"/api/sessions/{urllib.parse.quote(session_id)}")

    def stop_all(self) -> Dict[str, Any]:
        """Stop all currently running sessions."""
        return self._delete("/api/sessions")

    def sessions(self) -> List[Dict[str, Any]]:
        """Return a list of all sessions (active and completed)."""
        return self._get("/api/sessions")

    def status(self, session_id: str) -> Dict[str, Any]:
        """Return the current status of a specific session."""
        return self._get(f"/api/sessions/{urllib.parse.quote(session_id)}")

    # ── HTTP helpers ───────────────────────────────────────────────────────────

    def _get(self, path: str) -> Any:
        req = urllib.request.Request(self.recorder_url + path)
        return self._send(req)

    def _post(self, path: str, body: dict) -> Any:
        data = json.dumps(body).encode()
        req  = urllib.request.Request(
            self.recorder_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(req)

    def _delete(self, path: str) -> Any:
        req = urllib.request.Request(
            self.recorder_url + path, method="DELETE"
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail", str(e))
            except Exception:
                detail = str(e)
            raise TraceRecorderError(f"Recorder error {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise TraceRecorderError(
                f"Cannot reach recorder at {self.recorder_url}: {e.reason}\n"
                "Make sure demo/recorder.py is running."
            ) from e
