# Modbus Trigger Functionality Tests - Success Criteria

## What Gets Tested

The test suite validates that the modbus trigger mechanism in `mqtt_debugger.py` works correctly. This is critical for reading energy registers (3500-3700 range) that require a mode-selection trigger.

## Test Scenarios

### Scenario 1: PV Data Trigger (trigger_val=0)
**Purpose**: Select PV (solar panel) energy data mode

**Command Sequence**:
1. Write 0 to register 2027 (trigger write)
2. Wait 100ms
3. Read registers 3500-3508 (PV energy data)

**Success Criteria**:
✓ WriteSingleRegister(2027, 0) completes without exception
✓ ReadHoldingRegisters(3500, 4) returns valid data (not exception 3)
✓ Data can be parsed as energy values

**Test**: `TestTriggerWithDifferentValues.test_pv_trigger_value_0`

### Scenario 2: Grid Import Data Trigger (trigger_val=2)
**Purpose**: Select Grid Import energy data mode

**Command Sequence**:
1. Write 2 to register 2027 (trigger write)
2. Wait 100ms
3. Read registers 3500-3506 (Grid Import energy data)

**Success Criteria**:
✓ WriteSingleRegister(2027, 2) completes without exception
✓ ReadHoldingRegisters(3500, 3) returns valid data (not exception 3)
✓ Data can be parsed as energy values

**Test**: `TestTriggerWithDifferentValues.test_grid_trigger_value_2`

### Scenario 3: Encrypted Register Read (V2 Protocol)
**Purpose**: Read encrypted energy registers after trigger

**Command Sequence**:
1. WriteSingleRegisterV2(2027, 0, encrypted) - Encrypted trigger write
2. Wait 100ms
3. ReadHoldingRegistersV2(3500, 16, encrypted) - Encrypted data read

**Success Criteria**:
✓ Encrypted flag is preserved during grouping
✓ Both write and read use encryption
✓ Decryption succeeds and returns valid data

**Test**: `TestEncryptedRegistersWithTrigger.test_encrypted_register_with_trigger`

### Scenario 4: Multiple Registers After Single Trigger
**Purpose**: Confirm trigger applies to multiple reads

**Command Sequence**:
1. Write 0 to register 2027 (single trigger)
2. Read register 3500 (PV Cumulative)
3. Read register 3506 (PV 2024)
4. Read register 3508 (PV 2025)
5. Read register 3602 (PV 2024 Total)

**Success Criteria**:
✓ One trigger write applies to all subsequent reads
✓ All registers are grouped with same trigger_reg=2027, trigger_val=0
✓ Each read completes without exception

**Test**: `TestFullTriggerPollingSequence.test_multiple_register_reads_after_single_trigger`

### Scenario 5: Exception 3 Detection
**Purpose**: Verify that invalid reads return exception 3 when trigger isn't set

**Response Format**:
```
[Slave_ID] [Func_Code | 0x80] [Exception_Code]
   0x01         0x83             0x03
```

**Success Criteria**:
✓ Exception bit (0x80) is set in function code
✓ Exception code = 3 indicates "Illegal Data Value"
✓ This only happens when trigger isn't set correctly

**Test**: `TestModbusExceptionHandling.test_modbus_exception_3_detection`

### Scenario 6: Valid Read Response
**Purpose**: Confirm valid reads don't have exception bit set

**Response Format**:
```
[Slave_ID] [Func_Code] [Byte_Count] [Data...] [CRC]
   0x01        0x03         0x20    (32 bytes)  0xXXXX
```

**Success Criteria**:
✓ Function code = 0x03 (no exception bit)
✓ Byte count matches requested data
✓ Data is present (not all zeros or invalid)

**Test**: `TestModbusExceptionHandling.test_valid_read_response_not_exception`

## Real-World Example

When polling the "PV Cumulative Energy" register:

1. **Debugger reads configuration**:
   ```json
   {
     "reg": 3500,
     "trigger_reg": 2027,
     "trigger_val": 0
   }
   ```

2. **Groups registers** by trigger:
   ```
   Group 1 (trigger=0):
   - Start: 3500, Count: 9 regs
   - Trigger: Write 0 to 2027
   
   Group 2 (trigger=2):
   - Start: 3500, Count: 3 regs  
   - Trigger: Write 2 to 2027
   ```

3. **Polls Group 1**:
   ```
   Step 1: WriteSingleRegister(2027, 0)
           → Sets device to PV data mode
   
   Step 2: Sleep 100ms (let device switch modes)
   
   Step 3: ReadHoldingRegisters(3500, 9)
           → Reads registers 3500-3508
           → Returns valid energy values
           → No exception 3
   
   Result: ✓ Success
   ```

4. **Publishes to MQTT**:
   ```
   bluetti_debugger/ep2000/3500/state
   {
     "value": 1234.5,
     "PossibleName": "PV Cumulative Energy",
     "modbus_register": "3500",
     "valid": true
   }
   ```

## What Makes Tests Pass

The tests verify these key success conditions:

1. **Command Creation**
   - WriteSingleRegister creates valid bytes
   - ReadHoldingRegisters has correct address/quantity

2. **Configuration Integrity**
   - trigger_reg and trigger_val present
   - Multiple trigger values supported
   - Encrypted flag preserved

3. **Grouping Logic**
   - Same triggers grouped together
   - Different triggers in separate groups
   - 3500 range properly included

4. **Exception Handling**
   - Exception 3 properly detected
   - Valid responses identified
   - Data parsing confirmed

5. **Full Sequence**
   - Trigger write before read
   - Correct register addresses
   - All necessary parameters present

## Running Individual Tests

```bash
# Test PV trigger (trigger_val=0)
python -m unittest tests.test_mqtt_debugger_triggers.TestTriggerWithDifferentValues.test_pv_trigger_value_0 -v

# Test Grid trigger (trigger_val=2)
python -m unittest tests.test_mqtt_debugger_triggers.TestTriggerWithDifferentValues.test_grid_trigger_value_2 -v

# Test 3500 register range
python -m unittest tests.test_mqtt_debugger_triggers.TestReadRegister3500Range -v

# Test exception handling
python -m unittest tests.test_mqtt_debugger_triggers.TestModbusExceptionHandling -v

# Test full sequence
python -m unittest tests.test_mqtt_debugger_triggers.TestFullTriggerPollingSequence -v
```

## Expected Output

When tests pass, you'll see:
```
test_3500_range_with_trigger ... ok
test_encrypted_register_with_trigger ... ok
test_grid_trigger_value_2 ... ok
test_modbus_exception_3_detection ... ok
test_pv_trigger_value_0 ... ok
test_read_3500_register_address ... ok
test_trigger_poll_sequence_order ... ok
test_write_single_register_trigger_format ... ok

Ran 27 tests in 0.019s

OK
```

## Troubleshooting

### If tests fail:

1. **ImportError - Module not found**
   - Solution: Install dependencies with pip
   ```bash
   pip install paho-mqtt bleak crcmod dbus-next pycryptodome
   ```

2. **AttributeError on WriteSingleRegister**
   - Use `.address` not `.starting_address`
   - WriteSingleRegister has `.address` and `.value` attributes

3. **Test timeout**
   - Increase timeout in test runner
   - Some grouping logic may be slow with large configs

### Debugging tips:

```python
# Print grouped commands
grouped = group_commands(config)
for g in grouped:
    print(f"Group: start={g['start_reg']}, count={g['num_regs']}, trigger={g.get('trigger_reg')}")

# Check trigger values
cmd = {"reg": 3500, "trigger_reg": 2027, "trigger_val": 0}
grouped = group_commands([cmd])
print(f"Trigger: reg={grouped[0]['trigger_reg']}, val={grouped[0]['trigger_val']}")
```
