"""Jackery screen-timeout control uses different write and readback fields."""
import json

import pytest

from openpowerstation.jackery import (JACKERY_CONTROLS, jackery_control_command,
                              jackery_control_readback_matches, jackery_control_value)
from openpowerstation.jackery_fields import map_properties


def command_body(command: str) -> tuple[int, int, dict]:
    assert command.startswith("DFEC00")
    action = int(command[6:8], 16)
    message_type = int(command[8:10], 16)
    body_length = int(command[10:12], 16)
    body = bytes.fromhex(command[12:]).decode("utf-8")
    assert len(body.encode("utf-8")) == body_length
    return action, message_type, json.loads(body)


@pytest.mark.parametrize(
    ("option", "command_minutes", "readback_enum"),
    (("always_on", 0, 1), ("2m", 2, 2), ("2h", 120, 3)),
)
def test_screen_timeout_command_and_readback_are_distinct(option, command_minutes, readback_enum):
    spec = JACKERY_CONTROLS["jackery_screen_timeout"]
    assert spec["wire"] == "sltb"
    assert spec["command_wire"] == "slt"
    assert jackery_control_value("jackery_screen_timeout", option) == readback_enum
    action, message_type, body = command_body(
        jackery_control_command("jackery_screen_timeout", option)
    )
    assert action == 0x08
    assert message_type == 0x04
    assert body == {"slt": command_minutes}


def test_screen_timeout_readback_is_normalized_from_sltb():
    assert map_properties({"sltb": 1})["jackery_screen_timeout"] == 1.0
    assert map_properties({"sltb": 2})["jackery_screen_timeout"] == 2.0
    assert map_properties({"sltb": 3})["jackery_screen_timeout"] == 3.0
    assert "jackery_screen_timeout" not in map_properties({"sltb": 4})


@pytest.mark.parametrize("bad", (True, 1.0, 1.5, 2.5, 3.0, float("nan")))
def test_screen_timeout_readback_rejects_non_integer_enums(bad):
    assert "jackery_screen_timeout" not in map_properties({"sltb": bad})


@pytest.mark.parametrize("bad", (True, 1.0, 1.5, 3.0, float("nan")))
def test_screen_timeout_confirmation_rejects_non_integer_equivalents(bad):
    assert not jackery_control_readback_matches("jackery_screen_timeout", "always_on", bad)
    assert jackery_control_readback_matches("jackery_screen_timeout", "always_on", 1)


def test_other_select_readback_is_also_type_strict():
    assert jackery_control_readback_matches("jackery_battery_save", "full", 0)
    assert not jackery_control_readback_matches("jackery_battery_save", "full", False)
    assert not jackery_control_readback_matches("jackery_battery_save", "full", 0.0)


def test_screen_timeout_rejects_unadvertised_options():
    with pytest.raises(ValueError):
        jackery_control_value("jackery_screen_timeout", "5m")
