"""Regression coverage for the security review; no live credentials or Bluetooth."""
from dataclasses import asdict
import json
import logging
from types import SimpleNamespace

import pytest

from opendp3.config import Config, load_config, resolve_user_id
from opendp3.protocol import AuthenticationError, ProtocolError, derive_session_key, parse_packet
from opendp3.vendor import keydata
from opendp3.vendor.crc import crc16
from opendp3.vendor.packet import InvalidPacket, Packet, PacketV4


def test_every_seed_requires_a_complete_table_window():
    rejected = 0
    for low in range(256):
        for high in range(256):
            offset = low * 16 + ((high - 1) & 255) * 256
            seed = bytes([low, high])
            if offset + 16 > len(keydata._data):
                with pytest.raises(AuthenticationError):
                    derive_session_key(seed, bytes(range(16)))
                rejected += 1
            else:
                assert len(derive_session_key(seed, bytes(range(16)))) == 16
    assert rejected == 2176
    # Golden vectors preserve valid behavior at the beginning and end of the table.
    assert derive_session_key(b"\0\1", bytes(range(16))).hex() == "cf19aab35c7605235e885521945354d8"
    assert derive_session_key(b"\x0f\xff", bytes(range(16))).hex() == "a9cff83b01b4c82b47993892f29de9b1"


def test_partial_table_material_is_rejected(monkeypatch):
    monkeypatch.setattr(keydata, "get8bytes", lambda offset: b"short")
    with pytest.raises(AuthenticationError):
        derive_session_key(b"\1\1", b"r" * 16)


def test_invalid_packet_subclass_is_not_accepted(packet, monkeypatch):
    # The outer checks pass; a future change in the vendored parser must still
    # not turn an InvalidPacket into an apparently valid telemetry message.
    raw = packet(bms_max_cell_temp=23)
    monkeypatch.setattr(Packet, "from_bytes", lambda *args, **kwargs: InvalidPacket("parse failure"))
    with pytest.raises(ProtocolError, match="Unsupported DP3 frame"):
        parse_packet(raw)


def test_non_packet_decoder_result_is_rejected_before_invalid_check(packet, monkeypatch):
    """PacketV4 must fail the type gate without reaching the invalid-subclass check."""
    raw = packet(bms_max_cell_temp=23)
    monkeypatch.setattr(Packet, "from_bytes", lambda *args, **kwargs: PacketV4(1, 2, 3, 4))
    monkeypatch.setattr(
        Packet, "is_invalid",
        lambda value: (_ for _ in ()).throw(AssertionError("type gate must run first")),
    )
    with pytest.raises(ProtocolError, match="Unsupported DP3 frame"):
        parse_packet(raw)


def corrupt_crc(raw):
    return raw[:-1] + bytes([raw[-1] ^ 1])


def corrupt_header_with_valid_body_crc(raw):
    raw = bytearray(raw)
    raw[4] ^= 1
    raw[-2:] = crc16(raw[:-2]).to_bytes(2, "little")
    return bytes(raw)


@pytest.mark.parametrize("case", ["prefix", "v3_short", "v3_crc", "v3_header",
                                  "v4_short", "v4_length", "v4_crc", "v4_header"])
def test_parser_failures_never_log_or_return_payloads(case, caplog):
    secret = b"PRIVATE_AUTH_OR_TELEMETRY_123"
    v3 = Packet(2, 0x21, 0xFE, 0x15, secret).to_bytes()
    v4 = PacketV4(2, 0x21, 0xFE, 0x15, secret).to_bytes()
    cases = {
        "prefix": (Packet, b"BAD_PREFIX" + secret),
        "v3_short": (Packet, b"\xaa\x03SECRET"),
        "v3_crc": (Packet, corrupt_crc(v3)),
        "v3_header": (Packet, corrupt_header_with_valid_body_crc(v3)),
        "v4_short": (PacketV4, b"SECRET"),
        "v4_length": (PacketV4, v4 + secret),
        "v4_crc": (PacketV4, corrupt_crc(v4)),
        "v4_header": (PacketV4, corrupt_header_with_valid_body_crc(v4)),
    }
    codec, raw = cases[case]
    with caplog.at_level(logging.ERROR, logger="opendp3.vendor.packet"):
        result = codec.from_bytes(raw)
    assert Packet.is_invalid(result)
    records = [r for r in caplog.records if r.name == "opendp3.vendor.packet"]
    assert records and all(not r.args for r in records)
    output = caplog.text + repr(result)
    assert raw.hex() not in output and secret.hex() not in output and secret.decode() not in output


def sample_config():
    return asdict(Config("AA:BB:CC:DD:EE:FF", "MR51123456789012", "123456"))


@pytest.mark.parametrize("change", [
    {"address": 123}, {"serial": []}, {"user_id": 123}, {"region": {}},
    {"firmware": False}, {"conditions": []}, {"temperature_jump": "PRIVATE_VALUE"},
    {"temperature_window": None}, {"temperature_jump": True},
    {"temperature_jump": float("nan")}, {"temperature_window": float("inf")},
    {"temperature_jump": 10 ** 1000}, {"unexpected_PRIVATE_KEY": "PRIVATE_VALUE"},
])
def test_bad_config_types_fail_with_safe_message(tmp_path, change):
    values = sample_config(); values.update(change)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_config(path)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("contents", ["[]", "null", '"PRIVATE_VALUE"', "{}", "{PRIVATE_VALUE", "\ufeff{}"])
def test_malformed_config_shape_fails_safely(tmp_path, contents):
    path = tmp_path / "config.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid configuration file") as error:
        load_config(path)
    assert "PRIVATE" not in str(error.value)


def test_cli_bad_config_has_no_traceback_or_input_echo(tmp_path, capsys):
    from opendp3.cli import main
    values = sample_config(); values["temperature_jump"] = "PRIVATE_VALUE"
    (tmp_path / "config.json").write_text(json.dumps(values), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path), "record"]) == 1
    output = capsys.readouterr().err
    assert "OpenPowerstation:" in output and "Traceback" not in output and "PRIVATE_VALUE" not in output


@pytest.mark.parametrize("body", ["invalid_json", [], None, {"code": "0", "data": []},
                                  {"code": "0", "data": {"user": {"userId": False}}}])
async def test_login_bad_responses_are_sanitized(monkeypatch, body):
    import httpx
    request_bodies = []
    def response_json():
        if body == "invalid_json":
            raise json.JSONDecodeError("PRIVATE_PARSE_DETAIL", "PRIVATE_RESPONSE", 0)
        return body
    class Client:
        def __init__(self, **kwargs):
            assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, **kwargs):
            request_bodies.append(kwargs["json"])
            return SimpleNamespace(status_code=200, json=response_json)
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(ValueError) as error:
        await resolve_user_id("email@example.test", "PRIVATE_PASSWORD", "US")
    assert "PRIVATE" not in str(error.value)
    assert request_bodies == [{}]  # Request-body references released on failure too.
