#!/usr/bin/env python3
"""
Detailed V2 Protocol Debugging Script

This script provides detailed debugging for V2 encrypted protocol issues,
including packet analysis and CRC verification.
"""

import asyncio
import logging
import struct
from typing import Optional
from bluetti_mqtt.bluetooth import BluetoothClient, BadConnectionError
from bluetti_mqtt.mqtt_debugger import ReadHoldingRegistersV2, WriteSingleRegisterV2
from bluetti_mqtt.crc import bluetti_custom_crc

class V2ProtocolDebugger:
    """Debug V2 protocol issues with detailed packet analysis."""

    def __init__(self, device_address: str):
        self.device_address = device_address
        self.client: Optional[BluetoothClient] = None
        self.client_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Connect to the device and return success status."""
        print(f"🔗 Connecting to {self.device_address}...")
        self.client = BluetoothClient(
            self.device_address,
            response_timeout=15,
            debug_logging=True,
            device_name="EP2000"
        )
        self.client_task = asyncio.create_task(self.client.run())

        # Wait for connection
        for i in range(60):  # 60 second timeout
            if self.client.is_ready:
                print("✅ Connected successfully!")
                return True
            await asyncio.sleep(1)

        print("❌ Connection timeout")
        return False

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client_task:
            self.client_task.cancel()
            try:
                await self.client_task
            except asyncio.CancelledError:
                pass

    def analyze_v2_packet(self, cmd, description: str):
        """Analyze a V2 command packet in detail."""
        packet = bytes(cmd)
        print(f"\n🔍 {description}")
        print(f"   Raw packet: {packet.hex()}")
        print(f"   Length: {len(packet)} bytes")

        if len(packet) >= 10:
            header = packet[:10]
            payload = packet[10:-2]
            crc_bytes = packet[-2:]

            print(f"   Header: {header.hex()}")
            print(f"   Protocol ID: {header[0]:02x} {header[1]:02x}")
            print(f"   Slave ID: {header[2]}")
            print(f"   Command Type: {header[3]:02x}")
            print(f"   Payload Length: {header[4] << 8 | header[5]}")
            print(f"   Payload: {payload.hex()}")
            print(f"   CRC: {crc_bytes.hex()}")

            # Verify CRC
            calculated_crc = bluetti_custom_crc(packet[:-2])
            received_crc = struct.unpack('!H', crc_bytes)[0]
            print(f"   Calculated CRC: {calculated_crc:04x}")
            print(f"   Received CRC: {received_crc:04x}")
            print(f"   CRC Match: {'✅' if calculated_crc == received_crc else '❌'}")

    async def test_v2_read(self, register: int = 12002, count: int = 1) -> bool:
        """Test V2 read command with detailed analysis."""
        print(f"\n📖 Testing V2 Read: Register {register}, Count {count}")

        cmd = ReadHoldingRegistersV2(register, count)
        self.analyze_v2_packet(cmd, "V2 Read Command Analysis")

        try:
            print("   Sending command...")
            future = await self.client.perform(cmd)
            response = await future

            print(f"   ✅ Response received: {response.hex()}")
            print(f"   Response length: {len(response)} bytes")

            # Analyze response
            if len(response) >= 12:
                header = response[:10]
                payload = response[10:-2]
                crc_bytes = response[-2:]

                print(f"   Response Header: {header.hex()}")
                print(f"   Response Payload: {payload.hex()}")
                print(f"   Response CRC: {crc_bytes.hex()}")

                # Verify response CRC
                calculated_crc = bluetti_custom_crc(response[:-2])
                received_crc = struct.unpack('!H', crc_bytes)[0]
                print(f"   Response CRC Valid: {'✅' if calculated_crc == received_crc else '❌'}")

                # Try to decrypt payload
                try:
                    from Crypto.Cipher import AES
                    from Crypto.Util.Padding import unpad

                    cipher = AES.new(b"sxd_aiot_key_001", AES.MODE_CBC, b"sxd_aiot_2022_01")
                    decrypted = unpad(cipher.decrypt(payload), 16)
                    print(f"   Decrypted payload: {decrypted.hex()}")
                    print(f"   Decrypted data: {decrypted}")

                    # Parse as Modbus response
                    if len(decrypted) >= 3:
                        slave_id = decrypted[0]
                        function_code = decrypted[1]
                        byte_count = decrypted[2]
                        data = decrypted[3:]
                        print(f"   Slave ID: {slave_id}")
                        print(f"   Function Code: {function_code}")
                        print(f"   Byte Count: {byte_count}")
                        print(f"   Data: {data.hex()}")

                except Exception as e:
                    print(f"   Decryption failed: {e}")

            return True

        except BadConnectionError as e:
            print(f"   ❌ BadConnectionError: {e}")
            return False
        except Exception as e:
            print(f"   ❌ Unexpected error: {e}")
            return False

    async def test_v2_write(self, register: int = 2027, value: int = 0) -> bool:
        """Test V2 write command with detailed analysis."""
        print(f"\n📝 Testing V2 Write: Register {register} = {value}")

        cmd = WriteSingleRegisterV2(register, value)
        self.analyze_v2_packet(cmd, "V2 Write Command Analysis")

        try:
            print("   Sending command...")
            future = await self.client.perform(cmd)
            response = await future

            print(f"   ✅ Response received: {response.hex()}")
            print(f"   Response length: {len(response)} bytes")

            # Analyze response (similar to read)
            if len(response) >= 12:
                header = response[:10]
                payload = response[10:-2]
                crc_bytes = response[-2:]

                print(f"   Response Header: {header.hex()}")
                print(f"   Response Payload: {payload.hex()}")
                print(f"   Response CRC: {crc_bytes.hex()}")

                # Verify response CRC
                calculated_crc = bluetti_custom_crc(response[:-2])
                received_crc = struct.unpack('!H', crc_bytes)[0]
                print(f"   Response CRC Valid: {'✅' if calculated_crc == received_crc else '❌'}")

            return True

        except BadConnectionError as e:
            print(f"   ❌ BadConnectionError: {e}")
            return False
        except Exception as e:
            print(f"   ❌ Unexpected error: {e}")
            return False

async def main():
    """Main debugging function."""
    import sys

    if len(sys.argv) != 2:
        print("Usage: python debug_v2.py <device_address>")
        sys.exit(1)

    device_address = sys.argv[1]

    debugger = V2ProtocolDebugger(device_address)

    try:
        if not await debugger.connect():
            return

        # Test various V2 operations
        print("\n" + "="*60)
        print("🔧 V2 PROTOCOL DEBUGGING SESSION")
        print("="*60)

        # Test read operations
        await debugger.test_v2_read(1545, 1)    # Basic read
        await debugger.test_v2_read(3500, 1)    # PV register
        await debugger.test_v2_read(4000, 1)    # Grid register

        # Test write operations
        await debugger.test_v2_write(2027, 0)   # Basic write

        print("\n" + "="*60)
        print("🔧 DEBUGGING COMPLETE")
        print("="*60)

    finally:
        await debugger.disconnect()

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)  # Reduce noise
    asyncio.run(main())