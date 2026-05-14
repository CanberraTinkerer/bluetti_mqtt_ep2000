# Bluetti Debugger Configuration Specification (`debugger.json`)

This document outlines the syntax, requirements, and conventions for the `debugger.json` file used by the Bluetti MQTT Debugger. This file serves as the "source of truth" for register mapping, data parsing, and Home Assistant discovery.

---

## 1. Basic JSON Syntax

The configuration is a **JSON Array** (enclosed in `[ ]`) containing multiple **JSON Objects** (enclosed in `{ }`).

### Common Rules:
- **Double Quotes**: All keys (e.g., `"reg"`) and string values (e.g., `"Voltage"`) must be in double quotes.
- **Commas**: Use a comma between every item in a list and every key/value pair in an object. **Never** put a comma after the last item.
- **Booleans**: Use `true` or `false` without quotes.
- **Numbers**: Use decimal numbers (e.g., `1100`) without quotes.

---

## 2. Core Field Definitions

Every basic register entry should include these fields:

| Field | Type | Description |
| :--- | :--- | :--- |
| `reg` | Integer | The decimal address of the Modbus register. |
| `name` | String | Friendly name for the entity (e.g., "Total SOC"). |
| `len` | Integer | Bit length (16 for 1 word, 32 for 2 words) or character length for ASCII. Defaults to 1. |
| `slave_id`| Integer | The Modbus Slave ID (1 = Inverter/Gateway, 41 = BMS Hub). Defaults to 1. |
| `notes` | String | Internal documentation for the developer. Not published to HA state. |

---

## 3. Data Processing & Formatting

These fields control how the raw bytes from the device are converted into human-readable numbers.

### Math Operations
- `"scale"`: Integer. The power of 10 to divide by. (`1` = /10, `2` = /100, `3` = /1000).
- `"subtract"`: Number. Value to subtract from the result (e.g., `40` for temperature offsets).
- `"signed"`: Boolean. If `true`, handles negative numbers (2's complement).
- `"absolute"`: Boolean. If `true`, applies `Math.abs()` to the result (useful for power flow).
- `"byte_swap"`: Boolean. Swaps the High/Low bytes within a 16-bit word.
- `"no_word_swap"`: Boolean. Prevents the default V2 behavior of swapping words in 32-bit values.

### Type Conversion
- `"ascii"`: Boolean. Set to `true` to treat the data as a text string (e.g., Serial Numbers).
- `"type"`: String. Can be `"enum"`, `"decimal"`, or `"float"`.
- `"values"`: Array. Used with `"type": "enum"`. Maps the number to a label (e.g., `["Off", "On"]`).
- `"format"`: String. Specialized rendering like `"ipv4"` or `"mac"`.

---

## 4. Home Assistant Discovery

- `"device_class"`: String. Tells HA what kind of sensor it is (e.g., `"voltage"`, `"power"`, `"energy"`, `"temperature"`, `"current"`, `"battery"`).
- `state_class`: String. Home Assistant state class (e.g., `"measurement"`, `"total_increasing"`, `"measurement"`).
- `unit`: String. The unit of measurement (e.g., `"V"`, `"W"`, `"kWh"`, `"°C"`, `"A"`, `"%"`).

---

## 5. Advanced Structures

### 5.1 Bitmasks and Split Registers (`outputs`)
If one register contains multiple independent data fields (like bit-flags), use the `outputs` array.

```json
{
  "reg": 124,
  "name": "System Status",
  "outputs": [
    { "name": "AC Switch", "offset": 1, "mask": 1, "type": "enum", "values": ["Off", "On"] },
    { "name": "ECO Mode", "offset": 9, "mask": 1, "type": "enum", "values": ["Off", "On"] }
  ]
}
```
- **`offset`**: Number of bits to shift right before masking.
- **`mask`**: Bitmask to apply (e.g., `1` for a single bit, `255` for a whole byte).

### 5.2 Bulk and Grouped Reads (`bulk_read`)
To improve Bluetooth efficiency, group sequential registers into a single Modbus request.

```json
{
  "bulk_read": {
    "slave_id": 41,
    "read_start_reg": 6000,
    "read_count": 10,
    "registers": [
       { "reg": 6000, "name": "Platform Type" },
       { "reg": 6003, "name": "Voltage", "unit": "V", "scale": 1 }
    ]
  }
}
```

### 5.3 Triggers (`trigger_write`)
Used for Energy Statistics (3500 range) or Fault Logs. This writes a value to a "selector" register before reading the data.

```json
{
  "trigger_write": [
    { "trigger_metadata": { "trigger_reg": 2027, "trigger_value": 0, "slave_id": 1 } },
    { "trigger_metadata": { "trigger_reg": 2028, "trigger_value": 1, "delay": 0.8 } },
    { "bulk_read": { "registers": [ { "reg": 3501, "name": "PV Total Energy" } ] } }
  ]
}
```

---

## 6. Dynamic and Repeating Blocks

These are specialized types that automatically discover and create Home Assistant entities based on data values.

### 6.1 `repeating_count`
Used when one register tells you how many blocks follow it (e.g., Number of Phases or Node List).

**Key Fields:**
- `count_offset`: Distance from the `reg` where the count value is stored (0 = current register).
- **Fallback**: If the register value is `0`, the script automatically calculates the count based on the `len` (total bytes read) and `block_regs`.
- `start_offset`: Number of registers to skip after the count before the blocks start.
- `block_regs`: Size of each repeating block in registers.
- `outputs`: The fields to extract from each discovered block.

### 6.2 `dynamic_bmu_block`
A specialized hardware-specific handler for the `6300` register range.
- It automatically calculates the Cell and NTC counts from the headers.
- It handles the "shifting start address" where NTCs follow immediately after the last cell.
- It handles the "two-values-per-register" format for temperatures.
- **Voltage Masking**: Uses `mask: 16383` (0x3FFF) for 1mV resolution.
- **Balance Bitmask**: Uses `offset: 14` and `mask: 3` to extract bits 14-15 for balancing status.

### 6.3 `segmented_repeating`
Used for interleaved data tables (like `7200` BMU metadata).
- It allows the config to "stitch together" data for a single node (e.g., Pack 1) from different "strips" of data across the response.
- **`stride`**: How many registers a single node occupies within a segment.

---

## 7. Development Conventions

### Naming
- **Telemetry**: Use a "Category: Name" pattern (e.g., `"INV_PV_INFO: Voltage"`).
- **Calculated Fields**: If a field is derived by the script, mark it in the `notes`.
- **Units**: Always use standard SI units where possible.

### Scaling Reference
| Device Value | JSON `scale` | Real Value |
| :--- | :--- | :--- |
| `1234` | `0` | `1234` |
| `1234` | `1` | `123.4` |
| `1234` | `2` | `12.34` |
| `1234` | `3` | `1.234` |

### Slave IDs for EP2000
- **`1`**: The main Inverter / Gateway. (Default)
- **`41`**: The BMS Gateway (HV800). This is where the majority of battery detail is found.

---

## 9. Session Maintenance (Keep-Alive)

The EP2000 employs a "Low-Power Telemetry Strategy." If it does not detect a heartbeat from a controller, it clears the registers on Slave 41 to save resources.

The Debugger automatically mimics the official app by sending the following "Session Lock" writes at the start of every poll:
1. **Register 190 = 1 (Slave 1)**: Master session lock.
2. **Register 30001 = 1 (Slave 0)**: Global broadcast mesh refresh.
3. **Register 21000 = 6 (Slave 1)**: Forces a re-scan of peripheral nodes.

*Note: This behavior is automatically triggered when the device is detected as a V2 protocol device.*

---

## 8. Troubleshooting

### Common Errors
1. **`Expecting ',' delimiter`**: You missed a comma between objects or arrays.
2. **`KeyError: 'name'`**: You added a register definition but forgot the `"name"` field.
3. **`Invalid Protocol`**: You defined a 32-bit register but the device returned 16 bits. Check your `len` and `reg` offset.

### Debugging Tips
- Use a JSON validator (like JSONLint) to check your syntax before running the debugger.
- The debugger will reload the config automatically if you save changes while it is running.

---

*This documentation is part of the EP2000 MQTT Bridge project.*