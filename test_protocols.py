#!/usr/bin/env python3
"""
Test different protocol versions to see what the device responds to.
"""

import asyncio
import logging
from bluetti_mqtt.bluetooth import BluetoothClient
from bluetti_mqtt.mqtt_debugger import (
    ReadHoldingRegisters, WriteSingleRegister,
    detect_device_protocol
)

try:
    from bluetti_mqtt.mqtt_debugger import ReadHoldingRegistersV2, WriteSingleRegisterV2
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("⚠️  Crypto library not available - V2 tests will be skipped")

async def test_protocol_versions(device_address):
    """Test V1 (plaintext) vs V2 (encrypted) protocols."""
    print(f"🧪 Testing protocol versions on {device_address}")

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    # Test V1 (plaintext) protocol first
    print("\n📄 Testing V1 (Plaintext) Protocol...")
    client = BluetoothClient(device_address, response_timeout=5, debug_logging=True, device_name="test_device")
    client_task = asyncio.create_task(client.run())

    try:
        # Wait for connection
        print("⏳ Connecting...")
        for i in range(30):
            if client.is_ready:
                print("✅ Connected!")
                break
            await asyncio.sleep(1)
        else:
            print("❌ Failed to connect")
            return

        # Test V1 read
        print("📖 Testing V1 read (register 100)...")
        try:
            cmd = ReadHoldingRegisters(100, 1)  # Plaintext read
            print(f"   TX: {bytes(cmd).hex()}")
            future = await client.perform(cmd)
            response = await future
            print(f"✅ V1 Read successful: {response.hex()}")
        except Exception as e:
            print(f"❌ V1 Read failed: {e}")

        # Test V1 write
        print("📝 Testing V1 write (register 2027 = 0)...")
        try:
            cmd = WriteSingleRegister(2027, 0)  # Plaintext write
            print(f"   TX: {bytes(cmd).hex()}")
            future = await client.perform(cmd)
            response = await future
            print(f"✅ V1 Write successful: {response.hex()}")
        except Exception as e:
            print(f"❌ V1 Write failed: {e}")

    finally:
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass

    # Test V2 (encrypted) protocol
    print("\n🔐 Testing V2 (Encrypted) Protocol...")
    if not HAS_CRYPTO:
        print("❌ V2 tests skipped - crypto library not available")
        return

    client = BluetoothClient(device_address, response_timeout=10, debug_logging=True, device_name="EP2000")
    client_task = asyncio.create_task(client.run())

    try:
        # Wait for connection
        print("⏳ Connecting...")
        for i in range(30):
            if client.is_ready:
                print("✅ Connected!")
                break
            await asyncio.sleep(1)
        else:
            print("❌ Failed to connect")
            return

        # Test V2 read
        print("📖 Testing V2 read (register 12002)...")
        try:
            cmd = ReadHoldingRegistersV2(12002, 1)  # Encrypted read
            print(f"   TX: {bytes(cmd).hex()}")
            future = await client.perform(cmd)
            response = await future
            print(f"✅ V2 Read successful: {response.hex()}")
        except Exception as e:
            print(f"❌ V2 Read failed: {e}")

        # Test V2 write
        print("📝 Testing V2 write (register 2027 = 0)...")
        try:
            cmd = WriteSingleRegisterV2(2027, 0)  # Encrypted write
            print(f"   TX: {bytes(cmd).hex()}")
            future = await client.perform(cmd)
            response = await future
            print(f"✅ V2 Write successful: {response.hex()}")
        except Exception as e:
            print(f"❌ V2 Write failed: {e}")

    finally:
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass

    print("\n" + "="*50)
    print("Protocol test complete.")
    print("If V1 works but V2 doesn't, check CRC implementation or device compatibility.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python test_protocols.py <device_address>")
        sys.exit(1)

    device_address = sys.argv[1]
    asyncio.run(test_protocol_versions(device_address))