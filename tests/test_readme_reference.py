"""The reference's event and protocol reference must describe what the code does.

That reference is written by hand, and it had drifted. It catalogued 18 of the
23 event kinds the recorders emit and the export allowlist recognizes, counted
40 decoder fields where there are 42, and said ConfigWrite is not used and no
output control leaves the radio after authentication, while the gate sends the
two allowlisted AC-output writes whenever control is on. Each check below ties
one of those statements to the code it describes, so a change to either side
fails here instead of misleading a reader judging device-write risk.
"""
from pathlib import Path
import re

from openpowerstation.decoder import FIELDS
from openpowerstation.events import PIN_KINDS, SAFE_EVENT_KINDS, SEGMENT_KINDS
from openpowerstation.protocol import CONTROL_FIELDS
from openpowerstation.vendor.pb import mr521_pb2

README = Path(__file__).resolve().parents[1] / "docs" / "REFERENCE.md"
TEXT = README.read_text(encoding="utf-8")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def number(text):
    return int(text) if text.isdigit() else WORDS.get(text.lower(), text)


def section(heading):
    """The lines under `heading`, up to the next heading at its level or above."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = TEXT.splitlines()
    start = lines.index(heading) + 1
    for end in range(start, len(lines)):
        marks = len(lines[end]) - len(lines[end].lstrip("#"))
        if 0 < marks <= level and lines[end][marks:marks + 1] == " ":
            return lines[start:end]
    return lines[start:]


def table(lines, header):
    """Cells of the Markdown table whose header row starts with `header`."""
    start = next(index for index, line in enumerate(lines) if line.startswith(header))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


CATALOG = section("### Complete OpenPowerstation timeline-event catalog")
EVENTS = {row[0].strip("`"): row for row in table(CATALOG, "| Identifier |")}


def test_the_event_catalog_lists_exactly_the_recognized_kinds():
    rows = table(CATALOG, "| Identifier |")
    assert len(rows) == len(EVENTS), "an identifier is listed twice"
    assert sorted(EVENTS) == sorted(SAFE_EVENT_KINDS)
    stated = re.search(r"The following (\d+) strings are recognized", "\n".join(CATALOG))
    assert stated and int(stated.group(1)) == len(SAFE_EVENT_KINDS)


def test_every_recognized_kind_is_described():
    headings = "\n".join(line for line in CATALOG if line.startswith("#### "))
    assert [kind for kind in sorted(SAFE_EVENT_KINDS) if f"`{kind}`" not in headings] == []


def test_segment_and_pin_columns_follow_the_recorder():
    for kind, (_, _, segment, pins) in EVENTS.items():
        # Recorder.event() starts a segment for exactly these kinds.
        assert segment.startswith("Yes") == (kind in SEGMENT_KINDS), kind
        # ...and always pins these; other kinds pin only where a rule asks.
        if kind in PIN_KINDS:
            assert pins.startswith("Yes"), kind


def test_the_field_reference_matches_the_decoder():
    lines = section("### Telemetry fields available to this decoder")
    text = " ".join(lines)
    display = mr521_pb2.DisplayPropertyUpload.DESCRIPTOR
    direct = [field for field in FIELDS if field.key in display.fields_by_name]
    stated = re.search(r"maps \*\*(\w+) numeric field names\*\*: (\w+) directly from "
                       r"`DisplayPropertyUpload`, plus (\w+) conditional", text)
    assert stated, "the field count sentence changed shape"
    assert [number(value) for value in stated.groups()] == [
        len(FIELDS), len(direct), len(FIELDS) - len(direct)]
    grouped = {name for row in table(lines, "| Group |")
               for name in re.findall(r"`([a-z0-9_]+)`", row[1])}
    assert sorted(grouped) == sorted(field.key for field in FIELDS)
    schema = re.search(r"The display schema contains (\d+) fields", text)
    assert schema and int(schema.group(1)) == len(display.fields)


def test_the_schema_counts_match_the_vendored_descriptor():
    text = " ".join(section("#### Runtime measurements and configuration definitions"))
    schema = mr521_pb2.DESCRIPTOR

    def descend(message):
        yield message
        for child in message.nested_types:
            yield from descend(child)

    definitions = [nested for message in schema.message_types_by_name.values()
                   for nested in descend(message)]
    enums = list(schema.enum_types_by_name.values()) + [
        enum for message in definitions for enum in message.enum_types]
    runtime = re.search(r"`RuntimePropertyUpload` has (\d+) fields", text)
    assert runtime and int(runtime.group(1)) == len(mr521_pb2.RuntimePropertyUpload.DESCRIPTOR.fields)
    stated = re.search(r"It contains (\d+) top-level messages plus (\w+) nested message, "
                       r"(\d+) field definitions, and (\d+) enums with (\d+) named values", text)
    assert stated, "the schema count sentence changed shape"
    assert [number(value) for value in stated.groups()] == [
        len(schema.message_types_by_name),
        len(definitions) - len(schema.message_types_by_name),
        sum(len(message.fields) for message in definitions),
        len(enums), sum(len(enum.values) for enum in enums)]


def test_the_dp3_route_table_names_the_control_allowlist():
    lines = section("### Device-originated messages and event structures")
    row = next(line for line in lines if line.startswith("| `ConfigWrite`"))
    assert "Not used" not in row
    assert set(re.findall(r"`(cfg_[a-z_]+)`", row)) == set(CONTROL_FIELDS)
