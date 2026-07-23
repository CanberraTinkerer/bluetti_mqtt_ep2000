# MQTT Debugger Trigger Tests

## Overview

This test suite validates the modbus trigger functionality in `mqtt_debugger.py`. The trigger mechanism allows the debugger to write a value to a trigger register (register 2027) before reading a group of target registers (typically in the 3500 modbus register range).

## Test Coverage

### 1. **Trigger Configuration** (TestTriggerConfiguration)
   - Verifies that `trigger_reg` and `trigger_val` are properly configured in register definitions
   - Tests that different trigger values are supported (0 for PV data, 2 for Grid Import data)

### 2. **Command Grouping** (TestGroupCommandsWithTriggers)  
   - Confirms that commands with the same trigger are grouped together
   - Verifies that commands with different trigger values are kept in separate groups
   - Ensures the 3500 register range is properly included in grouped commands

### 3. **Trigger Write and Read** (TestTriggerWriteAndRead)
   - Tests that WriteSingleRegister commands are properly created for trigger writes
   - Verifies correct format and structure of trigger write commands
   - Confirms read commands can be created after trigger setup

### 4. **Register 3500 Range** (TestReadRegister3500Range)
   - Tests reading individual registers from the 3500 range (3500, 3506, 3508, 3602, 3604, 3606, 3608)
   - Confirms multiple 3500-range registers include trigger configuration
   - Validates address and quantity parameters for read commands

### 5. **Modbus Exception Handling** (TestModbusExceptionHandling)
   - Tests detection of modbus exception 3 (Illegal Data Value)
   - Confirms valid read responses don't have the exception bit set
   - Validates data responses are correctly identified as non-exception

### 6. **Multiple Trigger Values** (TestTriggerWithDifferentValues)
   - Tests PV data trigger (trigger_val=0)
   - Tests Grid Import trigger (trigger_val=2)
   - Confirms different trigger values result in separate groups

### 7. **Encrypted Registers** (TestEncryptedRegistersWithTrigger)
   - Tests that encrypted (V2 protocol) 3500 registers with triggers are properly configured
   - Verifies encryption flags are preserved during grouping

### 8. **Slave ID Determination** (TestTargetSlaveId)
   - Tests default slave ID (1) for 3500 range
   - Confirms explicit slave_id overrides defaults
   - Validates slave ID handling with triggers

### 9. **Full Polling Sequence** (TestFullTriggerPollingSequence)
   - Tests the logical sequence: trigger write → wait → read data
   - Confirms multiple registers can be read after a single trigger write
   - Validates all necessary polling parameters are present

### 10. **Debugger JSON Integration** (TestDebuggerJsonIntegration)
   - Tests loading the actual debugger.json configuration
   - Finds and validates 3500 register entries with triggers
   - Simulates the complete trigger write and read sequence

## Running the Tests

### Prerequisites
```bash
pip install paho-mqtt bleak crcmod dbus-next pycryptodome
```

### Run All Tests
```bash
cd /srv/bluetti_mqtt_ep2000
python -m unittest tests.test_mqtt_debugger_triggers -v
```

### Run Specific Test Class
```bash
python -m unittest tests.test_mqtt_debugger_triggers.TestTriggerConfiguration -v
```

### Run Specific Test Method
```bash
python -m unittest tests.test_mqtt_debugger_triggers.TestReadRegister3500Range.test_read_3500_register_address -v
```

## Test Statistics
- **Total Tests**: 27
- **Test Classes**: 10
- **Coverage Areas**: 
  - Configuration validation
  - Command grouping logic
  - Write/Read operations
  - Exception handling
  - Multiple trigger values
  - Encryption support
  - Slave ID management
  - Full integration scenarios

## How Triggers Work

The modbus trigger mechanism works as follows:

1. **Configuration**: Registers (like 3500-3700) define a `trigger_reg` (e.g., 2027) and `trigger_val` (0 or 2)

2. **Grouping**: Commands with the same trigger are grouped together for efficient polling

3. **Trigger Write**: Before reading the target registers, the debugger writes `trigger_val` to `trigger_reg`:
   ```python
   WriteSingleRegister(2027, 0)  # Write 0 to register 2027
   ```

4. **Brief Pause**: A small delay (100ms) allows the device to process the trigger

5. **Data Read**: Then reads the target registers:
   ```python
   ReadHoldingRegisters(3500, 16)  # Read 16 registers starting at 3500
   ```

6. **Success Criteria**: 
   - Write command completes without exception
   - Read response returns valid data (not modbus exception 3)
   - Data can be parsed and published to MQTT

## Example from debugger.json

```json
{
  "reg": 3500,
  "name": "PV Cumulative Energy",
  "len": 32,
  "scale": 1,
  "unit": "kWh",
  "device_class": "energy",
  "trigger_reg": 2027,
  "trigger_val": 0,
  "encrypted": true
}
```

This configuration means:
- Read from register 3500 (32 bits)
- But first, write value 0 to register 2027 (the trigger)
- This selects the "PV" data mode
- Data is encrypted (V2 protocol)

## Modbus Exception 3 Note

Exception 3 (Illegal Data Value) indicates the register doesn't contain valid data. By triggering with the correct value, the device switches to the appropriate data mode, making the registers readable and avoiding this exception.

## File Location
```
/srv/bluetti_mqtt_ep2000/tests/test_mqtt_debugger_triggers.py
```
