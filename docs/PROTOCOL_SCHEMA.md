# Pinned DP3 protocol schema reference

[Application README and detailed event behavior](REFERENCE.md#events-and-ble-api-reference)

Generated from the exact vendored protobuf descriptor and current field map.
**Schema definitions are not a list of functioning endpoints or guaranteed device measurements.**
This file contains no account configuration, actual telemetry, or captured payloads.

- Source schema: `mr521.proto`; package `mr521`.
- Upstream revision: `7cde8e5922589b5e3c81585890b5188747f5b037`.
- OpenPowerstation decoder: `dp3-mr521/0.1.4+7cde8e592258`.
- Descriptor SHA-256: `ee0b56296016c4a7ae7e73d50e4e1061fca3ae9133e96e29b5f51e6c3c41d575`.
- 46 top-level messages, 47 including nested messages; 749 field definitions.
- 12 enums, 68 named enum values.
- 42 normalized field names: 38 direct display fields and 4 conditional extra-battery values.

Sources: [pinned upstream schema module](https://github.com/rabits/ha-ef-ble/blob/7cde8e5922589b5e3c81585890b5188747f5b037/custom_components/ef_ble/eflib/pb/mr521_pb2.py), [pinned DP3 handler](https://github.com/rabits/ha-ef-ble/blob/7cde8e5922589b5e3c81585890b5188747f5b037/custom_components/ef_ble/eflib/devices/delta_pro_3.py), [local decoder](../src/openpowerstation/decoder.py). Schema identifiers and structure originate
from ha-ef-ble under Apache-2.0; see [notices](../THIRD_PARTY_NOTICES.md).

## How to read this inventory

- A tag is a protobuf field number within that message, not a fault code or BLE command ID.
- Protobuf types specify representation. They do not establish scaling, physical units, semantics, or accuracy.
- Presence = yes means omission can be distinguished from an explicitly present default value. Repeated fields do not have scalar presence.
- `DisplayPropertyUpload` is routed only from source `0x02`, command set `0xFE`, command `0x15`.
- DP3 `ConfigWrite` is sent only to destination `0x02`, command set `0xFE`, command `0x11`, and only for the two opt-in AC output fields. Jackery controls use its separate portable BLE route described in the README.
- Other top-level routes are unimplemented unless specifically described as session authentication or opt-in control in the README.
- Display helpers are decodable only when nested inside a supported display upload and actually present.
- Known unmapped display fields are retained in local decoded JSON. Unknown protobuf tags remain only in raw frames.
- Runtime fields and event-push fields are not automatically obtained by importing their schema classes.
- Apart from the two opt-in DP3 AC output fields above, DP3 commands, configuration and acknowledgements listed here are not sent by OpenPowerstation. Jackery controls are governed by its separate allowlist; no additional permissions are enabled by this document.
- The schema supplies no named `EventPush.LogItem.event_no` dictionary. Do not invent event IDs or map them to Error 036.

## Normalized field catalog

These fields can appear in numeric storage, charts/coverage and sanitized telemetry CSV when observed.
Values are not promised on every firmware or packet. Raw error fields keep their original numeric values.

| Field | Label | Unit | Display tag / source | Quality on a nonduplicate packet | Event eligibility |
|---|---|---|---|---|---|
| `bms_max_cell_temp` | BMS maximum cell temperature | °C | 259 | `observed` | suspect_telemetry rule |
| `bms_min_cell_temp` | BMS minimum cell temperature | °C | 258 | `observed` | suspect_telemetry rule |
| `bms_max_mos_temp` | BMS maximum MOS temperature | °C | 261 | `observed` | suspect_telemetry rule |
| `bms_min_mos_temp` | BMS minimum MOS temperature | °C | 260 | `observed` | suspect_telemetry rule |
| `cms_batt_temp` | CMS battery temperature | °C | 102 | `observed` | suspect_telemetry rule |
| `bms_batt_soc` | Main battery SOC | % | 242 | `observed` | None |
| `cms_batt_soc` | System SOC | % | 262 | `observed` | None |
| `cms_batt_soh` | System SOH | % | 263 | `observed` | None |
| `bms_batt_soh` | Main battery SOH | % | 243 | `observed` | None |
| `pow_in_sum_w` | Total input | W | 3 | `observed` | None |
| `pow_out_sum_w` | Total output | W | 4 | `observed` | None |
| `pow_get_ac_in` | AC input | W | 54 | `observed` | None |
| `pow_get_ac_lv_out` | AC LV output (raw sign) | W | 56 | `observed` | None |
| `pow_get_ac_hv_out` | AC HV output (raw sign) | W | 55 | `observed` | None |
| `pow_get_pv_h` | PV HV input | W | 35 | `observed` | None |
| `pow_get_pv_l` | PV LV input | W | 36 | `observed` | None |
| `pow_get_bms` | Battery power (raw sign) | W | 158 | `observed` | None |
| `flow_info_ac_hv_out` | AC HV output state (raw bitmask) | Raw / no verified unit | 48 | `observed` | None |
| `flow_info_ac_lv_out` | AC LV output state (raw bitmask) | Raw / no verified unit | 49 | `observed` | None |
| `cms_bms_run_state` | BMS run state (raw) | Raw / no verified unit | 275 | `observed` | state_change rule |
| `cms_chg_dsg_state` | Charge/discharge state (raw) | Raw / no verified unit | 282 | `observed` | state_change rule |
| `plug_in_info_ac_charger_flag` | AC connected | Raw / no verified unit | 202 | `observed` | state_change rule |
| `plug_in_info_pv_h_type` | PV HV source type (raw) | Raw / no verified unit | 40 | `observed` | None |
| `plug_in_info_pv_l_type` | PV LV source type (raw) | Raw / no verified unit | 43 | `observed` | None |
| `errcode` | Device error (raw code) | Raw / no verified unit | 1 | `observed` | device_error rule |
| `bms_err_code` | BMS error (raw code) | Raw / no verified unit | 140 | `observed` | device_error rule |
| `mppt_err_code` | MPPT error (raw code) | Raw / no verified unit | 215 | `observed` | device_error rule |
| `inv_err_code` | Inverter error (raw code) | Raw / no verified unit | 450 | `observed` | device_error rule |
| `extra1_soc` | Extra battery 1 SOC (community mapping) | % | Conditional reserved-data mapping | `community_mapping` | None |
| `extra1_temperature` | Extra battery 1 temperature (unverified) | °C | Conditional reserved-data mapping | `unverified` | None |
| `extra2_soc` | Extra battery 2 SOC (community mapping) | % | Conditional reserved-data mapping | `community_mapping` | None |
| `extra2_temperature` | Extra battery 2 temperature (unverified) | °C | Conditional reserved-data mapping | `unverified` | None |
| `pd_err_code` | pd err code (raw) | Raw / no verified unit | 213 | `observed` | device_error rule |
| `llc_err_code` | llc err code (raw) | Raw / no verified unit | 214 | `observed` | device_error rule |
| `llc_inv_err_code` | llc inv err code (raw) | Raw / no verified unit | 232 | `observed` | device_error rule |
| `dcdc_err_code` | dcdc err code (raw) | Raw / no verified unit | 437 | `observed` | device_error rule |
| `plug_in_info_5p8_err_code` | plug in info 5p8 err code (raw) | Raw / no verified unit | 216 | `observed` | device_error rule |
| `plug_in_info_acp_err_code` | plug in info acp err code (raw) | Raw / no verified unit | 117 | `observed` | device_error rule |
| `plug_in_info_4p8_1_err_code` | plug in info 4p8 1 err code (raw) | Raw / no verified unit | 217 | `observed` | device_error rule |
| `plug_in_info_4p8_2_err_code` | plug in info 4p8 2 err code (raw) | Raw / no verified unit | 218 | `observed` | device_error rule |
| `plug_in_info_dcp_err_code` | plug in info dcp err code (raw) | Raw / no verified unit | 438 | `observed` | device_error rule |
| `plug_in_info_dcp2_err_code` | plug in info dcp2 err code (raw) | Raw / no verified unit | 439 | `observed` | device_error rule |

All recognized duplicates use `repeated_unverified` instead and do not trigger these rules.
The extra-battery mapping requires a nonzero connection flag in the same packet and enough reserved words.
Its temperature interpretation is unverified; refer to the README for the preservation and comparison rules.

Currently unavailable as verified normalized measurements: Pack voltage, Pack current, PV HV voltage, PV HV current, PV LV voltage, PV LV current.

## Message index

| Message | Fields | Current treatment |
|---|---:|---|
| [mr521.EventPush](#message-mr521-eventpush) | 3 | Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender |
| [mr521.EventPush.LogItem](#message-mr521-eventpush-logitem) | 4 | Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender |
| [mr521.EventAck](#message-mr521-eventack) | 3 | Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender |
| [mr521.ReqTouStrategy](#message-mr521-reqtoustrategy) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgBeep](#message-mr521-cfgbeep) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgAcOutAlwaysOn](#message-mr521-cfgacoutalwayson) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgBmsPowerOffWriteAck](#message-mr521-cfgbmspoweroffwriteack) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgBmsPushWrite](#message-mr521-cfgbmspushwrite) | 4 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgBmsPushWriteAck](#message-mr521-cfgbmspushwriteack) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.SetTimeTaskWrite](#message-mr521-settimetaskwrite) | 9 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.SetTimeTaskWriteAck](#message-mr521-settimetaskwriteack) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.GetAllTimeTaskReadck](#message-mr521-getalltimetaskreadck) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgEnergyBackup](#message-mr521-cfgenergybackup) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgAcHvAlwaysOn](#message-mr521-cfgachvalwayson) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgAcLvAlwaysOn](#message-mr521-cfgaclvalwayson) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgTouStrategy](#message-mr521-cfgtoustrategy) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgRj45CommcService](#message-mr521-cfgrj45commcservice) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgStormPattern](#message-mr521-cfgstormpattern) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgGeneratorMpptHybridMode](#message-mr521-cfggeneratormppthybridmode) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgGeneratorCareMode](#message-mr521-cfggeneratorcaremode) | 2 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.CfgEnergyStrategyOperateMode](#message-mr521-cfgenergystrategyoperatemode) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.InstallmentPaymentServeBelone](#message-mr521-installmentpaymentservebelone) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.InstallmentPaymentServe](#message-mr521-installmentpaymentserve) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.ResvInfo](#message-mr521-resvinfo) | 1 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.ResetMiddlemenDeliverySetting](#message-mr521-resetmiddlemendeliverysetting) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.PropertyUploadPeriod](#message-mr521-propertyuploadperiod) | 4 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.PvDcChgSetting](#message-mr521-pvdcchgsetting) | 3 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.PvDcChgSettingList](#message-mr521-pvdcchgsettinglist) | 1 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.PvChgMaxItem](#message-mr521-pvchgmaxitem) | 3 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.PvChgMaxList](#message-mr521-pvchgmaxlist) | 1 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.TimeTaskItemV2](#message-mr521-timetaskitemv2) | 10 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.TimeTaskItemV2List](#message-mr521-timetaskitemv2list) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.StatisticsRecordItem](#message-mr521-statisticsrecorditem) | 2 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.DisplayStatisticsRecordList](#message-mr521-displaystatisticsrecordlist) | 1 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.RuntimeStatisticsRecordList](#message-mr521-runtimestatisticsrecordlist) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.ErrcodeRecordItem](#message-mr521-errcoderecorditem) | 2 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.ErrcodeRecordList](#message-mr521-errcoderecordlist) | 1 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.WirelessCoordinateList](#message-mr521-wirelesscoordinatelist) | 6 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.TimeTaskParamDetail](#message-mr521-timetaskparamdetail) | 2 | Nested helper reachable from display uploads; local JSON only unless explicitly mapped |
| [mr521.ConfigWrite](#message-mr521-configwrite) | 93 | Outbound control route, opt-in: only `cfg_hv_ac_out_open` and `cfg_lv_ac_out_open` are ever sent, and only while control is enabled in Settings. Every other field on this message is refused by the gate |
| [mr521.ConfigWriteAck](#message-mr521-configwriteack) | 95 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.ConfigRead](#message-mr521-configread) | 1 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.ConfigReadAck](#message-mr521-configreadack) | 10 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.DisplayPropertyUpload](#message-mr521-displaypropertyupload) | 296 | Supported display route; selected fields normalized, other present known fields kept locally |
| [mr521.RuntimePropertyUpload](#message-mr521-runtimepropertyupload) | 144 | Runtime schema only; no implemented runtime-property route |
| [mr521.DevRequest](#message-mr521-devrequest) | 4 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |
| [mr521.DevRequestAck](#message-mr521-devrequestack) | 3 | Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented |

## All message definitions

<a id="message-mr521-eventpush"></a>

### mr521.EventPush

Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `event_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `event_seq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `event_item` | [mr521.EventPush.LogItem](#message-mr521-eventpush-logitem) | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-eventpush-logitem"></a>

### mr521.EventPush.LogItem

Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `unix_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `ms` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `event_no` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 4 | `event_detail` | float | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-eventack"></a>

### mr521.EventAck

Event schema only; no implemented EventPush route, event-number meanings, or acknowledgement sender.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `result` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `event_seq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `event_item_num` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-reqtoustrategy"></a>

### mr521.ReqTouStrategy

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `req_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgbeep"></a>

### mr521.CfgBeep

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `beep_act_count` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `beep_act_ms` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgacoutalwayson"></a>

### mr521.CfgAcOutAlwaysOn

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `ac_always_on_flag` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `ac_always_on_mini_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgbmspoweroffwriteack"></a>

### mr521.CfgBmsPowerOffWriteAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `bms_power_off` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `bms_power_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgbmspushwrite"></a>

### mr521.CfgBmsPushWrite

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `bms_heartbeap_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `bms_health_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 3 | `bms_heartbeap_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 4 | `bms_health_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgbmspushwriteack"></a>

### mr521.CfgBmsPushWriteAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `bms_heartbeap_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `bms_health_open` | bool | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-settimetaskwrite"></a>

### mr521.SetTimeTaskWrite

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `task_index` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `is_valid` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 3 | `is_cfg` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 4 | `is_enable` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 5 | `conflict_flag` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 6 | `type` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 7 | `time_mode` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 8 | `time_param` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 9 | `time_table` | uint32 | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-settimetaskwriteack"></a>

### mr521.SetTimeTaskWriteAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `task_index` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `sta` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-getalltimetaskreadck"></a>

### mr521.GetAllTimeTaskReadck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `time_task` | [mr521.SetTimeTaskWrite](#message-mr521-settimetaskwrite) | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgenergybackup"></a>

### mr521.CfgEnergyBackup

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `energy_backup_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `energy_backup_start_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgachvalwayson"></a>

### mr521.CfgAcHvAlwaysOn

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `ac_hv_always_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `ac_always_on_mini_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgaclvalwayson"></a>

### mr521.CfgAcLvAlwaysOn

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `ac_lv_always_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `ac_always_on_mini_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgtoustrategy"></a>

### mr521.CfgTouStrategy

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `tou_hours_strategy` | uint32 | Yes | No | Schema definition; no current standalone decoding |
| 2 | `tou_gird_chg_stop_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgrj45commcservice"></a>

### mr521.CfgRj45CommcService

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `RJ45_commc_timeout` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `RJ45_display_property_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `RJ45_runtime_property_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgstormpattern"></a>

### mr521.CfgStormPattern

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `storm_pattern_enable` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `storm_pattern_open_flag` | bool | No | Yes | Schema definition; no current standalone decoding |
| 3 | `storm_pattern_end_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfggeneratormppthybridmode"></a>

### mr521.CfgGeneratorMpptHybridMode

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `generator_pv_hybrid_mode_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `generator_pv_hybrid_mode_soc_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfggeneratorcaremode"></a>

### mr521.CfgGeneratorCareMode

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `generator_care_mode_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `generator_care_mode_start_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-cfgenergystrategyoperatemode"></a>

### mr521.CfgEnergyStrategyOperateMode

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `operate_self_powered_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 2 | `operate_scheduled_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 3 | `operate_tou_mode_open` | bool | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-installmentpaymentservebelone"></a>

### mr521.InstallmentPaymentServeBelone

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `serve_middlemen` | [mr521.SERVE_MIDDLEMEN](#enum-mr521-serve-middlemen) | No | Yes | Schema definition; no current standalone decoding |
| 2 | `installment_payment_overdue_limit` | [mr521.INSTALLMENT_PAYMENT_OVERDUE_LIMIT](#enum-mr521-installment-payment-overdue-limit) | No | Yes | Schema definition; no current standalone decoding |
| 3 | `installment_payment_state` | [mr521.INSTALLMENT_PAYMENT_STATE](#enum-mr521-installment-payment-state) | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-installmentpaymentserve"></a>

### mr521.InstallmentPaymentServe

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `installment_payment_state` | [mr521.INSTALLMENT_PAYMENT_STATE](#enum-mr521-installment-payment-state) | No | Yes | Schema definition; no current standalone decoding |
| 2 | `installment_payment_start_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `installment_payment_overdue_limit_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-resvinfo"></a>

### mr521.ResvInfo

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `resv_info` | uint32 | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-resetmiddlemendeliverysetting"></a>

### mr521.ResetMiddlemenDeliverySetting

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `serve_middlemen` | [mr521.SERVE_MIDDLEMEN](#enum-mr521-serve-middlemen) | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-propertyuploadperiod"></a>

### mr521.PropertyUploadPeriod

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `display_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `display_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `runtime_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 4 | `runtime_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-pvdcchgsetting"></a>

### mr521.PvDcChgSetting

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `pv_plug_index` | [mr521.PV_PLUG_INDEX](#enum-mr521-pv-plug-index) | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `pv_chg_vol_spec` | [mr521.PV_CHG_VOL_SPEC](#enum-mr521-pv-chg-vol-spec) | No | Yes | Nested local JSON when present in a supported upload |
| 3 | `pv_chg_amp_limit` | uint32 | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-pvdcchgsettinglist"></a>

### mr521.PvDcChgSettingList

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `list_info` | [mr521.PvDcChgSetting](#message-mr521-pvdcchgsetting) | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-pvchgmaxitem"></a>

### mr521.PvChgMaxItem

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `pv_chg_vol_type` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `pv_chg_amp_max` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 3 | `pv_chg_amp_mini` | uint32 | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-pvchgmaxlist"></a>

### mr521.PvChgMaxList

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `pv_chg_max_item` | [mr521.PvChgMaxItem](#message-mr521-pvchgmaxitem) | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-timetaskitemv2"></a>

### mr521.TimeTaskItemV2

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `task_index` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `is_cfg` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 3 | `is_enable` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 4 | `conflict_flag` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 5 | `time_mode` | [mr521.TIME_TASK_MODE](#enum-mr521-time-task-mode) | No | Yes | Nested local JSON when present in a supported upload |
| 6 | `time_param` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 7 | `time_table` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 8 | `task_type` | [mr521.TIME_TASK_TYPE](#enum-mr521-time-task-type) | No | Yes | Nested local JSON when present in a supported upload |
| 9 | `task_param` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 10 | `task_param_detail` | [mr521.TimeTaskParamDetail](#message-mr521-timetaskparamdetail) | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-timetaskitemv2list"></a>

### mr521.TimeTaskItemV2List

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `time_task` | [mr521.TimeTaskItemV2](#message-mr521-timetaskitemv2) | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-statisticsrecorditem"></a>

### mr521.StatisticsRecordItem

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `statistics_object` | [mr521.STATISTICS_OBJECT](#enum-mr521-statistics-object) | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `statistics_content` | uint32 | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-displaystatisticsrecordlist"></a>

### mr521.DisplayStatisticsRecordList

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `list_info` | [mr521.StatisticsRecordItem](#message-mr521-statisticsrecorditem) | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-runtimestatisticsrecordlist"></a>

### mr521.RuntimeStatisticsRecordList

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `list_info` | [mr521.StatisticsRecordItem](#message-mr521-statisticsrecorditem) | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-errcoderecorditem"></a>

### mr521.ErrcodeRecordItem

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `errcode` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `errcode_timestamp` | uint32 | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-errcoderecordlist"></a>

### mr521.ErrcodeRecordList

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `list_info` | [mr521.ErrcodeRecordItem](#message-mr521-errcoderecorditem) | Yes | No | Nested local JSON when present in a supported upload |

<a id="message-mr521-wirelesscoordinatelist"></a>

### mr521.WirelessCoordinateList

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `connect_flag` | bool | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `dev_type` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 3 | `dev_detail` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 4 | `dev_sn` | string | No | Yes | Nested local JSON when present in a supported upload |
| 5 | `dev_firm_ver` | uint32 | No | Yes | Nested local JSON when present in a supported upload |
| 6 | `dev_err_code` | uint32 | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-timetaskparamdetail"></a>

### mr521.TimeTaskParamDetail

Nested helper reachable from display uploads; local JSON only unless explicitly mapped.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `type` | [mr521.TIME_TASK_DETAIL_TYPE](#enum-mr521-time-task-detail-type) | No | Yes | Nested local JSON when present in a supported upload |
| 2 | `val` | float | No | Yes | Nested local JSON when present in a supported upload |

<a id="message-mr521-configwrite"></a>

### mr521.ConfigWrite

Outbound control route, opt-in: only `cfg_hv_ac_out_open` and `cfg_lv_ac_out_open` are ever sent, and only while control is enabled in Settings. Every other field on this message is refused by the gate.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 3 | `cfg_power_off` | bool | No | Yes | Schema definition; no current standalone decoding |
| 4 | `cfg_power_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 5 | `reset_factory_setting` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 6 | `cfg_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 7 | `cfg_utc_timezone` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 8 | `cfg_beep` | [mr521.CfgBeep](#message-mr521-cfgbeep) | No | Yes | Schema definition; no current standalone decoding |
| 9 | `cfg_beep_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 10 | `cfg_ac_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 11 | `cfg_dc_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 12 | `cfg_screen_off_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 13 | `cfg_dev_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 14 | `cfg_lcd_light` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 15 | `cfg_hv_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 16 | `cfg_lv_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 17 | `cfg_ac_out_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 18 | `cfg_dc_12v_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 19 | `cfg_usb_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 20 | `cfg_dc_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 23 | `cfg_ac_out_always_on` | [mr521.CfgAcOutAlwaysOn](#message-mr521-cfgacoutalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 25 | `cfg_xboost_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 26 | `cfg_bypass_out_disable` | bool | No | Yes | Schema definition; no current standalone decoding |
| 30 | `cfg_bms_power_off` | bool | No | Yes | Schema definition; no current standalone decoding |
| 31 | `cfg_soc_cali` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 32 | `cfg_bms_push` | [mr521.CfgBmsPushWrite](#message-mr521-cfgbmspushwrite) | No | Yes | Schema definition; no current standalone decoding |
| 33 | `cfg_max_chg_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 34 | `cfg_min_dsg_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 39 | `set_time_task` | [mr521.SetTimeTaskWrite](#message-mr521-settimetaskwrite) | No | Yes | Schema definition; no current standalone decoding |
| 43 | `cfg_energy_backup` | [mr521.CfgEnergyBackup](#message-mr521-cfgenergybackup) | No | Yes | Schema definition; no current standalone decoding |
| 52 | `cfg_plug_in_info_pv_l_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 53 | `cfg_plug_in_info_pv_h_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 54 | `cfg_plug_in_info_ac_in_chg_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 56 | `cfg_plug_in_info_5p8_chg_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 58 | `cfg_cms_oil_self_start` | bool | No | Yes | Schema definition; no current standalone decoding |
| 59 | `cfg_cms_oil_on_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 60 | `cfg_cms_oil_off_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 61 | `cfg_llc_GFCI_flag` | bool | No | Yes | Schema definition; no current standalone decoding |
| 62 | `cfg_ac_hv_always_on` | [mr521.CfgAcHvAlwaysOn](#message-mr521-cfgachvalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 63 | `cfg_ac_lv_always_on` | [mr521.CfgAcLvAlwaysOn](#message-mr521-cfgaclvalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 65 | `cfg_tou_strategy` | [mr521.CfgTouStrategy](#message-mr521-cfgtoustrategy) | No | Yes | Schema definition; no current standalone decoding |
| 66 | `cfg_ble_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 67 | `cfg_display_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 68 | `cfg_display_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 69 | `cfg_runtime_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 70 | `cfg_runtime_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 71 | `active_display_property_full_upload` | bool | No | Yes | Schema definition; no current standalone decoding |
| 72 | `active_runtime_property_full_upload` | bool | No | Yes | Schema definition; no current standalone decoding |
| 73 | `cfg_RJ45_commc_service` | [mr521.CfgRj45CommcService](#message-mr521-cfgrj45commcservice) | No | Yes | Schema definition; no current standalone decoding |
| 76 | `cfg_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 77 | `cfg_generator_perf_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 78 | `cfg_generator_engine_open` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 79 | `cfg_generator_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 80 | `cfg_generator_ac_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 81 | `cfg_generator_dc_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 82 | `cfg_generator_self_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 83 | `reset_generator_maintence_state` | bool | No | Yes | Schema definition; no current standalone decoding |
| 84 | `cfg_fuels_liquefied_gas_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 85 | `cfg_fuels_liquefied_gas_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 86 | `cfg_fuels_liquefied_gas_val` | float | No | Yes | Schema definition; no current standalone decoding |
| 87 | `cfg_plug_in_info_pv_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 88 | `cfg_ups_alram` | bool | No | Yes | Schema definition; no current standalone decoding |
| 89 | `cfg_led_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 90 | `cfg_pv_chg_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 91 | `cfg_low_power_alarm` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 93 | `cfg_storm_pattern` | [mr521.CfgStormPattern](#message-mr521-cfgstormpattern) | No | Yes | Schema definition; no current standalone decoding |
| 95 | `cfg_generator_mppt_hybrid_mode` | [mr521.CfgGeneratorMpptHybridMode](#message-mr521-cfggeneratormppthybridmode) | No | Yes | Schema definition; no current standalone decoding |
| 97 | `cfg_generator_care_mode` | [mr521.CfgGeneratorCareMode](#message-mr521-cfggeneratorcaremode) | No | Yes | Schema definition; no current standalone decoding |
| 99 | `cfg_ac_energy_saving_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 100 | `cfg_multi_bp_chg_dsg_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 102 | `cfg_backup_reverse_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 106 | `cfg_energy_strategy_operate_mode` | [mr521.CfgEnergyStrategyOperateMode](#message-mr521-cfgenergystrategyoperatemode) | No | Yes | Schema definition; no current standalone decoding |
| 116 | `cfg_sp_charger_chg_mode` | [mr521.SP_CHARGER_CHG_MODE](#enum-mr521-sp-charger-chg-mode) | No | Yes | Schema definition; no current standalone decoding |
| 119 | `cfg_installment_payment_serve_enable` | bool | No | Yes | Schema definition; no current standalone decoding |
| 120 | `cfg_installment_payment_serve_belone` | [mr521.InstallmentPaymentServeBelone](#message-mr521-installmentpaymentservebelone) | No | Yes | Schema definition; no current standalone decoding |
| 121 | `cfg_installment_payment_serve` | [mr521.InstallmentPaymentServe](#message-mr521-installmentpaymentserve) | No | Yes | Schema definition; no current standalone decoding |
| 122 | `cfg_sp_charger_chg_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 123 | `cfg_sp_charger_chg_pow_limit` | float | No | Yes | Schema definition; no current standalone decoding |
| 124 | `reset_middlemen_delivery_setting` | [mr521.ResetMiddlemenDeliverySetting](#message-mr521-resetmiddlemendeliverysetting) | No | Yes | Schema definition; no current standalone decoding |
| 125 | `cfg_ac_in_chg_mode` | [mr521.AC_IN_CHG_MODE](#enum-mr521-ac-in-chg-mode) | No | Yes | Schema definition; no current standalone decoding |
| 126 | `cfg_pv_dc_chg_setting` | [mr521.PvDcChgSetting](#message-mr521-pvdcchgsetting) | No | Yes | Schema definition; no current standalone decoding |
| 127 | `cfg_time_task_v2_item` | [mr521.TimeTaskItemV2](#message-mr521-timetaskitemv2) | No | Yes | Schema definition; no current standalone decoding |
| 128 | `active_selected_time_task_v2` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 130 | `cfg_generator_low_power_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 131 | `cfg_generator_low_power_threshold` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 132 | `cfg_generator_lpg_monitor_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 133 | `cfg_fuels_liquefied_gas_lpg_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 134 | `cfg_fuels_liquefied_gas_lng_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 135 | `cfg_utc_timezone_id` | string | No | Yes | Schema definition; no current standalone decoding |
| 136 | `cfg_utc_set_mode` | bool | No | Yes | Schema definition; no current standalone decoding |
| 137 | `cfg_sp_charger_car_batt_vol_setting` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 138 | `cfg_wireless_oil_self_start` | bool | No | Yes | Schema definition; no current standalone decoding |
| 139 | `cfg_wireless_oil_on_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 140 | `cfg_wireless_oil_off_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 141 | `cfg_output_power_off_memory` | bool | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-configwriteack"></a>

### mr521.ConfigWriteAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `action_id` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `config_ok` | bool | No | Yes | Schema definition; no current standalone decoding |
| 3 | `cfg_power_off` | bool | No | Yes | Schema definition; no current standalone decoding |
| 4 | `cfg_power_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 5 | `reset_factory_setting` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 6 | `cfg_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 7 | `cfg_utc_timezone` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 8 | `cfg_beep` | [mr521.CfgBeep](#message-mr521-cfgbeep) | No | Yes | Schema definition; no current standalone decoding |
| 9 | `cfg_beep_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 10 | `cfg_ac_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 11 | `cfg_dc_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 12 | `cfg_screen_off_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 13 | `cfg_dev_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 14 | `cfg_lcd_light` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 15 | `cfg_hv_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 16 | `cfg_lv_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 17 | `cfg_ac_out_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 18 | `cfg_dc_12v_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 19 | `cfg_usb_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 20 | `cfg_dc_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 23 | `cfg_ac_out_always_on` | [mr521.CfgAcOutAlwaysOn](#message-mr521-cfgacoutalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 25 | `cfg_xboost_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 26 | `cfg_bypass_out_disable` | bool | No | Yes | Schema definition; no current standalone decoding |
| 30 | `cfg_bms_power_off` | [mr521.CfgBmsPowerOffWriteAck](#message-mr521-cfgbmspoweroffwriteack) | No | Yes | Schema definition; no current standalone decoding |
| 31 | `cfg_soc_cali` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 32 | `cfg_bms_push` | [mr521.CfgBmsPushWriteAck](#message-mr521-cfgbmspushwriteack) | No | Yes | Schema definition; no current standalone decoding |
| 33 | `cfg_max_chg_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 34 | `cfg_min_dsg_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 39 | `set_time_task` | [mr521.SetTimeTaskWriteAck](#message-mr521-settimetaskwriteack) | No | Yes | Schema definition; no current standalone decoding |
| 43 | `cfg_energy_backup` | [mr521.CfgEnergyBackup](#message-mr521-cfgenergybackup) | No | Yes | Schema definition; no current standalone decoding |
| 52 | `cfg_plug_in_info_pv_l_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 53 | `cfg_plug_in_info_pv_h_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 54 | `cfg_plug_in_info_ac_in_chg_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 56 | `cfg_plug_in_info_5p8_chg_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 58 | `cfg_cms_oil_self_start` | bool | No | Yes | Schema definition; no current standalone decoding |
| 59 | `cfg_cms_oil_on_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 60 | `cfg_cms_oil_off_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 61 | `cfg_llc_GFCI_flag` | bool | No | Yes | Schema definition; no current standalone decoding |
| 62 | `cfg_ac_hv_always_on` | [mr521.CfgAcHvAlwaysOn](#message-mr521-cfgachvalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 63 | `cfg_ac_lv_always_on` | [mr521.CfgAcLvAlwaysOn](#message-mr521-cfgaclvalwayson) | No | Yes | Schema definition; no current standalone decoding |
| 65 | `cfg_tou_strategy` | [mr521.CfgTouStrategy](#message-mr521-cfgtoustrategy) | No | Yes | Schema definition; no current standalone decoding |
| 66 | `cfg_ble_standby_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 67 | `cfg_display_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 68 | `cfg_display_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 69 | `cfg_runtime_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 70 | `cfg_runtime_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 71 | `active_display_property_full_upload` | bool | No | Yes | Schema definition; no current standalone decoding |
| 72 | `active_runtime_property_full_upload` | bool | No | Yes | Schema definition; no current standalone decoding |
| 73 | `cfg_RJ45_commc_service` | [mr521.CfgRj45CommcService](#message-mr521-cfgrj45commcservice) | No | Yes | Schema definition; no current standalone decoding |
| 76 | `cfg_ac_out_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 77 | `cfg_generator_perf_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 78 | `cfg_generator_engine_open` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 79 | `cfg_generator_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 80 | `cfg_generator_ac_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 81 | `cfg_generator_dc_out_pow_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 82 | `cfg_generator_self_on` | bool | No | Yes | Schema definition; no current standalone decoding |
| 83 | `reset_generator_maintence_state` | bool | No | Yes | Schema definition; no current standalone decoding |
| 84 | `cfg_fuels_liquefied_gas_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 85 | `cfg_fuels_liquefied_gas_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 86 | `cfg_fuels_liquefied_gas_val` | float | No | Yes | Schema definition; no current standalone decoding |
| 87 | `cfg_plug_in_info_pv_dc_amp_max` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 88 | `cfg_ups_alram` | bool | No | Yes | Schema definition; no current standalone decoding |
| 89 | `cfg_led_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 90 | `cfg_pv_chg_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 91 | `cfg_low_power_alarm` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 93 | `cfg_storm_pattern` | [mr521.CfgStormPattern](#message-mr521-cfgstormpattern) | No | Yes | Schema definition; no current standalone decoding |
| 95 | `cfg_generator_mppt_hybrid_mode` | [mr521.CfgGeneratorMpptHybridMode](#message-mr521-cfggeneratormppthybridmode) | No | Yes | Schema definition; no current standalone decoding |
| 97 | `cfg_generator_care_mode` | [mr521.CfgGeneratorCareMode](#message-mr521-cfggeneratorcaremode) | No | Yes | Schema definition; no current standalone decoding |
| 99 | `cfg_ac_energy_saving_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 100 | `cfg_multi_bp_chg_dsg_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 102 | `cfg_backup_reverse_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 106 | `cfg_energy_strategy_operate_mode` | [mr521.CfgEnergyStrategyOperateMode](#message-mr521-cfgenergystrategyoperatemode) | No | Yes | Schema definition; no current standalone decoding |
| 116 | `cfg_sp_charger_chg_mode` | [mr521.SP_CHARGER_CHG_MODE](#enum-mr521-sp-charger-chg-mode) | No | Yes | Schema definition; no current standalone decoding |
| 119 | `cfg_installment_payment_serve_enable` | bool | No | Yes | Schema definition; no current standalone decoding |
| 120 | `cfg_installment_payment_serve_belone` | [mr521.InstallmentPaymentServeBelone](#message-mr521-installmentpaymentservebelone) | No | Yes | Schema definition; no current standalone decoding |
| 121 | `cfg_installment_payment_serve` | [mr521.InstallmentPaymentServe](#message-mr521-installmentpaymentserve) | No | Yes | Schema definition; no current standalone decoding |
| 122 | `cfg_sp_charger_chg_open` | bool | No | Yes | Schema definition; no current standalone decoding |
| 123 | `cfg_sp_charger_chg_pow_limit` | float | No | Yes | Schema definition; no current standalone decoding |
| 124 | `reset_middlemen_delivery_setting` | [mr521.ResetMiddlemenDeliverySetting](#message-mr521-resetmiddlemendeliverysetting) | No | Yes | Schema definition; no current standalone decoding |
| 125 | `cfg_ac_in_chg_mode` | [mr521.AC_IN_CHG_MODE](#enum-mr521-ac-in-chg-mode) | No | Yes | Schema definition; no current standalone decoding |
| 126 | `cfg_pv_dc_chg_setting` | [mr521.PvDcChgSetting](#message-mr521-pvdcchgsetting) | No | Yes | Schema definition; no current standalone decoding |
| 127 | `cfg_time_task_v2_item` | [mr521.TimeTaskItemV2](#message-mr521-timetaskitemv2) | No | Yes | Schema definition; no current standalone decoding |
| 128 | `active_selected_time_task_v2` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 130 | `cfg_generator_low_power_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 131 | `cfg_generator_low_power_threshold` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 132 | `cfg_generator_lpg_monitor_en` | bool | No | Yes | Schema definition; no current standalone decoding |
| 133 | `cfg_fuels_liquefied_gas_lpg_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 134 | `cfg_fuels_liquefied_gas_lng_uint` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 135 | `cfg_utc_timezone_id` | string | No | Yes | Schema definition; no current standalone decoding |
| 136 | `cfg_utc_set_mode` | bool | No | Yes | Schema definition; no current standalone decoding |
| 137 | `cfg_sp_charger_car_batt_vol_setting` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 138 | `cfg_wireless_oil_self_start` | bool | No | Yes | Schema definition; no current standalone decoding |
| 139 | `cfg_wireless_oil_on_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 140 | `cfg_wireless_oil_off_soc` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 141 | `cfg_output_power_off_memory` | bool | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-configread"></a>

### mr521.ConfigRead

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `action_id` | uint32 | Yes | No | Schema definition; no current standalone decoding |

<a id="message-mr521-configreadack"></a>

### mr521.ConfigReadAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 6 | `cfg_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 7 | `cfg_utc_timezone` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 41 | `get_time_task_list` | [mr521.GetAllTimeTaskReadck](#message-mr521-getalltimetaskreadck) | No | Yes | Schema definition; no current standalone decoding |
| 129 | `read_time_task_v2_list` | [mr521.TimeTaskItemV2List](#message-mr521-timetaskitemv2list) | No | Yes | Schema definition; no current standalone decoding |
| 144 | `get_pd_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 145 | `get_iot_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 146 | `get_mppt_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 147 | `get_llc_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 148 | `get_inv_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 149 | `get_bms_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-displaypropertyupload"></a>

### mr521.DisplayPropertyUpload

Supported display route; selected fields normalized, other present known fields kept locally.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `errcode` | uint32 | No | Yes | Normalized numeric field |
| 2 | `sys_status` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 130 | `dev_online_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 133 | `utc_timezone` | int32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 134 | `utc_timezone_id` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 135 | `utc_set_mode` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 275 | `cms_bms_run_state` | uint32 | No | Yes | Normalized numeric field |
| 262 | `cms_batt_soc` | float | No | Yes | Normalized numeric field |
| 263 | `cms_batt_soh` | float | No | Yes | Normalized numeric field |
| 282 | `cms_chg_dsg_state` | uint32 | No | Yes | Normalized numeric field |
| 287 | `cms_batt_full_cap` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 288 | `cms_batt_design_cap` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 289 | `cms_batt_remain_cap` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 462 | `cms_batt_full_energy` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 268 | `cms_dsg_rem_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 269 | `cms_chg_rem_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 459 | `cms_batt_pow_out_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 460 | `cms_batt_pow_in_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 102 | `cms_batt_temp` | int32 | No | Yes | Normalized numeric field |
| 270 | `cms_max_chg_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 271 | `cms_min_dsg_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 242 | `bms_batt_soc` | float | No | Yes | Normalized numeric field |
| 243 | `bms_batt_soh` | float | No | Yes | Normalized numeric field |
| 281 | `bms_chg_dsg_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 248 | `bms_design_cap` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 254 | `bms_dsg_rem_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 255 | `bms_chg_rem_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 258 | `bms_min_cell_temp` | int32 | No | Yes | Normalized numeric field |
| 259 | `bms_max_cell_temp` | int32 | No | Yes | Normalized numeric field |
| 260 | `bms_min_mos_temp` | int32 | No | Yes | Normalized numeric field |
| 261 | `bms_max_mos_temp` | int32 | No | Yes | Normalized numeric field |
| 212 | `dev_sleep_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 195 | `en_beep` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 5 | `lcd_light` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 298 | `ac_out_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 72 | `btn_ac_out_lv_switch` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 73 | `btn_ac_out_hv_switch` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 74 | `dc_out_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 75 | `btn_dc_12v_out_switch` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 76 | `btn_usb_switch` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 17 | `dev_standby_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 18 | `screen_off_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 19 | `ac_standby_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 20 | `dc_standby_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 290 | `ble_standby_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 355 | `ups_alram` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 357 | `led_mode` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 358 | `low_power_alarm` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 359 | `silence_chg_watt` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 211 | `ac_out_freq` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 227 | `llc_hv_lv_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 22 | `ac_always_on_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 283 | `ac_hv_always_on` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 284 | `ac_lv_always_on` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 23 | `ac_always_on_mini_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 25 | `xboost_en` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 147 | `output_power_off_memory` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 182 | `fast_charge_switch` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 200 | `llc_GFCI_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 455 | `ac_energy_saving_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 146 | `bypass_out_disable` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 125 | `pv_dc_chg_setting_list` | [mr521.PvDcChgSettingList](#message-mr521-pvdcchgsettinglist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 148 | `pv_chg_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 6 | `energy_backup_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 8 | `energy_backup_start_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 7 | `energy_backup_en` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 467 | `storm_pattern_enable` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 468 | `storm_pattern_open_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 469 | `storm_pattern_end_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 219 | `time_task_current` | [mr521.SetTimeTaskWrite](#message-mr521-settimetaskwrite) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 126 | `current_time_task_v2_item` | [mr521.TimeTaskItemV2](#message-mr521-timetaskitemv2) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 285 | `time_task_conflict_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 286 | `time_task_change_cnt` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 274 | `cms_oil_self_start` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 272 | `cms_oil_on_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 273 | `cms_oil_off_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 143 | `wireless_oil_self_start` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 144 | `wireless_oil_on_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 145 | `wireless_oil_off_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 451 | `generator_pv_hybrid_mode_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 452 | `generator_pv_hybrid_mode_soc_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 453 | `generator_care_mode_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 454 | `generator_care_mode_start_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 456 | `multi_bp_chg_dsg_mode` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 3 | `pow_in_sum_w` | float | No | Yes | Normalized numeric field |
| 4 | `pow_out_sum_w` | float | No | Yes | Normalized numeric field |
| 9 | `pow_get_qcusb1` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 10 | `pow_get_qcusb2` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 11 | `pow_get_typec1` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 12 | `pow_get_typec2` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 35 | `pow_get_pv_h` | float | No | Yes | Normalized numeric field |
| 36 | `pow_get_pv_l` | float | No | Yes | Normalized numeric field |
| 361 | `pow_get_pv` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 70 | `pow_get_pv2` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 37 | `pow_get_12v` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 38 | `pow_get_24v` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 52 | `pow_get_llc` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 53 | `pow_get_ac` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 54 | `pow_get_ac_in` | float | No | Yes | Normalized numeric field |
| 368 | `pow_get_ac_out` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 55 | `pow_get_ac_hv_out` | float | No | Yes | Normalized numeric field |
| 56 | `pow_get_ac_lv_out` | float | No | Yes | Normalized numeric field |
| 57 | `pow_get_ac_lv_tt30_out` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 58 | `pow_get_5p8` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 297 | `pow_get_dc` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 158 | `pow_get_bms` | float | No | Yes | Normalized numeric field |
| 159 | `pow_get_4p8_1` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 160 | `pow_get_4p8_2` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 425 | `pow_get_dcp` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 77 | `pow_get_dcp2` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 105 | `pow_get_dc_bidi` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 463 | `display_statistics_sum` | [mr521.DisplayStatisticsRecordList](#message-mr521-displaystatisticsrecordlist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 13 | `flow_info_qcusb1` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 14 | `flow_info_qcusb2` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 15 | `flow_info_typec1` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 16 | `flow_info_typec2` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 31 | `flow_info_pv_h` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 32 | `flow_info_pv_l` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 360 | `flow_info_pv` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 414 | `flow_info_pv2` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 33 | `flow_info_12v` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 34 | `flow_info_24v` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 45 | `flow_info_ac2dc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 46 | `flow_info_dc2ac` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 47 | `flow_info_ac_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 48 | `flow_info_ac_hv_out` | uint32 | No | Yes | Normalized numeric field |
| 49 | `flow_info_ac_lv_out` | uint32 | No | Yes | Normalized numeric field |
| 50 | `flow_info_5p8_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 51 | `flow_info_5p8_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 367 | `flow_info_ac_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 152 | `flow_info_bms_dsg` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 153 | `flow_info_bms_chg` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 154 | `flow_info_4p8_1_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 155 | `flow_info_4p8_1_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 156 | `flow_info_4p8_2_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 157 | `flow_info_4p8_2_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 423 | `flow_info_dcp_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 424 | `flow_info_dcp_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 78 | `flow_info_dcp2_in` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 79 | `flow_info_dcp2_out` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 39 | `plug_in_info_pv_h_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 40 | `plug_in_info_pv_h_type` | uint32 | No | Yes | Normalized numeric field |
| 171 | `plug_in_info_pv_h_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 181 | `plug_in_info_pv_h_dc_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 236 | `plug_in_info_pv_h_chg_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 233 | `plug_in_info_pv_h_chg_vol_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 42 | `plug_in_info_pv_l_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 43 | `plug_in_info_pv_l_type` | uint32 | No | Yes | Normalized numeric field |
| 170 | `plug_in_info_pv_l_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 180 | `plug_in_info_pv_l_dc_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 235 | `plug_in_info_pv_l_chg_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 234 | `plug_in_info_pv_l_chg_vol_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 362 | `plug_in_info_pv_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 363 | `plug_in_info_pv_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 364 | `plug_in_info_pv_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 356 | `plug_in_info_pv_dc_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 365 | `plug_in_info_pv_chg_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 366 | `plug_in_info_pv_chg_vol_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 136 | `plug_in_info_pv_chg_max_list` | [mr521.PvChgMaxList](#message-mr521-pvchgmaxlist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 421 | `plug_in_info_pv2_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 422 | `plug_in_info_pv2_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 430 | `plug_in_info_pv2_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 80 | `plug_in_info_pv2_dc_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 81 | `plug_in_info_pv2_chg_amp_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 82 | `plug_in_info_pv2_chg_vol_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 137 | `plug_in_info_pv2_chg_max_list` | [mr521.PvChgMaxList](#message-mr521-pvchgmaxlist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 61 | `plug_in_info_ac_in_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 202 | `plug_in_info_ac_charger_flag` | bool | No | Yes | Normalized numeric field |
| 62 | `plug_in_info_ac_in_feq` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 209 | `plug_in_info_ac_in_chg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 458 | `plug_in_info_ac_in_chg_hal_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 124 | `plug_in_info_ac_in_chg_mode` | [mr521.AC_IN_CHG_MODE](#enum-mr521-ac-in-chg-mode) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 238 | `plug_in_info_ac_out_dsg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 210 | `plug_in_info_5p8_chg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 457 | `plug_in_info_5p8_chg_hal_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 237 | `plug_in_info_5p8_dsg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 63 | `plug_in_info_5p8_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 191 | `plug_in_info_5p8_dsg_chg` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 203 | `plug_in_info_5p8_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 64 | `plug_in_info_5p8_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 65 | `plug_in_info_5p8_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 193 | `plug_in_info_5p8_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 204 | `plug_in_info_5p8_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 216 | `plug_in_info_5p8_err_code` | uint32 | No | Yes | Normalized numeric field |
| 194 | `plug_in_info_5p8_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 192 | `plug_in_info_5p8_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 107 | `plug_in_info_acp_chg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 108 | `plug_in_info_acp_chg_hal_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 109 | `plug_in_info_acp_dsg_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 110 | `plug_in_info_acp_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 111 | `plug_in_info_acp_dsg_chg` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 112 | `plug_in_info_acp_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 113 | `plug_in_info_acp_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 114 | `plug_in_info_acp_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 115 | `plug_in_info_acp_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 116 | `plug_in_info_acp_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 117 | `plug_in_info_acp_err_code` | uint32 | No | Yes | Normalized numeric field |
| 118 | `plug_in_info_acp_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 119 | `plug_in_info_acp_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 106 | `plug_in_info_dc_bidi_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 426 | `plug_in_info_dcp_in_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 431 | `plug_in_info_dcp_dsg_chg_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 435 | `plug_in_info_dcp_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 427 | `plug_in_info_dcp_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 428 | `plug_in_info_dcp_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 433 | `plug_in_info_dcp_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 436 | `plug_in_info_dcp_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 434 | `plug_in_info_dcp_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 432 | `plug_in_info_dcp_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 83 | `plug_in_info_dcp2_in_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 84 | `plug_in_info_dcp2_dsg_chg_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 85 | `plug_in_info_dcp2_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 86 | `plug_in_info_dcp2_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 87 | `plug_in_info_dcp2_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 88 | `plug_in_info_dcp2_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 89 | `plug_in_info_dcp2_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 90 | `plug_in_info_dcp2_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 91 | `plug_in_info_dcp2_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 161 | `plug_in_info_4p8_1_in_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 183 | `plug_in_info_4p8_1_dsg_chg_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 205 | `plug_in_info_4p8_1_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 162 | `plug_in_info_4p8_1_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 163 | `plug_in_info_4p8_1_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 185 | `plug_in_info_4p8_1_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 206 | `plug_in_info_4p8_1_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 217 | `plug_in_info_4p8_1_err_code` | uint32 | No | Yes | Normalized numeric field |
| 186 | `plug_in_info_4p8_1_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 184 | `plug_in_info_4p8_1_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 164 | `plug_in_info_4p8_2_in_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 187 | `plug_in_info_4p8_2_dsg_chg_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 207 | `plug_in_info_4p8_2_charger_flag` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 165 | `plug_in_info_4p8_2_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 166 | `plug_in_info_4p8_2_detail` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 189 | `plug_in_info_4p8_2_sn` | string | No | Yes | Local decoded JSON when present; not normalized/exported |
| 208 | `plug_in_info_4p8_2_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 218 | `plug_in_info_4p8_2_err_code` | uint32 | No | Yes | Normalized numeric field |
| 190 | `plug_in_info_4p8_2_firm_ver` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 188 | `plug_in_info_4p8_2_resv` | [mr521.ResvInfo](#message-mr521-resvinfo) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 142 | `wireless_coordinate_dev_list` | [mr521.WirelessCoordinateList](#message-mr521-wirelesscoordinatelist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 213 | `pd_err_code` | uint32 | No | Yes | Normalized numeric field |
| 215 | `mppt_err_code` | uint32 | No | Yes | Normalized numeric field |
| 214 | `llc_err_code` | uint32 | No | Yes | Normalized numeric field |
| 232 | `llc_inv_err_code` | uint32 | No | Yes | Normalized numeric field |
| 437 | `dcdc_err_code` | uint32 | No | Yes | Normalized numeric field |
| 438 | `plug_in_info_dcp_err_code` | uint32 | No | Yes | Normalized numeric field |
| 439 | `plug_in_info_dcp2_err_code` | uint32 | No | Yes | Normalized numeric field |
| 140 | `bms_err_code` | uint32 | No | Yes | Normalized numeric field |
| 450 | `inv_err_code` | uint32 | No | Yes | Normalized numeric field |
| 141 | `err_code_record_list` | [mr521.ErrcodeRecordList](#message-mr521-errcoderecordlist) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 30 | `pcs_fan_level` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 226 | `pcs_fan_err_flag` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 299 | `generator_fuels_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 300 | `generator_remain_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 301 | `generator_run_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 302 | `generator_total_output` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 303 | `generator_abnormal_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 304 | `fuels_oil_val` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 305 | `fuels_liquefied_gas_type` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 306 | `fuels_liquefied_gas_uint` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 307 | `fuels_liquefied_gas_val` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 308 | `fuels_liquefied_gas_consume_per_hour` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 309 | `fuels_liquefied_gas_remain_val` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 131 | `fuels_liquefied_gas_lpg_uint` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 132 | `fuels_liquefied_gas_lng_uint` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 310 | `generator_perf_mode` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 311 | `generator_engine_open` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 127 | `generator_low_power_en` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 128 | `generator_low_power_threshold` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 129 | `generator_lpg_monitor_en` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 312 | `generator_out_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 313 | `generator_ac_out_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 314 | `generator_dc_out_pow_max` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 315 | `generator_sub_battery_temp` | int32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 316 | `generator_sub_battery_soc` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 317 | `generator_sub_battery_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 319 | `generator_maintence_state` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 330 | `generator_pcs_err_code` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 123 | `generator_conn_dev_errcode` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 581 | `sp_charger_chg_mode` | [mr521.SP_CHARGER_CHG_MODE](#enum-mr521-sp-charger-chg-mode) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 597 | `sp_charger_chg_open` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 598 | `sp_charger_chg_pow_limit` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 603 | `sp_charger_chg_pow_max` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 582 | `sp_charger_run_state` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 583 | `sp_charger_is_connect_car` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 138 | `sp_charger_car_batt_vol_setting` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 139 | `sp_charger_car_batt_vol` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 599 | `module_bluetooth_snr` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 600 | `module_bluetooth_rssi` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 601 | `module_wifi_snr` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 602 | `module_wifi_rssi` | float | No | Yes | Local decoded JSON when present; not normalized/exported |
| 591 | `installment_payment_serve_enable` | bool | No | Yes | Local decoded JSON when present; not normalized/exported |
| 592 | `serve_middlemen` | [mr521.SERVE_MIDDLEMEN](#enum-mr521-serve-middlemen) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 593 | `installment_payment_overdue_limit` | [mr521.INSTALLMENT_PAYMENT_OVERDUE_LIMIT](#enum-mr521-installment-payment-overdue-limit) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 594 | `installment_payment_state` | [mr521.INSTALLMENT_PAYMENT_STATE](#enum-mr521-installment-payment-state) | No | Yes | Local decoded JSON when present; not normalized/exported |
| 595 | `installment_payment_start_utc_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |
| 596 | `installment_payment_overdue_limit_utc_time` | uint32 | No | Yes | Local decoded JSON when present; not normalized/exported |

<a id="message-mr521-runtimepropertyupload"></a>

### mr521.RuntimePropertyUpload

Runtime schema only; no implemented runtime-property route.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 293 | `display_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 294 | `display_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 295 | `runtime_property_full_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 296 | `runtime_property_incremental_upload_period` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 264 | `cms_batt_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 265 | `cms_batt_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 266 | `cms_chg_req_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 267 | `cms_chg_req_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 241 | `bms_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 244 | `bms_batt_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 245 | `bms_batt_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 246 | `bms_bal_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 247 | `bms_full_cap` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 249 | `bms_remain_cap` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 250 | `bms_alm_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 291 | `bms_alm_state_2` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 251 | `bms_pro_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 292 | `bms_pro_state_2` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 252 | `bms_flt_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 253 | `bms_err_code` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 256 | `bms_min_cell_vol` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 257 | `bms_max_cell_vol` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 21 | `ac_phase_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 24 | `pcs_work_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 464 | `runtime_statistics_sum` | [mr521.RuntimeStatisticsRecordList](#message-mr521-runtimestatisticsrecordlist) | No | Yes | Schema definition; no current standalone decoding |
| 41 | `plug_in_info_pv_h_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 221 | `plug_in_info_pv_h_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 44 | `plug_in_info_pv_l_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 222 | `plug_in_info_pv_l_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 380 | `plug_in_info_pv_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 381 | `plug_in_info_pv_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 442 | `plug_in_info_pv2_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 71 | `plug_in_info_pv2_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 68 | `plug_in_info_ac_in_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 223 | `plug_in_info_ac_in_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 59 | `plug_in_info_ac_out_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 60 | `plug_in_info_ac_out_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 67 | `plug_in_info_ac_out_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 224 | `plug_in_info_ac_out_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 69 | `plug_in_info_5p8_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 225 | `plug_in_info_5p8_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 66 | `plug_in_info_5p8_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 120 | `plug_in_info_acp_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 121 | `plug_in_info_acp_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 122 | `plug_in_info_acp_freq` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 382 | `plug_in_info_12v_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 383 | `plug_in_info_12v_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 169 | `plug_in_info_bms_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 103 | `plug_in_info_dc_bidi_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 104 | `plug_in_info_dc_bidi_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 443 | `plug_in_info_dcp_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 448 | `plug_in_info_dcp_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 92 | `plug_in_info_dcp2_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 93 | `plug_in_info_dcp2_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 167 | `plug_in_info_4p8_1_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 239 | `plug_in_info_4p8_1_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 168 | `plug_in_info_4p8_2_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 240 | `plug_in_info_4p8_2_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 172 | `pd_mppt_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 173 | `pd_llc_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 174 | `pd_bms_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 175 | `pd_iot_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 444 | `pd_dcdc_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 445 | `pd_inv_comm_err` | bool | No | Yes | Schema definition; no current standalone decoding |
| 176 | `pd_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 177 | `iot_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 178 | `mppt_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 179 | `llc_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 231 | `llc_inv_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 446 | `dcdc_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 447 | `inv_firm_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 26 | `temp_pcs_dc` | float | No | Yes | Schema definition; no current standalone decoding |
| 27 | `temp_pcs_ac` | float | No | Yes | Schema definition; no current standalone decoding |
| 28 | `temp_pv_h` | float | No | Yes | Schema definition; no current standalone decoding |
| 29 | `temp_pv_l` | float | No | Yes | Schema definition; no current standalone decoding |
| 379 | `temp_pv` | float | No | Yes | Schema definition; no current standalone decoding |
| 440 | `temp_pv2` | float | No | Yes | Schema definition; no current standalone decoding |
| 276 | `bms_overload_icon` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 277 | `bms_warn_icon` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 278 | `bms_high_temp_icon` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 279 | `bms_low_temp_icon` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 280 | `bms_limit_icon` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 320 | `generator_run_state` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 321 | `generator_oil_val_real` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 322 | `generator_engine_spd` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 323 | `generator_engine_oil_val` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 324 | `generator_engine_activation_cnt` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 325 | `generator_engine_head_temp` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 326 | `generator_sub_battery_vol` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 327 | `generator_sub_battery_amp` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 328 | `generator_commc_cnt_ems_to_psdr` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 329 | `generator_commc_cnt_psdr_to_ems` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 331 | `generator_pcs_fan_level` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 332 | `generator_pcs_bus_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 333 | `generator_pcs_bus_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 334 | `generator_pcs_temp1` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 335 | `generator_pcs_temp2` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 336 | `generator_pcs_fules_type` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 149 | `plug_in_info_24v_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 150 | `plug_in_info_24v_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 151 | `plug_in_info_l1_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 196 | `plug_in_info_l1_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 197 | `plug_in_info_l2_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 198 | `plug_in_info_l2_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 199 | `plug_in_info_acp_l1_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 201 | `plug_in_info_acp_l1l2_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 220 | `mppt_monitor_flag` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 318 | `mppt_recv_cms_chg_req_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 337 | `mppt_recv_cms_chg_req_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 338 | `pv_vin_ref` | float | No | Yes | Schema definition; no current standalone decoding |
| 339 | `pv2_vin_ref` | float | No | Yes | Schema definition; no current standalone decoding |
| 340 | `pv2_bus_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 341 | `mppt_bat_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 342 | `mppt_bat_amp` | float | No | Yes | Schema definition; no current standalone decoding |
| 343 | `pv_pause_cnt` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 344 | `pv2_pause_cnt` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 345 | `mppt_fanspeed` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 346 | `ads_ntc_temp` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 347 | `mppt_hardware_ver` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 348 | `inv_monitor_flag` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 349 | `inv_main_fsmstate` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 350 | `l1_main_fsmstate` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 351 | `l2_main_fsmstate` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 352 | `plug_in_info_pfc_out_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 353 | `pow_get_l1` | float | No | Yes | Schema definition; no current standalone decoding |
| 354 | `pow_get_l2` | float | No | Yes | Schema definition; no current standalone decoding |
| 369 | `inv_bus_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 370 | `inv_ntc_temp2` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 371 | `inv_ntc_temp3` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 372 | `llc_monitor_flag` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 373 | `llc_ntc_temp` | int32 | No | Yes | Schema definition; no current standalone decoding |
| 374 | `llc_fsmstate` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 375 | `pd_to_inv_dsg_mode` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 376 | `dcdc_chg_req_cur` | float | No | Yes | Schema definition; no current standalone decoding |
| 377 | `llc_recv_cms_chg_req_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 378 | `inv_to_llc_ac_pow_lim` | float | No | Yes | Schema definition; no current standalone decoding |
| 384 | `llc_bat_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 385 | `llc_bat_cur` | float | No | Yes | Schema definition; no current standalone decoding |
| 386 | `llc_bus_vol` | float | No | Yes | Schema definition; no current standalone decoding |
| 387 | `pd_skt_ocp1` | float | No | Yes | Schema definition; no current standalone decoding |
| 388 | `pd_skt_ocp2` | float | No | Yes | Schema definition; no current standalone decoding |
| 389 | `pd_skt_ocp3` | float | No | Yes | Schema definition; no current standalone decoding |
| 390 | `pd_skt_ocp4` | float | No | Yes | Schema definition; no current standalone decoding |
| 391 | `pd_skt_ocp5` | float | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-devrequest"></a>

### mr521.DevRequest

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `dev_utc_time` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `dev_utc_timezone` | float | No | Yes | Schema definition; no current standalone decoding |
| 3 | `require_property_upload_period` | bool | No | Yes | Schema definition; no current standalone decoding |
| 4 | `require_tou_strategy` | [mr521.ReqTouStrategy](#message-mr521-reqtoustrategy) | No | Yes | Schema definition; no current standalone decoding |

<a id="message-mr521-devrequestack"></a>

### mr521.DevRequestAck

Schema only outside supported display nesting; no standalone route/request/acknowledgement implemented.

| Tag | Field | Protobuf type | Repeated | Presence | OpenPowerstation use |
|---:|---|---|:---:|:---:|---|
| 1 | `request_id` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 2 | `require_ok` | uint32 | No | Yes | Schema definition; no current standalone decoding |
| 3 | `property_upload_period` | [mr521.PropertyUploadPeriod](#message-mr521-propertyuploadperiod) | No | Yes | Schema definition; no current standalone decoding |

## All enum definitions

The names/numbers below are preserved exactly, including upstream spelling.
An enum belongs only to fields that reference its type; it is not a generic device-error dictionary.
Unrecognized numeric values must not be silently assigned the closest known meaning.

<a id="enum-mr521-module-type"></a>

### mr521.MODULE_TYPE

| Value | Schema name |
|---:|---|
| 0 | `MODULE_TYPE_EF` |

<a id="enum-mr521-sp-charger-chg-mode"></a>

### mr521.SP_CHARGER_CHG_MODE

| Value | Schema name |
|---:|---|
| 0 | `SP_CHARGER_CHG_MODE_IDLE` |
| 1 | `SP_CHARGER_CHG_MODE_DRIVING_CHG` |
| 2 | `SP_CHARGER_CHG_MODE_BAT_MAINTENANCE` |
| 3 | `SP_CHARGER_CHG_MODE_PARKING_CHG` |

<a id="enum-mr521-serve-middlemen"></a>

### mr521.SERVE_MIDDLEMEN

| Value | Schema name |
|---:|---|
| 0 | `SERVE_MIDDLEMEN_NONE` |
| 1 | `SERVE_MIDDLEMEN_SUNNOVA` |

<a id="enum-mr521-installment-payment-state"></a>

### mr521.INSTALLMENT_PAYMENT_STATE

| Value | Schema name |
|---:|---|
| 0 | `INSTALLMENT_PAYMENT_STATE_WAIT_FOT_ACTIVATE` |
| 1 | `INSTALLMENT_PAYMENT_STATE_ACTIVATED` |
| 2 | `INSTALLMENT_PAYMENT_STATE_OVERDUE` |
| 3 | `INSTALLMENT_PAYMENT_STATE_SETTLE` |
| 4 | `INSTALLMENT_PAYMENT_STATE_MANUAL_LOCKDOWN` |

<a id="enum-mr521-installment-payment-overdue-limit"></a>

### mr521.INSTALLMENT_PAYMENT_OVERDUE_LIMIT

| Value | Schema name |
|---:|---|
| 0 | `INSTALLMENT_PAYMENT_OVERDUE_LIMIT_NONE` |
| 1 | `INSTALLMENT_PAYMENT_OVERDUE_LIMIT_CLOSE_OUTPUT` |

<a id="enum-mr521-statistics-object"></a>

### mr521.STATISTICS_OBJECT

| Value | Schema name |
|---:|---|
| 0 | `STATISTICS_OBJECT_START` |
| 1 | `STATISTICS_OBJECT_DEV_WORK_TIME` |
| 2 | `STATISTICS_OBJECT_AC_OUT_ENERGY` |
| 3 | `STATISTICS_OBJECT_DC12V_OUT_ENERGY` |
| 4 | `STATISTICS_OBJECT_TYPEC_OUT_ENERGY` |
| 5 | `STATISTICS_OBJECT_USBA_OUT_ENERGY` |
| 6 | `STATISTICS_OBJECT_AC_IN_ENERGY` |
| 7 | `STATISTICS_OBJECT_PV_IN_ENERGY` |
| 8 | `STATISTICS_OBJECT_AC_IN_0W_100W_TIME` |
| 9 | `STATISTICS_OBJECT_AC_IN_OVER_100W_TIME` |
| 10 | `STATISTICS_OBJECT_AC_OUT_0W_50W_TIME` |
| 11 | `STATISTICS_OBJECT_AC_OUT_50W_100W_TIME` |
| 12 | `STATISTICS_OBJECT_AC_OUT_100W_200W_TIME` |
| 13 | `STATISTICS_OBJECT_AC_OUT_OVER_200W_TIME` |
| 14 | `STATISTICS_OBJECT_AC_OUT_200W_300W_TIME` |
| 15 | `STATISTICS_OBJECT_AC_OUT_300W_400W_TIME` |
| 16 | `STATISTICS_OBJECT_AC_OUT_400W_500W_TIME` |
| 17 | `STATISTICS_OBJECT_AC_OUT_OVER_500W_TIME` |
| 18 | `STATISTICS_OBJECT_PV_IN_TIME` |
| 19 | `STATISTICS_OBJECT_TYPEC_IN_TIME` |
| 20 | `STATISTICS_OBJECT_DC_OUT_0W_60W_TIME` |
| 21 | `STATISTICS_OBJECT_DC_OUT_OVER_60W_TIME` |
| 22 | `STATISTICS_OBJECT_TYPEC_OUT_0W_30W_TIME` |
| 23 | `STATISTICS_OBJECT_TYPEC_OUT_30W_60W_TIME` |
| 24 | `STATISTICS_OBJECT_TYPEC_OUT_OVER_60W_TIME` |
| 25 | `STATISTICS_OBJECT_USBA_OUT_TIME` |
| 26 | `STATISTICS_OBJECT_LED_OUT_TIME` |

<a id="enum-mr521-ac-in-chg-mode"></a>

### mr521.AC_IN_CHG_MODE

| Value | Schema name |
|---:|---|
| 0 | `AC_IN_CHG_MODE_SELF_DEF_POW` |
| 1 | `AC_IN_CHG_MODE_BAT_OPTIMAL_POW` |
| 2 | `AC_IN_CHG_MODE_SILENCE` |

<a id="enum-mr521-pv-plug-index"></a>

### mr521.PV_PLUG_INDEX

| Value | Schema name |
|---:|---|
| 0 | `PV_PLUG_INDEX_RESV` |
| 1 | `PV_PLUG_INDEX_1` |
| 2 | `PV_PLUG_INDEX_2` |

<a id="enum-mr521-pv-chg-vol-spec"></a>

### mr521.PV_CHG_VOL_SPEC

| Value | Schema name |
|---:|---|
| 0 | `PV_CHG_VOL_SPEC_RESV` |
| 1 | `PV_CHG_VOL_SPEC_12V` |
| 2 | `PV_CHG_VOL_SPEC_24V` |
| 3 | `PV_CHG_VOL_SPEC_48V` |

<a id="enum-mr521-time-task-mode"></a>

### mr521.TIME_TASK_MODE

| Value | Schema name |
|---:|---|
| 0 | `TIME_TASK_MODE_RESV` |
| 1 | `TIME_TASK_MODE_PER_WEEK` |
| 2 | `TIME_TASK_MODE_ONCE` |

<a id="enum-mr521-time-task-type"></a>

### mr521.TIME_TASK_TYPE

| Value | Schema name |
|---:|---|
| 0 | `TIME_TASK_TYPE_AC_CHG` |
| 1 | `TIME_TASK_TYPE_AC_DSG` |
| 2 | `TIME_TASK_TYPE_AC2_DSG` |
| 3 | `TIME_TASK_TYPE_DC_CHG` |
| 4 | `TIME_TASK_TYPE_DC2_CHG` |
| 5 | `TIME_TASK_TYPE_DC_DSG` |
| 6 | `TIME_TASK_TYPE_OIL_ON` |
| 7 | `TIME_TASK_TYPE_OIL_OFF` |
| 8 | `TIME_TASK_TYPE_USB_CHG` |
| 9 | `TIME_TASK_TYPE_USB_DSG` |

<a id="enum-mr521-time-task-detail-type"></a>

### mr521.TIME_TASK_DETAIL_TYPE

| Value | Schema name |
|---:|---|
| 0 | `TIME_TASK_DETAIL_IDLE` |
| 1 | `TIME_TASK_DETAIL_POW` |
| 2 | `TIME_TASK_DETAIL_TEMP` |
| 3 | `TIME_TASK_DETAIL_LEVEL` |

## Regeneration

From the project root, with the project's dependencies installed:

```powershell
.\.venv\Scripts\python.exe scripts/generate_protocol_reference.py
.\.venv\Scripts\python.exe scripts/generate_protocol_reference.py --check
```

The generator only inspects source descriptors and the field map. It does not open configuration,
read recordings, initialize Bluetooth, authenticate, or modify the device.
