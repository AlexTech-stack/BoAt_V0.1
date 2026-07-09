from boat.test.pdu import PduHelper, _unpack_intel, _unpack_motorola, unpack_message
from boat.message import Message
from boat.pdu_db import PduDatabase


DB_PATH = "./config/pdu_db_test.json"


class TestBitUnpacking:
    def test_unpack_intel_full_byte(self) -> None:
        data = bytes([0b10101010])
        val = _unpack_intel(data, start_bit=0, length=8)
        assert val == 0b10101010

    def test_unpack_intel_partial(self) -> None:
        data = bytes([0b11100111])
        # bits 2,3,4 = 1,0,0 → LSB at bit 2 → 001 = 1
        val = _unpack_intel(data, start_bit=2, length=3)
        assert val == 1

    def test_unpack_intel_multi_byte(self) -> None:
        data = bytes([0x01, 0x02])
        val = _unpack_intel(data, start_bit=0, length=16)
        assert val == 0x0201  # little-endian

    def test_unpack_motorola_single_byte(self) -> None:
        """Motorola: MSB at start_bit=7, LSB wraps to start over."""
        data = bytes([0b10101010])
        val = _unpack_motorola(data, start_bit=7, length=8)
        assert val == 85  # bit-reversed D0→D7: 01010101

    def test_unpack_motorola_high_bits(self) -> None:
        """Motorola: start_bit=7 → bit 0 in byte, length=2 → bits 0,1 = 1,1 → 11 = 3"""
        data = bytes([0b00000011])
        val = _unpack_motorola(data, start_bit=7, length=2)
        assert val == 3

    def test_unpack_motorola_multi_byte(self) -> None:
        data = bytes([0x01, 0x02])
        with_motorola = _unpack_motorola(data, start_bit=7, length=16)
        assert isinstance(with_motorola, int)


class TestPduHelper:
    def test_get_message_by_name(self) -> None:
        helper = PduHelper(DB_PATH)
        msg = helper.get_message("CoolantTemp")
        assert msg.name == "CoolantTemp"
        assert msg.length > 0

    def test_pack_unsigned_roundtrip(self) -> None:
        """Unsigned Intel signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("VehicleSpeed", {"VehicleSpeed": 100.0})
        values = helper.unpack("VehicleSpeed", payload)
        assert abs(values["VehicleSpeed"] - 100.0) < 0.1

    def test_pack_motorola_roundtrip(self) -> None:
        """Unsigned Motorola signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("CoolantTemp", {"CoolantTemp": 80.0})
        values = helper.unpack("CoolantTemp", payload)
        assert abs(values["CoolantTemp"] - 80.0) < 0.1

    def test_pack_motorola_multi_byte(self) -> None:
        """16-bit unsigned Motorola signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("HV_Voltage", {"HV_Voltage": 400.0})
        values = helper.unpack("HV_Voltage", payload)
        assert abs(values["HV_Voltage"] - 400.0) < 0.1

    def test_lookup_can_id(self) -> None:
        helper = PduHelper(DB_PATH)
        cid = helper.lookup_can_id("VehicleSpeed", bus="Powertrain_CAN")
        assert cid == 0x100

    def test_get_message_unknown_raises(self) -> None:
        helper = PduHelper(DB_PATH)
        try:
            helper.get_message("NonExistent")
            assert False, "Should have raised"
        except KeyError:
            pass

    def test_unpack_message_function(self) -> None:
        db = PduDatabase(DB_PATH)
        entry = db.by_name_and_bus("VehicleSpeed", "Powertrain_CAN")
        msg = Message(entry)
        msg.set("VehicleSpeed", 100.0)
        payload = msg.pack()
        values = unpack_message(payload, msg)
        assert abs(values["VehicleSpeed"] - 100.0) < 0.1

    def test_pack_unpack_multiple_messages(self) -> None:
        """Different message types pack/unpack correctly."""
        helper = PduHelper(DB_PATH)
        pairs = [
            ("VehicleSpeed", {"VehicleSpeed": 50.0}),
            ("CoolantTemp", {"CoolantTemp": 90.0}),
            ("BatterySOC", {"BatterySOC": 75.0}),
        ]
        for name, signals in pairs:
            payload = helper.pack(name, signals)
            values = helper.unpack(name, payload)
            for sname, expected in signals.items():
                assert abs(values[sname] - expected) < 0.5, f"{name}.{sname}: {values[sname]} != {expected}"

    def test_db_property(self) -> None:
        helper = PduHelper(DB_PATH)
        assert helper.db is not None
