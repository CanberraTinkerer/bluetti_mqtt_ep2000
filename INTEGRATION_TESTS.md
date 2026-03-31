# Integration Tests with Real Device

## Overview

The integration test suite connects to a **real Bluetti device** via Bluetooth and performs actual modbus operations. These tests validate the complete trigger functionality end-to-end.

## What Gets Tested

### 1. Device Connection
- ✓ Successful Bluetooth connection
- ✓ Device ready state
- ✓ Connection timeout handling

### 2. Trigger Writes
- ✓ PV trigger write (value 0 to register 2027)
- ✓ Grid trigger write (value 2 to register 2027)
- ✓ Response validation
- ✓ Exception detection

### 3. Register 3500 Reads
- ✓ Read after PV trigger
- ✓ Read after Grid trigger
- ✓ Response parsing
- ✓ Exception 3 detection

### 4. Full Polling Sequences
- ✓ Complete PV polling: trigger write → wait → read
- ✓ Complete Grid polling: trigger write → wait → read
- ✓ Multi-register reads with single trigger

### 5. Exception Handling
- ✓ Exception 3 without trigger (expected failure)
- ✓ Normal responses with valid trigger
- ✓ Device communication errors

## Requirements

### Hardware
- Bluetti device (EP2000, AC300, AC500, etc.)
- Device must be turned on and in range
- Bluetooth connectivity to your machine

### Software
```bash
pip install paho-mqtt bleak crcmod dbus-next pycryptodome
```

### Device Address
You need the Bluetooth MAC address of your device:

```bash
# On Linux, scan for devices:
sudo bluetoothctl
scan on
# Wait for your device to appear
# Note the MAC address (format: XX:XX:XX:XX:XX:XX)
exit

# Or find devices programmatically:
python -m bluetti_mqtt.discovery_cli --scan
```

## Running Integration Tests

### Option 1: With Device Present
```bash
export BLUETTI_DEVICE_ADDRESS='AA:BB:CC:DD:EE:FF'
cd /srv/bluetti_mqtt_ep2000
python -m unittest tests.test_mqtt_debugger_integration -v
```

### Option 2: Run Specific Test Class
```bash
export BLUETTI_DEVICE_ADDRESS='AA:BB:CC:DD:EE:FF'
python -m unittest tests.test_mqtt_debugger_integration.TestTriggerWriteOnDevice -v
```

### Option 3: Run Specific Test
```bash
export BLUETTI_DEVICE_ADDRESS='AA:BB:CC:DD:EE:FF'
python -m unittest tests.test_mqtt_debugger_integration.TestRegister3500ReadOnDevice.test_read_3500_after_pv_trigger -v
```

### Option 4: Without Device (Tests Skipped)
```bash
python -m unittest tests.test_mqtt_debugger_integration -v
# All tests will show:
# ⊘ SKIPPED: test_name - Set BLUETTI_DEVICE_ADDRESS env var to run
```

## Test Output Examples

### Successful PV Trigger Write
```
📝 Testing PV Trigger Write to Device AA:BB:CC:DD:EE:FF
  Writing 0 to register 2027 (PV mode)...
  Response: 010600000000... 
✓ PV trigger write successful
```

### Successful 3500 Read After Trigger
```
📖 Testing Register 3500 Read After PV Trigger
  Step 1: Writing trigger (0) to register 2027...
    Response: 010600fbb000...
  Step 2: Waiting 200ms for mode switch...
  Step 3: Reading register 3500...
    Response length: 35 bytes
    Response hex: 01030f1234567...
✓ Read successful - Normal response (0x03)
```

### Full Polling Sequence
```
🔄 Testing Full PV Polling Sequence
  Trigger: 2027 = 0
  Read range: 3500 - 3509
  Executing trigger write...
✓ Trigger write complete
  Executing group read...
✓ Read successful - 35 bytes received
```

## Test Classes

### TestDeviceConnection
Tests basic Bluetooth connectivity.
- `test_device_connection` - Connect and verify ready state

### TestTriggerWriteOnDevice
Tests actual trigger writes to the device.
- `test_write_pv_trigger` - Write 0 to register 2027
- `test_write_grid_trigger` - Write 2 to register 2027

### TestRegister3500ReadOnDevice
Tests reading the 3500 register range after triggers.
- `test_read_3500_after_pv_trigger` - PV mode read
- `test_read_3500_after_grid_trigger` - Grid mode read

### TestFullTriggerPollingSequence
Tests complete polling workflows.
- `test_full_pv_polling_sequence` - Full PV polling
- `test_full_grid_polling_sequence` - Full Grid polling

### TestExceptionHandlingOnDevice
Tests exception handling with real device.
- `test_exception_3_without_trigger` - Verify exception when trigger not set

## Timing Considerations

- **Connection timeout**: 30 seconds (configurable via `self.timeout`)
- **Mode switch delay**: 200ms (between trigger write and read)
- **Total test time**: ~2-3 seconds per test (after connection)

## Troubleshooting

### Test Hangs on Connection
```
# Timeout is 30 seconds. If device is slow:
# 1. Verify device is powered on
# 2. Check Bluetooth visibility
# 3. Restart Bluetooth on device
# 4. Increase timeout (modify test code)
```

### Exception 3 Returned
```
# This is NORMAL if:
# - Device is busy
# - Trigger write didn't complete
# - Mode switch time insufficient (increase sleep time)
```

### ModbusError or Bad Response
```
# Possible causes:
# 1. Device disconnected
# 2. Register not supported
# 3. Device in error state

# Solution: Restart device and reconnect
```

### Bluetooth Not Found
```
# Linux only - need bluetoothctl:
sudo apt-get install bluetooth

# Or use discovery:
python -m bluetti_mqtt.discovery_cli --scan
```

## Comparing Unit vs Integration Tests

| Aspect | Unit Tests | Integration Tests |
|--------|-----------|-------------------|
| Device Required | ❌ No | ✅ Yes |
| Real Modbus | ❌ No | ✅ Yes |
| Bluetooth | ❌ No | ✅ Yes |
| Speed | ⚡ Fast (<1s) | 🐢 Slow (2-3s per test) |
| Skippable | ❌ No | ✅ Yes |
| CI/CD | ✅ Yes | ❌ (needs device) |
| Debugging | 🔍 Easy | 🔍 Medium |

## Running Both Test Suites

```bash
# Run unit tests (fast, always works)
python -m unittest tests.test_mqtt_debugger_triggers -v

# Run unit + integration tests
python -m unittest discover -s tests -p "test_*.py" -v

# With device address for integration tests
export BLUETTI_DEVICE_ADDRESS='AA:BB:CC:DD:EE:FF'
python -m unittest discover -s tests -p "test_*.py" -v
```

## Creating Custom Integration Tests

Add tests to [test_mqtt_debugger_integration.py](test_mqtt_debugger_integration.py):

```python
class TestCustomFeature(unittest.TestCase):
    """Test a custom feature on real device."""
    
    def setUp(self):
        self.device_address = get_device_address()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        self.loop.close()
    
    @skip_if_no_device
    def test_my_feature(self):
        """Test description."""
        if not self.device_address:
            self.skipTest("No device address provided")
        
        async def run_test():
            print(f"\n🧪 My Test")
            
            client = BluetoothClient(self.device_address)
            client_task = asyncio.create_task(client.run())
            
            try:
                # Wait for connection
                timeout = 30
                while not client.is_ready and timeout > 0:
                    await asyncio.sleep(1)
                    timeout -= 1
                
                if not client.is_ready:
                    self.fail("Device not connected")
                
                # Your test code here
                print("✓ Test passed")
                
            finally:
                client_task.cancel()
                try:
                    await client_task
                except asyncio.CancelledError:
                    pass
        
        self.loop.run_until_complete(run_test())
```

## Continuous Integration

For CI/CD pipelines, skip integration tests:

```bash
# Only run unit tests
python -m unittest tests.test_mqtt_debugger_triggers -v

# Or skip integration tests explicitly
python -m unittest discover -s tests -p "test_mqtt_debugger_triggers.py" -v
```

## File Location

```
/srv/bluetti_mqtt_ep2000/tests/
├── test_mqtt_debugger_triggers.py        ← Unit tests (always runs)
└── test_mqtt_debugger_integration.py     ← Integration tests (needs device)
```

## Next Steps

1. **Find your device address**:
   ```bash
   python -m bluetti_mqtt.discovery_cli --scan
   ```

2. **Export it**:
   ```bash
   export BLUETTI_DEVICE_ADDRESS='XX:XX:XX:XX:XX:XX'
   ```

3. **Run integration tests**:
   ```bash
   python -m unittest tests.test_mqtt_debugger_integration -v
   ```

4. **Compare with unit tests**:
   ```bash
   python -m unittest tests.test_mqtt_debugger_triggers -v
   ```

5. **Run both together**:
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v
   ```

## Support

If tests fail:
1. Check device is powered on and discoverable
2. Verify device address is correct
3. Try running with `-v -s` for verbose output
4. Check Bluetooth connectivity
5. Restart device if needed
