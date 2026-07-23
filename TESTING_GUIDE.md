# MQTT Debugger Modbus Trigger Tests - Complete Guide

## Summary

I've created a comprehensive test suite with **27 tests** that validate the modbus trigger functionality in `mqtt_debugger.py`. All tests are **passing** ✓

### Test Results
```
Ran 27 tests in 0.021s
OK
```

## Files Created

### 1. **Test Suite** 
📄 [tests/test_mqtt_debugger_triggers.py](tests/test_mqtt_debugger_triggers.py)
- Main test file with 27 comprehensive tests
- Uses Python's built-in `unittest` framework
- No external test dependencies required

### 2. **Test Summary**
📄 [TEST_SUMMARY.md](TEST_SUMMARY.md)
- Overview of all 10 test classes
- What each test validates
- How to run specific tests

### 3. **Test Examples**
📄 [TRIGGER_TEST_EXAMPLES.md](TRIGGER_TEST_EXAMPLES.md)
- Real-world test scenarios
- Success criteria for each test
- Example modbus trigger sequences
- Debugging tips

### 4. **Test Runner Script**
📄 [run_trigger_tests.sh](run_trigger_tests.sh)
- Automated script to set up and run tests
- Handles virtual environment creation
- Installs all dependencies automatically

## What the Tests Validate

### Core Trigger Functionality
✓ Trigger configuration (register 2027 with values 0, 2)
✓ Trigger write before data read sequence
✓ PV data selection (trigger_val=0)
✓ Grid Import data selection (trigger_val=2)
✓ Multiple registers with single trigger
✓ Encrypted (V2 protocol) triggers

### Register 3500 Range
✓ Reading register 3500
✓ Reading registers 3506, 3508, 3602, 3604, 3606, 3608
✓ Proper grouping with triggers
✓ Configuration preservation

### Exception Handling
✓ Detection of modbus exception 3 (Illegal Data Value)
✓ Valid response identification
✓ Data with actual values vs. exception responses

### Configuration & Grouping
✓ trigger_reg and trigger_val present in config
✓ Registers grouped by trigger value
✓ Slave ID handling
✓ Encrypted flag preservation

## Quick Start

### Option 1: Run Script (Easiest)
```bash
chmod +x /srv/bluetti_mqtt_ep2000/run_trigger_tests.sh
./run_trigger_tests.sh
```

### Option 2: Manual Setup
```bash
# Create and activate virtual environment
python3 -m venv /tmp/mqtt_test_venv
source /tmp/mqtt_test_venv/bin/activate

# Install dependencies
pip install paho-mqtt bleak crcmod dbus-next pycryptodome

# Run all tests
cd /srv/bluetti_mqtt_ep2000
python -m unittest tests.test_mqtt_debugger_triggers -v
```

### Option 3: Run Specific Tests
```bash
# Test PV trigger
python -m unittest tests.test_mqtt_debugger_triggers.TestTriggerWithDifferentValues.test_pv_trigger_value_0 -v

# Test 3500 register range
python -m unittest tests.test_mqtt_debugger_triggers.TestReadRegister3500Range -v

# Test exception handling
python -m unittest tests.test_mqtt_debugger_triggers.TestModbusExceptionHandling -v
```

## Test Categories (10 Classes, 27 Tests)

| Class | Tests | Purpose |
|-------|-------|---------|
| TestTriggerConfiguration | 2 | Verify trigger_reg/trigger_val exist |
| TestGroupCommandsWithTriggers | 3 | Test command grouping by trigger |
| TestTriggerWriteAndRead | 3 | Validate write/read command creation |
| TestReadRegister3500Range | 3 | Test 3500-range register reads |
| TestModbusExceptionHandling | 3 | Test exception 3 detection |
| TestTriggerWithDifferentValues | 3 | PV (0) vs Grid (2) triggers |
| TestEncryptedRegistersWithTrigger | 2 | V2 protocol with triggers |
| TestTargetSlaveId | 3 | Slave ID determination |
| TestFullTriggerPollingSequence | 2 | Complete polling sequence |
| TestDebuggerJsonIntegration | 3 | Real debugger.json validation |

## How Modbus Triggers Work

```
Device State: Mode Register 2027
- Write 0 → PV data mode
- Write 2 → Grid Import mode

Data Registers (3500-3700):
Only contain valid data when trigger is set correctly
Otherwise return Exception 3 (Illegal Data Value)

Correct Sequence:
1. WriteSingleRegister(2027, 0)  ← Set PV mode
2. Sleep(100ms)                   ← Wait for mode switch
3. ReadHoldingRegisters(3500, 4) ← Read PV energy data ✓
```

## Example Test Output

```
test_pv_trigger_value_0 ... ok
test_grid_trigger_value_2 ... ok
test_read_3500_register_address ... ok
test_modbus_exception_3_detection ... ok
test_trigger_poll_sequence_order ... ok
test_encrypted_register_with_trigger ... ok

Ran 27 tests in 0.021s
OK ✓
```

## Key Tests for Trigger Verification

### 1. PV Data Trigger
**Test**: `test_pv_trigger_value_0`
```python
config = [{"reg": 3500, "trigger_reg": 2027, "trigger_val": 0}]
# Verifies: Can read 3500 after writing 0 to 2027
```

### 2. Grid Data Trigger  
**Test**: `test_grid_trigger_value_2`
```python
config = [{"reg": 3500, "trigger_reg": 2027, "trigger_val": 2}]
# Verifies: Can read 3500 after writing 2 to 2027
```

### 3. Exception Handling
**Test**: `test_modbus_exception_3_detection`
```python
# Verifies: Exception 3 = Illegal Data Value
# Prevents: Misinterpreting failed reads as valid data
```

### 4. Full Sequence
**Test**: `test_trigger_poll_sequence_order`
```python
# Verifies: Write → Sleep → Read happens in order
# Ensures: Device has time to switch modes
```

## Success Criteria

A modbus trigger is **successful** when:

✓ WriteSingleRegister(2027, value) completes without exception
✓ No modbus exception 3 in response  
✓ Device switches to correct data mode
✓ ReadHoldingRegisters returns valid data
✓ Data can be parsed as modbus registers

## Failure Diagnosis

### Issue: Modbus Exception 3
```
Response: [0x01][0x83][0x03]
Meaning: Register contains no valid data
Solution: Ensure trigger (2027) is written before read
```

### Issue: Invalid Register Address
```
Solution: Verify register is in 3500-3700 range
Solution: Check trigger_reg and trigger_val in config
```

### Issue: Encrypted Read Fails
```
Solution: Ensure WriteSingleRegisterV2 is used for encrypted triggers
Solution: Verify "encrypted": true in config
```

## Files Location
```
/srv/bluetti_mqtt_ep2000/
├── tests/
│   ├── __init__.py
│   └── test_mqtt_debugger_triggers.py    ← Main test file
├── TEST_SUMMARY.md                        ← Detailed test overview
├── TRIGGER_TEST_EXAMPLES.md               ← Real-world examples
└── run_trigger_tests.sh                   ← Test runner script
```

## Dependencies

Tests require these Python packages:
- `paho-mqtt` - MQTT client
- `bleak` - Bluetooth LE client
- `crcmod` - CRC calculation
- `dbus-next` - DBus interface
- `pycryptodome` - AES encryption for V2 protocol

Install with:
```bash
pip install paho-mqtt bleak crcmod dbus-next pycryptodome
```

## Next Steps

1. **Run the tests**:
   ```bash
   ./run_trigger_tests.sh
   ```

2. **Verify all 27 tests pass**:
   - Expected: `Ran 27 tests... OK`

3. **Check specific trigger scenarios**:
   - PV trigger test
   - Grid trigger test  
   - Exception handling test

4. **Review test code**:
   - See `tests/test_mqtt_debugger_triggers.py`
   - Each test has a docstring explaining what it validates

5. **Integrate into CI/CD** (optional):
   ```bash
   python -m unittest tests.test_mqtt_debugger_triggers
   ```

## Questions?

- **How does the trigger work?** → See TRIGGER_TEST_EXAMPLES.md
- **What's being tested?** → See TEST_SUMMARY.md
- **How to run tests?** → See Quick Start section above
- **Test code location?** → `tests/test_mqtt_debugger_triggers.py`

---

**Test Suite Status**: ✅ All 27 tests passing
**Last Updated**: 2026-03-31
**Coverage**: Trigger configuration, write/read sequences, exception handling, encryption, all register ranges
