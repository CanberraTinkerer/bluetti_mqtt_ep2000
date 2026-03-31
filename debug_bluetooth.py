#!/usr/bin/env python3
"""
Debug script for Bluetooth Modbus communication issues.

This script helps diagnose "too many retries" errors by providing:
1. Detailed logging of all Bluetooth communication
2. Configurable timeout settings
3. Step-by-step command execution

Usage:
    python debug_bluetooth.py <device_address> [timeout_seconds] [--debug]

Example:
    python debug_bluetooth.py AA:BB:CC:DD:EE:FF 10 --debug
"""

import asyncio
import logging
import sys
from bluetti_mqtt.bluetooth import BluetoothClient
from bluetti_mqtt.mqtt_debugger import ReadHoldingRegistersV2, WriteSingleRegisterV2

async def test_basic_connection(device_address, timeout=5, debug=False):
    """Test basic Bluetooth connection and device discovery."""
    print(f"🔍 Testing basic connection to {device_address}")

    # Enable debug logging if requested
    if debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    client = BluetoothClient(device_address, response_timeout=timeout, debug_logging=debug, device_name="debug_device")
    client_task = asyncio.create_task(client.run())

    try:
        # Wait for connection
        print("⏳ Waiting for device connection...")
        for i in range(30):  # 30 second timeout
            if client.is_ready:
                print("✅ Device connected and ready!")
                print(f"📱 Device name: {client.name}")
                break
            await asyncio.sleep(1)
        else:
            print("❌ Failed to connect within 30 seconds")
            return False

        # Test a simple read command
        print("\n🧪 Testing simple register read (register 10)...")
        try:
            cmd = ReadHoldingRegistersV2(10, 1)  # Read 1 register from address 10
            future = await client.perform(cmd)
            response = await future
            print(f"✅ Read successful: {response.hex()}")
        except Exception as e:
            print(f"❌ Read failed: {e}")
            return False

        print("\n✅ All basic tests passed!")
        return True

    finally:
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass

async def test_trigger_sequence(device_address, timeout=5, debug=False):
    """Test the full trigger -> read sequence that was failing."""
    print(f"🔄 Testing trigger sequence on {device_address}")

    if debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    client = BluetoothClient(device_address, response_timeout=timeout, debug_logging=debug, device_name="debug_device")
    client_task = asyncio.create_task(client.run())

    try:
        # Wait for connection
        print("⏳ Waiting for device connection...")
        for i in range(30):
            if client.is_ready:
                print("✅ Device connected and ready!")
                break
            await asyncio.sleep(1)
        else:
            print("❌ Failed to connect within 30 seconds")
            return

        # Step 1: Write trigger
        print("\n📝 Step 1: Writing trigger (PV mode = 0) to register 2027...")
        try:
            trigger_cmd = WriteSingleRegisterV2(2027, 0)
            trigger_future = await client.perform(trigger_cmd)
            trigger_response = await trigger_future
            print(f"✅ Trigger write successful: {trigger_response.hex()}")
        except Exception as e:
            print(f"❌ Trigger write failed: {e}")
            return

        # Step 2: Wait for device to process
        print("⏳ Step 2: Waiting 200ms for device to process trigger...")
        await asyncio.sleep(0.2)

        # Step 3: Read 3500 register
        print("📖 Step 3: Reading register 3500 (should contain PV data)...")
        try:
            read_cmd = ReadHoldingRegistersV2(3500, 4)
            read_future = await client.perform(read_cmd)
            read_response = await read_future
            print(f"✅ Read successful: {read_response.hex()}")

            # Parse the data
            if len(read_response) >= 8:
                # For 32-bit values in CDAB order (word-swapped)
                val1 = (read_response[2] << 8) | read_response[3]  # High word
                val2 = (read_response[0] << 8) | read_response[1]  # Low word
                combined = (val1 << 16) | val2
                print(f"📊 Parsed 32-bit value: {combined} (0x{combined:08x})")
            else:
                print("⚠️  Response too short for 32-bit parsing")

        except Exception as e:
            print(f"❌ Read failed: {e}")
            print("   This might be expected if the device doesn't have PV data")

        print("\n✅ Trigger sequence test completed!")

    finally:
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass

def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_bluetooth.py <device_address> [timeout_seconds] [--debug]")
        print("Example: python debug_bluetooth.py AA:BB:CC:DD:EE:FF 10 --debug")
        sys.exit(1)

    device_address = sys.argv[1]
    timeout = 5  # default
    debug = False

    if len(sys.argv) > 2:
        try:
            timeout = float(sys.argv[2])
        except ValueError:
            if sys.argv[2] == '--debug':
                debug = True
            else:
                print(f"Invalid timeout value: {sys.argv[2]}")
                sys.exit(1)

    if len(sys.argv) > 3 and sys.argv[3] == '--debug':
        debug = True

    print(f"🚀 Starting Bluetooth debug session")
    print(f"   Device: {device_address}")
    print(f"   Timeout: {timeout}s")
    print(f"   Debug logging: {'enabled' if debug else 'disabled'}")
    print()

    # Run basic connection test
    asyncio.run(test_basic_connection(device_address, timeout, debug))

    print("\n" + "="*50 + "\n")

    # Run trigger sequence test
    asyncio.run(test_trigger_sequence(device_address, timeout, debug))

if __name__ == "__main__":
    main()