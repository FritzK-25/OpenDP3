"""Document the vendored schema, never connect to hardware or read recordings."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from google.protobuf.descriptor import FieldDescriptor
from openpowerstation import DECODER_VERSION, UPSTREAM_REVISION
from openpowerstation.decoder import FIELDS, FIELD_MAP, UNAVAILABLE
from openpowerstation.vendor.pb import mr521_pb2


def messages():
    def descend(message):
        yield message
        for child in message.nested_types:
            yield from descend(child)
    for message in mr521_pb2.DESCRIPTOR.message_types_by_name.values():
        yield from descend(message)


def anchor(kind, name):
    return kind + "-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def type_name(field):
    if field.message_type:
        name = field.message_type.full_name
        return f"[{name}](#{anchor('message', name)})"
    if field.enum_type:
        name = field.enum_type.full_name
        return f"[{name}](#{anchor('enum', name)})"
    return next(name[5:].lower() for name, value in vars(FieldDescriptor).items()
                if name.startswith("TYPE_") and value == field.type)


def reachable_from(message):
    result = set()
    def visit(current):
        if current.full_name in result:
            return
        result.add(current.full_name)
        for field in current.fields:
            if field.message_type:
                visit(field.message_type)
    visit(message)
    return result


def render():
    schema = mr521_pb2.DESCRIPTOR
    definitions = list(messages())
    enums = list(schema.enum_types_by_name.values()) + [e for m in definitions for e in m.enum_types]
    display = mr521_pb2.DisplayPropertyUpload.DESCRIPTOR
    reachable = reachable_from(display)
    direct = [f for f in FIELDS if f.key in display.fields_by_name]
    upstream = f"https://github.com/rabits/ha-ef-ble/blob/{UPSTREAM_REVISION}/custom_components/ef_ble/eflib"
    lines = [
        "# Pinned DP3 protocol schema reference", "",
        "[Application README and detailed event behavior](REFERENCE.md#events-and-ble-api-reference)", "",
        "Generated from the exact vendored protobuf descriptor and current field map.",
        "**Schema definitions are not a list of functioning endpoints or guaranteed device measurements.**",
        "This file contains no account configuration, actual telemetry, or captured payloads.", "",
        f"- Source schema: `{schema.name}`; package `{schema.package}`.",
        f"- Upstream revision: `{UPSTREAM_REVISION}`.",
        f"- OpenPowerstation decoder: `{DECODER_VERSION}`.",
        f"- Descriptor SHA-256: `{hashlib.sha256(schema.serialized_pb).hexdigest()}`.",
        f"- {len(schema.message_types_by_name)} top-level messages, {len(definitions)} including nested messages; "
        f"{sum(len(m.fields) for m in definitions)} field definitions.",
        f"- {len(enums)} enums, {sum(len(e.values) for e in enums)} named enum values.",
        f"- {len(FIELDS)} normalized field names: {len(direct)} direct display fields and "
        f"{len(FIELDS)-len(direct)} conditional extra-battery values.", "",
        f"Sources: [pinned upstream schema module]({upstream}/pb/mr521_pb2.py), "
        f"[pinned DP3 handler]({upstream}/devices/delta_pro_3.py), "
        "[local decoder](../src/openpowerstation/decoder.py). Schema identifiers and structure originate",
        "from ha-ef-ble under Apache-2.0; see [notices](../THIRD_PARTY_NOTICES.md).", "",
        "## How to read this inventory", "",
        "- A tag is a protobuf field number within that message, not a fault code or BLE command ID.",
        "- Protobuf types specify representation. They do not establish scaling, physical units, semantics, or accuracy.",
        "- Presence = yes means omission can be distinguished from an explicitly present default value. Repeated fields do not have scalar presence.",
        "- `DisplayPropertyUpload` is routed only from source `0x02`, command set `0xFE`, command `0x15`.",
        "- DP3 `ConfigWrite` is sent only to destination `0x02`, command set `0xFE`, command `0x11`, and only for the two opt-in AC output fields. Jackery controls use its separate portable BLE route described in the README.",
        "- Other top-level routes are unimplemented unless specifically described as session authentication or opt-in control in the README.",
        "- Display helpers are decodable only when nested inside a supported display upload and actually present.",
        "- Known unmapped display fields are retained in local decoded JSON. Unknown protobuf tags remain only in raw frames.",
        "- Runtime fields and event-push fields are not automatically obtained by importing their schema classes.",
        "- Apart from the two opt-in DP3 AC output fields above, DP3 commands, configuration and acknowledgements listed here are not sent by OpenPowerstation. Jackery controls are governed by its separate allowlist; no additional permissions are enabled by this document.",
        "- The schema supplies no named `EventPush.LogItem.event_no` dictionary. Do not invent event IDs or map them to Error 036.", "",
        "## Normalized field catalog", "",
        "These fields can appear in numeric storage, charts/coverage and sanitized telemetry CSV when observed.",
        "Values are not promised on every firmware or packet. Raw error fields keep their original numeric values.", "",
        "| Field | Label | Unit | Display tag / source | Quality on a nonduplicate packet | Event eligibility |",
        "|---|---|---|---|---|---|",
    ]
    for field in FIELDS:
        descriptor = display.fields_by_name.get(field.key)
        source = str(descriptor.number) if descriptor else "Conditional reserved-data mapping"
        quality = "observed" if descriptor else "community_mapping" if field.key.endswith("_soc") else "unverified"
        if descriptor and field.group == "temperature":
            event = "suspect_telemetry rule"
        elif field.key == "errcode" or field.key.endswith("_err_code"):
            event = "device_error rule"
        elif field.key in {"cms_bms_run_state", "cms_chg_dsg_state", "plug_in_info_ac_charger_flag"}:
            event = "state_change rule"
        else:
            event = "None"
        lines.append(f"| `{field.key}` | {field.label} | {field.unit or 'Raw / no verified unit'} | "
                     f"{source} | `{quality}` | {event} |")
    lines += ["", "All recognized duplicates use `repeated_unverified` instead and do not trigger these rules.",
              "The extra-battery mapping requires a nonzero connection flag in the same packet and enough reserved words.",
              "Its temperature interpretation is unverified; refer to the README for the preservation and comparison rules.", "",
              "Currently unavailable as verified normalized measurements: " + ", ".join(UNAVAILABLE) + ".", "",
              "## Message index", "", "| Message | Fields | Current treatment |", "|---|---:|---|"]

    def treatment(message):
        if message.full_name == display.full_name:
            return "Supported display route; selected fields normalized, other present known fields kept locally"
        if message.full_name in reachable:
            return "Nested helper reachable from display uploads; local JSON only unless explicitly mapped"
        if message.name in {"EventPush", "LogItem", "EventAck"}:
            return "Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender"
        if message.name == "RuntimePropertyUpload":
            return "Runtime schema only; no implemented runtime-property route"
        if message.name == "ConfigWrite":
            return ("Outbound control route, opt-in: only `cfg_hv_ac_out_open` and "
                    "`cfg_lv_ac_out_open` are ever sent, and only while control is enabled "
                    "in Settings. Every other field on this message is refused by the gate")
        return "Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented"

    for message in definitions:
        lines.append(f"| [{message.full_name}](#{anchor('message', message.full_name)}) | {len(message.fields)} | {treatment(message)} |")
    lines += ["", "## All message definitions", ""]
    for message in definitions:
        lines += [f'<a id="{anchor("message", message.full_name)}"></a>', "",
                  f"### {message.full_name}", "", treatment(message) + ".", "",
                  "| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |",
                  "|---:|---|---|:---:|:---:|---|"]
        for field in message.fields:
            if message.full_name == display.full_name:
                use = "Normalized numeric field" if field.name in FIELD_MAP else "Local decoded JSON when present; not normalized/exported"
            elif message.full_name in reachable:
                use = "Nested local JSON when present in a supported upload"
            else:
                use = "Schema definition; no current standalone decoding"
            lines.append(f"| {field.number} | `{field.name}` | {type_name(field)} | "
                         f"{'Yes' if field.is_repeated else 'No'} | {'Yes' if field.has_presence else 'No'} | {use} |")
        lines.append("")
    lines += ["## All enum definitions", "",
              "The names/numbers below are preserved exactly, including upstream spelling.",
              "An enum belongs only to fields that reference its type; it is not a generic device-error dictionary.",
              "Unrecognized numeric values must not be silently assigned the closest known meaning.", ""]
    for enum in enums:
        lines += [f'<a id="{anchor("enum", enum.full_name)}"></a>', "",
                  f"### {enum.full_name}", "", "| Value | Schema name |", "|---:|---|"]
        lines += [f"| {value.number} | `{value.name}` |" for value in enum.values]
        lines.append("")
    lines += ["## Regeneration", "",
              "From the project root, with the project's dependencies installed:", "",
              "```powershell", ".\\.venv\\Scripts\\python.exe scripts/generate_protocol_reference.py",
              ".\\.venv\\Scripts\\python.exe scripts/generate_protocol_reference.py --check", "```", "",
              "The generator only inspects source descriptors and the field map. It does not open configuration,",
              "read recordings, initialize Bluetooth, authenticate, or modify the device.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the generated reference is stale")
    args = parser.parse_args()
    destination = ROOT / "docs/PROTOCOL_SCHEMA.md"
    text = render()
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != text:
            raise SystemExit("Protocol schema reference is stale; regenerate it.")
        print("Protocol schema reference matches the vendored descriptor and field map.")
    else:
        destination.write_text(text, encoding="utf-8")
        print("Updated docs/PROTOCOL_SCHEMA.md")


if __name__ == "__main__":
    main()
