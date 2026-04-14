#!/usr/bin/env python3
"""
Test CRC calculation for V2 packets
"""

from bluetti_mqtt.crc import bluetti_custom_crc

# Test packet from the log
write_full_hex = '00170118001000000000368aee0b7afee4a55cdcef337052877f616f'
read_full_hex = '00170117001000000000f4c367760802341cfa3384b4cec2c89ad034'

write_hex = write_full_hex[:-4]
read_hex = read_full_hex[:-4]

print(f"Write hex: {write_hex}")
print(f"Read hex: {read_hex}")
print(f"Write hex length: {len(write_hex)}")
print(f"Read hex length: {len(read_hex)}")

write_packet_no_crc = bytes.fromhex(write_hex)
read_packet_no_crc = bytes.fromhex(read_hex)

print(f"Write packet CRC: {bluetti_custom_crc(write_packet_no_crc):04x}")
print(f"Read packet CRC: {bluetti_custom_crc(read_packet_no_crc):04x}")