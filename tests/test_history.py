"""Verify human-readable message codes and severity without dropping raw values."""

from struct import pack

from custom_components.automower_ble_plus.history import _decode_message


def test_history_reports_newer_vendor_message_and_its_severity():
    message = _decode_message(1, pack("<IIB", 1795600000, 501, 3))
    assert message["code"] == 501
    assert message["message_key"] == "mmi_communication_error"
    assert message["message"] == "Mmi communication error"
    assert message["severity"] == 3
    assert message["severity_name"] == "warning"


def test_history_does_not_treat_an_empty_message_as_an_error():
    message = _decode_message(0, pack("<IIB", 0, 0, 0))
    assert message["timestamp"] is None
    assert message["message_key"] == "no_message"
    assert message["message"] == "No message"
