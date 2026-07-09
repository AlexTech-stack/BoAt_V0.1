from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from boat.test.bus import TestCanBus, TestEthBus
from boat.test.config import BusConfig
from boat.test.exceptions import TestTimeoutError


def _make_bus_config(name: str = "can1", type_: str = "virtual", interface: str = "vcan0") -> BusConfig:
    return BusConfig(logical_name=name, type=type_, interface=interface)


class TestFrameMatching:
    def test_match_can_id(self) -> None:
        frame = MagicMock()
        frame.can_id = 0x300
        assert TestCanBus._matches(frame, can_id=0x300, data=None, mask=None)
        assert not TestCanBus._matches(frame, can_id=0x100, data=None, mask=None)

    def test_match_data_exact(self) -> None:
        frame = MagicMock()
        frame.can_id = 0x300

        class FakeBytes(bytes):
            pass

        frame.data = b'\x01\xF4'
        assert TestCanBus._matches(frame, can_id=0x300, data=b'\x01\xF4', mask=None)
        assert not TestCanBus._matches(frame, can_id=0x300, data=b'\x01\x00', mask=None)

    def test_match_data_with_mask(self) -> None:
        frame = MagicMock()
        frame.can_id = 0x300
        frame.data = b'\xFF\xFF'
        assert TestCanBus._matches(frame, can_id=0x300, data=b'\xAB\xCD', mask=b'\x00\x00')

    def test_match_any(self) -> None:
        frame = MagicMock()
        frame.can_id = 0x300
        frame.data = b'\x01'
        assert TestCanBus._matches(frame, can_id=None, data=None, mask=None)


class TestTestCanBus:
    def test_send_calls_rpc(self) -> None:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.accepted = True
        mock_client.can.SendCanFrame.return_value = resp

        bus = TestCanBus(mock_client, _make_bus_config())
        result = bus.send(0x100, b'\x01\x02')
        assert result is True
        mock_client.can.SendCanFrame.assert_called_once()

    def test_expect_returns_matching_frame(self) -> None:
        mock_client = MagicMock()
        frame1 = MagicMock()
        frame1.can_id = 0x100
        frame1.data = b'\x01'
        frame2 = MagicMock()
        frame2.can_id = 0x300
        frame2.data = b'\x02'

        mock_client.can.SubscribeCanFrames.return_value = iter([frame1, frame2])

        bus = TestCanBus(mock_client, _make_bus_config())
        result = bus.expect(can_id=0x300, timeout_ms=500)
        assert result.can_id == 0x300

    def test_expect_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.can.SubscribeCanFrames.return_value = iter([])

        bus = TestCanBus(mock_client, _make_bus_config())
        with pytest.raises(TestTimeoutError):
            bus.expect(can_id=0x999, timeout_ms=50)

    def test_send_raises_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.can.SendCanFrame.side_effect = RuntimeError("connection refused")

        bus = TestCanBus(mock_client, _make_bus_config())
        with pytest.raises(RuntimeError, match="CAN send failed"):
            bus.send(0x100, b'\x01')

    def test_properties(self) -> None:
        bus = TestCanBus(MagicMock(), _make_bus_config("mycan", "physical", "can0"))
        assert bus.name == "mycan"
        assert bus.interface == "can0"


class TestTestEthBus:
    def test_send_calls_rpc(self) -> None:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.accepted = True
        mock_client.ethernet.SendFrame.return_value = resp

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        result = bus.send(dst_mac=b'\x00' * 6, ethertype=0x88B5, payload=b'test')
        assert result is True
        mock_client.ethernet.SendFrame.assert_called_once()

    def test_expect_returns_matching_ethertype(self) -> None:
        mock_client = MagicMock()
        frame = MagicMock()
        frame.ethertype = 0x88B5
        mock_client.ethernet.SubscribeFrames.return_value = iter([frame])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        result = bus.expect(ethertype=0x88B5, timeout_ms=500)
        assert result.ethertype == 0x88B5

    def test_expect_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.ethernet.SubscribeFrames.return_value = iter([])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        with pytest.raises(TestTimeoutError):
            bus.expect(ethertype=0x0800, timeout_ms=50)
