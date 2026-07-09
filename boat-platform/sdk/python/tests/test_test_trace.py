from unittest.mock import MagicMock

from boat.test.harness import _TraceManager


class TestTraceManager:
    def test_start_sets_trace_id(self) -> None:
        mgr = _TraceManager(MagicMock())
        tid = mgr.start("test-123")
        assert tid == "test-123"
        assert mgr._trace_id == "test-123"

    def test_marker_calls_rpc(self) -> None:
        mock_client = MagicMock()
        mgr = _TraceManager(mock_client)
        mgr.start("trace-1")

        mgr.marker(step_id=1, step_name="Step 1")
        mock_client.trace.MarkStep.assert_called_once()
        call_args = mock_client.trace.MarkStep.call_args[0][0]
        assert call_args.trace_id == "trace-1"
        assert call_args.step_id == 1
        assert call_args.step_name == "Step 1"

    def test_marker_with_metadata(self) -> None:
        mock_client = MagicMock()
        mgr = _TraceManager(mock_client)
        mgr.start("trace-1")

        mgr.marker(step_id=2, step_name="Send", metadata={"bus": "can1", "id": "0x100"})
        call_args = mock_client.trace.MarkStep.call_args[0][0]
        assert call_args.metadata["bus"] == "can1"
        assert call_args.metadata["id"] == "0x100"

    def test_marker_noops_without_start(self) -> None:
        mock_client = MagicMock()
        mgr = _TraceManager(mock_client)
        mgr.marker(step_id=1, step_name="Noop")
        mock_client.trace.MarkStep.assert_not_called()

    def test_marker_silent_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.trace.MarkStep.side_effect = RuntimeError("RPC failed")
        mgr = _TraceManager(mock_client)
        mgr.start("trace-1")

        mgr.marker(step_id=1, step_name="Fail")
        mock_client.trace.MarkStep.assert_called_once()

    def test_stop_returns_empty_list(self) -> None:
        mgr = _TraceManager(MagicMock())
        mgr.start("trace-1")
        traces = mgr.stop()
        assert isinstance(traces, list)
        assert len(traces) == 0  # no recorder configured

    def test_stop_without_start(self) -> None:
        mgr = _TraceManager(MagicMock())
        traces = mgr.stop()
        assert isinstance(traces, list)
        assert len(traces) == 0
