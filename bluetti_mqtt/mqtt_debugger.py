"""
A utility to poll data from a Bluetti device and publish it to MQTT.

This utility connects to a Bluetti device via Bluetooth, reads a list of
registers from a JSON configuration file, polls the device for the values of
those registers, and then publishes the data to an MQTT broker. It also
supports Home Assistant's MQTT auto-discovery feature, which allows Home
Assistant to automatically discover and configure the device as a set of
sensors.
"""

import asyncio
import json
import struct
import os
import time
import logging
import hashlib
from datetime import datetime
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional, Set, cast

import paho.mqtt.client as mqtt
from bleak.exc import BleakError

from bluetti_mqtt.bluetooth import (
    BadConnectionError,
    BluetoothClient,
    ModbusError,
    ParseError,
    check_addresses,
    scan_devices,
)
from bluetti_mqtt.crc import bluetti_custom_crc
from bluetti_mqtt.core.commands import ReadHoldingRegisters, WriteSingleRegister
from bluetti_mqtt.core.utils import modbus_crc

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

INVERTER_MODEL_ID_REGISTER = 1100
BATTERY_PACK_MODEL_ID_REGISTER = 6100
BMU_LOGIC_MODEL_ID_REGISTER = 7232


def zero_pad(data: bytes, block_size: int = 16) -> bytes:
    """Pad data to block_size with zero bytes (AES/CBC/NoPadding)."""
    padding_len = (block_size - (len(data) % block_size)) % block_size
    return data + (b'\x00' * padding_len)


def generate_iv() -> bytes:
    """
    Generate IV for V2 encryption.
    Official implementation: 4 random bytes → MD5 hash (32 hex chars = 16 bytes)
    """
    random_bytes = os.urandom(4)
    iv_hex = random_bytes.hex()
    iv_hash = hashlib.md5(iv_hex.encode()).digest()
    return iv_hash


def bytes_to_words(response_bytes: bytes):
    return [int.from_bytes(response_bytes[i:i + 2], 'big') for i in range(0, len(response_bytes), 2)]


def to_signed(value: int) -> int:
    if value > 32767:
        return value - 65536
    return value


def to_32bit_signed(value: int) -> int:
    if value > 2147483647:
        return value - 4294967296
    return value


def apply_scale(value: int, scale: int) -> float:
    return value / (10 ** scale)


def swap_bytes(data: bytes) -> bytes:
    """Swaps the place of every other byte."""
    arr = bytearray(data)
    for i in range(0, len(arr) - 1, 2):
        arr[i], arr[i + 1] = arr[i + 1], arr[i]
    return bytes(arr)


def bytes_to_ascii(response_bytes: bytes) -> str:
    return swap_bytes(response_bytes).decode('ascii').strip('\x00')


class ReadHoldingRegistersV2(ReadHoldingRegisters):
    KEY = b"sxd_aiot_key_001"
    # IV is now generated dynamically per command instead of hardcoded

    def __init__(self, starting_address: int, quantity: int, slave_id: int = 1):
        if not HAS_CRYPTO:
            raise ImportError("Crypto library required for V2 protocol")
        
        super().__init__(starting_address, quantity, slave_id=slave_id)
        self.slave_id = slave_id # Store slave_id as an instance attribute
        
        # Generate a dynamic IV for this command (4 random bytes → MD5 hash)
        self.iv = generate_iv()
        print(f"DEBUG V2: Generated IV: {self.iv.hex()}")
        
        # 1. Build Modbus PDU: [Slave][Func][Addr_H][Addr_L][Count_H][Count_L]
        pdu = struct.pack('!BBHH', slave_id, 3, starting_address, quantity)
        print(f"DEBUG V2: Plaintext PDU: {pdu.hex()}")
        
        # 2. Append inner Modbus CRC (little-endian)
        inner_crc = modbus_crc(pdu)
        pdu_with_crc = pdu + inner_crc.to_bytes(2, byteorder='little')
        print(f"DEBUG V2: PDU with inner CRC: {pdu_with_crc.hex()}")

        # 3. Encrypt with AES-CBC/NoPadding (zero-pad to 16-byte boundary)
        padded_pdu = zero_pad(pdu_with_crc, 16)
        print(f"DEBUG V2: Padded PDU (zero-padding): {padded_pdu.hex()}")
        
        cipher = AES.new(self.KEY, AES.MODE_CBC, self.iv)
        encrypted_payload = cipher.encrypt(padded_pdu)
        print(f"DEBUG V2: Encrypted payload: {encrypted_payload.hex()}")

        # 4. Build V2 Frame Header (10 bytes)
        # Structure: [0x00][0x17][SlaveID][CommandType (0x17)][PayloadLen_H][PayloadLen_L][Reserved (4 bytes)]
        payload_len = len(encrypted_payload)
        header = bytearray([
            0x00,           # Protocol ID Byte 1
            0x17,           # Protocol ID Byte 2 (V2 Protocol)
            self.slave_id,  # Slave ID
            0x17,           # Command Type for Read Multiple (0x17)
            (payload_len >> 8) & 0xFF,  # Length High
            payload_len & 0xFF,         # Length Low
            0x00, 0x00, 0x00, 0x00      # Reserved (could contain IV info if needed)
        ])
        print(f"DEBUG V2: Header: {header.hex()}")
        
        # 5. Concatenate header and encrypted payload for CRC calculation
        self.cmd = header + encrypted_payload

        # 6. CRC calculated over the entire packet (Header + Payload)
        crc = bluetti_custom_crc(self.cmd)
        print(f"DEBUG V2: Calculated Read V2 CRC: {hex(crc)}")
        self.cmd.extend(struct.pack('!H', crc))
        print(f"DEBUG V2: Final packet: {self.cmd.hex()}")

    def response_size(self):
        # Response: [Header: 10] [EncryptedBody: N*16] [CRC: 2]
        # The encrypted PDU is: [Slave][Func][ByteCount][Data...][CRC_LO][CRC_HI]
        pdu_len = 3 + 2 * self.quantity + 2
        # Zero-padding aligns to 16-byte boundary
        num_blocks = (pdu_len + 15) // 16  # Ceiling division
        return 10 + (num_blocks * 16) + 2

    def is_valid_response(self, response: bytes):
        if len(response) < 12:
            return False
        crc = bluetti_custom_crc(response[:-2])
        return crc == struct.unpack('!H', response[-2:])[0]

    def is_exception_response(self, response: bytes):
        return False

    def parse_response(self, response: bytes):
        # V2 Header is 10 bytes, CRC is 2 bytes
        encrypted_body = response[10:-2]
        if len(encrypted_body) % 16 != 0:
            print(f"Warning: Encrypted response length {len(encrypted_body)} not a multiple of 16")
        
        # Decrypt with the same IV that was used for encryption
        cipher = AES.new(self.KEY, AES.MODE_CBC, self.iv)
        decrypted_body = cipher.decrypt(encrypted_body)
        print(f"DEBUG V2: Decrypted body (before unpadding): {decrypted_body.hex()}")
        
        # Remove zero-padding: find actual data length from Modbus structure
        # Response format: [Slave][Func][ByteCount][Data...][CRC_LO][CRC_HI][Padding...]
        if len(decrypted_body) >= 3 and (decrypted_body[1] & 0xFF) > 0x80:
            raise ModbusError(f'V2 Exception: {decrypted_body[2]}')

        if len(decrypted_body) < 5:
            raise ModbusError('V2 response too short')

        byte_count = decrypted_body[2]
        data_end = 3 + byte_count
        data = decrypted_body[3:data_end]

        if len(decrypted_body) >= data_end + 2:
            expected_crc = int.from_bytes(decrypted_body[data_end:data_end + 2], byteorder='little')
            if modbus_crc(decrypted_body[:data_end]) != expected_crc:
                raise ModbusError('V2 response inner CRC mismatch')

        return data

class WriteSingleRegisterV2(WriteSingleRegister):
    KEY = b"sxd_aiot_key_001"
    # IV is now generated dynamically per command instead of hardcoded

    def __init__(self, address: int, value: int, slave_id: int = 1):
        if not HAS_CRYPTO:
            raise ImportError("Crypto library required for V2 protocol")
        
        super().__init__(address, value, slave_id=slave_id)
        self.slave_id = slave_id # Store slave_id as an instance attribute
        
        # Generate a dynamic IV for this command (4 random bytes → MD5 hash)
        self.iv = generate_iv()
        print(f"DEBUG V2 Write: Generated IV: {self.iv.hex()}")
        
        # 1. Build Modbus PDU: [Slave][Func][Addr_H][Addr_L][Val_H][Val_L]
        pdu = struct.pack('!BBHH', slave_id, 6, address, value)
        print(f"DEBUG V2 Write: Plaintext PDU: {pdu.hex()}")
        
        # 2. Append inner Modbus CRC (little-endian)
        inner_crc = modbus_crc(pdu)
        pdu_with_crc = pdu + inner_crc.to_bytes(2, byteorder='little')
        print(f"DEBUG V2 Write: PDU with inner CRC: {pdu_with_crc.hex()}")

        # 3. Encrypt with AES-CBC/NoPadding (zero-pad to 16-byte boundary)
        padded_pdu = zero_pad(pdu_with_crc, 16)
        print(f"DEBUG V2 Write: Padded PDU (zero-padding): {padded_pdu.hex()}")
        
        cipher = AES.new(self.KEY, AES.MODE_CBC, self.iv)
        encrypted_payload = cipher.encrypt(padded_pdu)
        print(f"DEBUG V2 Write: Encrypted payload: {encrypted_payload.hex()}")

        # 4. Build V2 Frame Header (10 bytes)
        # Structure: [0x00][0x17][SlaveID][CommandType (0x18)][PayloadLen_H][PayloadLen_L][Reserved (4 bytes)]
        payload_len = len(encrypted_payload)
        header = bytearray([
            0x00,           # Protocol ID Byte 1
            0x17,           # Protocol ID Byte 2 (V2 Protocol)
            self.slave_id,  # Slave ID
            0x18,           # Command Type for Write Single (0x18)
            (payload_len >> 8) & 0xFF,  # Length High
            payload_len & 0xFF,         # Length Low
            0x00, 0x00, 0x00, 0x00      # Reserved (could contain IV info if needed)
        ])
        print(f"DEBUG V2 Write: Header: {header.hex()}")

        # 5. Concatenate header and encrypted payload for CRC calculation
        self.cmd = header + encrypted_payload

        # 6. CRC calculated over the entire packet (Header + Payload)
        crc = bluetti_custom_crc(self.cmd)
        print(f"DEBUG V2 Write: Calculated Write V2 CRC: {hex(crc)}")
        self.cmd.extend(struct.pack('!H', crc))
        print(f"DEBUG V2 Write: Final packet: {self.cmd.hex()}")

    def response_size(self):
        # Response: [Header: 10] [EncryptedBody: 16] [CRC: 2]
        # FC 6 PDU is 6 bytes -> 1 block (16 bytes with zero-padding)
        return 28

    def is_valid_response(self, response: bytes):
        if len(response) < 12:
            return False
        crc = bluetti_custom_crc(response[:-2])
        return crc == struct.unpack('!H', response[-2:])[0]

    def is_exception_response(self, response: bytes):
        return False

    def parse_response(self, response: bytes):
        encrypted_body = response[10:-2]
        # Decrypt with the same IV that was used for encryption
        cipher = AES.new(self.KEY, AES.MODE_CBC, self.iv)
        decrypted_body = cipher.decrypt(encrypted_body)
        print(f"DEBUG V2 Write: Decrypted body (before unpadding): {decrypted_body.hex()}")
        
        # Remove zero-padding: find actual data length from Modbus structure
        # Response format: [Slave][Func][Address_H][Address_L][Value_H][Value_L][CRC_LO][CRC_HI][Padding...]
        if len(decrypted_body) < 6:
            raise ModbusError('V2 response too short')
        if (decrypted_body[1] & 0xFF) > 0x80:
            raise ModbusError(f'V2 Exception: {decrypted_body[2]}')

        if len(decrypted_body) >= 8:
            payload = decrypted_body[:6]
            expected_crc = int.from_bytes(decrypted_body[6:8], byteorder='little')
            if modbus_crc(payload) != expected_crc:
                raise ModbusError('V2 response inner CRC mismatch')

        return struct.unpack('!H', decrypted_body[4:6])[0]


def get_command_fields(args: Namespace) -> List[Dict[str, Any]]:
    with open(args.config, "r") as config_file:
        config = json.load(config_file)
        return config


def get_target_slave_id(cmd: Dict[str, Any]) -> int:
    """Get the target slave ID for a command, defaulting to 1."""
    return cmd.get('slave_id', 1)


def detect_device_protocol(device_name: str) -> str:
    """Detect which Modbus protocol a device supports based on its name."""
    if not device_name:
        return "v1"  # Default to V1 if no name

    name_lower = device_name.lower()

    # Known V2 devices (EP2000, EP600, etc.)
    v2_devices = [
        "ep2000", "ep600", "ep760", "ac300", "ac500",
        "bluetti_ep2000", "bluetti_ep600", "bluetti_ep760",
        "bluetti_ac300", "bluetti_ac500"
    ]

    # Check for V2 device patterns
    if any(pattern in name_lower for pattern in v2_devices):
        return "v2"

    # Check for manufacturer indicators that suggest V2
    if "bluetti" in name_lower and any(model in name_lower for model in ["ep", "ac"]):
        return "v2"

    # Default to V1 for unknown devices
    return "v1"


def get_slave_validation_register(group: Dict[str, Any]) -> int:
    """Choose a stable register to read when switching Modbus slave IDs."""
    slave_id = group.get('slave_id', 1)
    if slave_id in (1, 2):
        return INVERTER_MODEL_ID_REGISTER

    if 41 <= slave_id <= 56:
        start_reg = group.get('start_reg', 0)
        if 7200 <= start_reg < 7300 or any(7200 <= cmd.get('reg', 0) < 7300 for cmd in group.get('commands', [])):
            return BMU_LOGIC_MODEL_ID_REGISTER
        return BATTERY_PACK_MODEL_ID_REGISTER

    return 1


def build_slave_validation_command(group: Dict[str, Any], device_protocol: str):
    """Build the best validation read command for a slave switch."""
    slave_id = group.get('slave_id', 1)
    validation_reg = get_slave_validation_register(group)
    # BMU/Battery range on v2 devices often responds to plaintext reads
    if 41 <= slave_id <= 56:
        return ReadHoldingRegisters(validation_reg, 1, slave_id=slave_id)

    if device_protocol == "v1" or not group.get('encrypted', False):
        return ReadHoldingRegisters(validation_reg, 1, slave_id=slave_id)

    return ReadHoldingRegistersV2(validation_reg, 1, slave_id=slave_id)


def group_commands(commands_to_poll: List[Dict[str, Any]], max_gap: int = 5, max_group_size: int = 32) -> List[Dict[str, Any]]:
    """Groups individual register commands into larger reads to improve polling efficiency."""
    if not commands_to_poll:
        return []

    # Sort commands by register to enable grouping
    sorted_commands = sorted(commands_to_poll, key=lambda x: (
        get_target_slave_id(x), 
        x.get('trigger_reg', 0) or 0, 
        x.get('trigger_val', 0) or 0, 
        x['reg']
    ))
    
    groups = []
    current_group = []
    current_group_encrypted = False
    current_group_slave_id = 1
    current_group_trigger_reg = None
    current_group_trigger_val = None

    def get_num_regs(cmd):
        length = cmd.get('len', 1)
        is_ascii = cmd.get('ascii', False)
        # For non-ascii, length is in bits, so we divide by 16 to get register count
        return length // 16 if not is_ascii and length >= 16 else length
    
    def is_encrypted(cmd):
        # Check explicit flag or infer from notes
        if cmd.get('encrypted', False):
            return True
        notes = cmd.get('notes', '').lower()
        return "v2 protocol" in notes or "may be encrypted" in notes

    for cmd in sorted_commands:
        cmd_encrypted = is_encrypted(cmd)
        cmd_slave_id = get_target_slave_id(cmd)
        cmd_trigger_reg = cmd.get('trigger_reg')
        cmd_trigger_val = cmd.get('trigger_val')

        if not current_group:
            current_group.append(cmd)
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id
            current_group_trigger_reg = cmd_trigger_reg
            current_group_trigger_val = cmd_trigger_val
            continue

        group_start_reg = current_group[0]['reg']
        last_cmd_in_group = current_group[-1]
        group_end_reg = last_cmd_in_group['reg'] + get_num_regs(last_cmd_in_group)

        gap = cmd['reg'] - group_end_reg
        new_group_size = (cmd['reg'] + get_num_regs(cmd)) - group_start_reg

        # Group if gap/size are okay AND encryption status matches AND slave ID matches AND triggers match
        if (gap >= 0 and gap <= max_gap and 
            new_group_size <= max_group_size and 
            cmd_encrypted == current_group_encrypted and
            cmd_slave_id == current_group_slave_id and
            cmd_trigger_reg == current_group_trigger_reg and
            cmd_trigger_val == current_group_trigger_val):
            current_group.append(cmd)
        else:
            groups.append(current_group)
            current_group = [cmd]
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id
            current_group_trigger_reg = cmd_trigger_reg
            current_group_trigger_val = cmd_trigger_val

    if current_group:
        groups.append(current_group)

    # Finalize group structure with start address and total register count for each group
    final_groups = []
    for group in groups:
        start_reg = group[0]['reg']
        encrypted = is_encrypted(group[0])
        slave_id = get_target_slave_id(group[0])
        trigger_reg = group[0].get('trigger_reg')
        trigger_val = group[0].get('trigger_val')
        
        # Find the end register of the group
        end_reg = 0
        for cmd in group:
            cmd_end = cmd['reg'] + get_num_regs(cmd)
            if cmd_end > end_reg:
                end_reg = cmd_end

        final_groups.append({
            'start_reg': start_reg,
            'num_regs': end_reg - start_reg,
            'commands': group,
            'encrypted': encrypted,
            'slave_id': slave_id,
            'trigger_reg': trigger_reg,
            'trigger_val': trigger_val
        })

    return final_groups


def process_and_publish(command_info: Dict[str, Any], data: bytes, device_name: str, mqtt_client: mqtt.Client, encrypted: bool):
    try:
        register = command_info['reg']
        slave_id = get_target_slave_id(command_info)
        length = command_info.get('len', 1)
        if not isinstance(length, int): # Handle cases where len might be malformed
            length = 1
        is_ascii = command_info.get('ascii', False)
        is_split = 'outputs' in command_info
        outputs = command_info.get('outputs', [command_info])

        # Parse the sliced data
        base_value = None
        output_format = command_info.get('format')
        output_type = command_info.get('type')

        if output_type == 'float':
            swapped_data = data[2:4] + data[0:2]
            base_value = struct.unpack('>f', swapped_data)[0]
            base_value = round(base_value, 3)
        elif output_format == 'ipv4':
            octets = data if len(data) == 4 else [data[i] for i in range(1, len(data), 2)]
            base_value = '.'.join(map(str, octets))
        elif output_format == 'mac':
            octets = data if len(data) == 6 else [data[i] for i in range(1, len(data), 2)]
            base_value = ':'.join(f'{o:02X}' for o in octets)
        elif is_ascii:
            base_value = bytes_to_ascii(data)
        elif length == 32 and not is_ascii:
            # V2 protocol 32-bit values use CDAB byte order (word-swapped)
            # Example: raw_data[0,1,2,3] -> (data[2]<<24) | (data[3]<<16) | (data[0]<<8) | data[1]
            base_value = (data[2] << 24) | (data[3] << 16) | (data[0] << 8) | data[1]
        elif length >= 32 and length % 16 == 0 and not is_ascii:
            # Legacy behavior: assume 32-bit+ values are word-swapped unless 'no_word_swap' is set.
            # This is for compatibility with older device definitions.
            if not command_info.get('no_word_swap', False):
                words = bytes_to_words(data)
                base_value = 0
                for i, word in enumerate(words):
                    base_value |= word << (i * 16)
            else:
                # If word swapping is off, parse as a standard big-endian integer
                base_value = int.from_bytes(data, 'big')
        else:
            base_value = int.from_bytes(data, 'little' if command_info.get('byte_swap', False) else 'big')

        # Process and Publish Outputs
        for output in outputs:
            value = base_value
            if not is_ascii and isinstance(value, int):
                if 'offset' in output: value >>= output['offset']
                if 'mask' in output: value &= output['mask']
                if output.get('signed', False) and not is_split:
                    if length == 32: value = to_32bit_signed(value)
                    elif length == 16: value = to_signed(value)
                if 'subtract' in output: value -= output['subtract']
                if 'scale' in output: value = apply_scale(value, output['scale'])
                if output.get('type') == 'decimal': value = str(value)
                if 'values' in output and isinstance(value, int) and 0 <= value < len(output['values']):
                    value = output['values'][value]

            slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
            topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
            state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}{slave_suffix}/state"
            state_payload = {
                "value": value, 
                "PossibleName": output['name'], 
                "modbus_register": f"{register}{topic_suffix}",
                "slave_id": slave_id,
                "encrypted": encrypted,
                "valid": True
            }
            if 'notes' in output: state_payload["notes"] = output['notes']

            mqtt_client.publish(state_topic, json.dumps(state_payload))
            print(f"Published {register}{topic_suffix} (Slave {slave_id}) ({output['name']}): {value}")

    except Exception as e:
        print(f"An error occurred while processing register {command_info.get('reg')}: {e}")


def publish_invalid(command_info: Dict[str, Any], device_name: str, mqtt_client: mqtt.Client, encrypted: bool):
    register = command_info['reg']
    slave_id = get_target_slave_id(command_info)
    is_split = 'outputs' in command_info
    outputs = command_info.get('outputs', [command_info])
    for output in outputs:
        slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
        topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}{slave_suffix}/state"
        state_payload = {
            "value": None,
            "PossibleName": output['name'],
            "modbus_register": f"{register}{topic_suffix}",
            "slave_id": slave_id,
            "encrypted": encrypted,
            "valid": False
        }
        if 'notes' in output:
            state_payload["notes"] = output['notes']
        mqtt_client.publish(state_topic, json.dumps(state_payload))

# Filter to suppress noisy tracebacks from the background Bluetooth client
class BriefConnectionErrors(logging.Filter):
    def filter(self, record):
        if "Error connecting to device" in record.getMessage() and record.exc_info:
            # Remove the traceback to reduce noise
            record.exc_info = None
            # Append a note so user knows it is retrying
            record.msg = f"{record.msg} - Retrying..."
        return True


async def poll_device_registers(
    client: BluetoothClient,
    client_task: asyncio.Task,
    commands_to_poll: List[Dict[str, Any]],
    device_name: str,
    mqtt_client: mqtt.Client,
    device_address: str,
    slave_switch_delay: float = 2.0,
    disconnect_on_slave_change: bool = False,
    max_group_size: int = 32,
    force_protocol: Optional[str] = None,
    debug_logging: bool = False,
    plaintext_slaves: Set[int] = set(),
) -> float:
    """
    Poll device registers and publish to MQTT.

    This function contains the core polling logic extracted from async_main
    to make it testable and reusable.

    Args:
        client: Connected BluetoothClient instance
        client_task: The asyncio task running the client
        commands_to_poll: List of command configurations to poll
        device_name: Device name for MQTT topics
        mqtt_client: MQTT client for publishing
        device_address: Device Bluetooth address
        slave_switch_delay: Delay when switching slave IDs
        disconnect_on_slave_change: Whether to disconnect/reconnect on slave changes
        max_group_size: Maximum registers per Modbus read command

    Returns:
        Duration of the polling operation in seconds
    """
    # Detect device protocol
    if force_protocol:
        device_protocol = force_protocol
        logging.info(f"Forced protocol: {device_protocol}")
    else:
        device_protocol = detect_device_protocol(device_name)
        logging.info(f"Detected protocol for {device_name}: {device_protocol}")

    # Parse plaintext slaves list
    if plaintext_slaves:
        logging.info(f"Forcing plaintext for slaves: {sorted(plaintext_slaves)}")

    # Group commands for polling
    grouped_commands = group_commands(commands_to_poll, max_group_size=max_group_size)

    # Start the timer
    start_time = time.perf_counter()

    print(f"Polling {len(commands_to_poll)} registers in {len(grouped_commands)} groups...")

    previous_slave_id = 1

    for group in grouped_commands:
        slave_id = group.get('slave_id', 1)
        print(f"Preparing polling command for {group['start_reg']} count {group['num_regs']} (Slave {slave_id})")

        # Sleep only if we are switching to a different slave
        if slave_id != previous_slave_id:
            if disconnect_on_slave_change:
                print(f"Switching from Slave {previous_slave_id} to {slave_id}: Reconnecting...")
                # Stop existing client
                client_task.cancel()
                try:
                    await client_task
                except asyncio.CancelledError:
                    pass

                # Wait briefly for OS stack to clear
                await asyncio.sleep(2.0)

                # Recreate client
                client = BluetoothClient(device_address, debug_logging=debug_logging)
                client_task = asyncio.create_task(client.run())

                while not client.is_ready:
                    await asyncio.sleep(0.1)
                print("Reconnected.")
            else:
                print(f"Switching from Slave {previous_slave_id} to {slave_id}, sleeping for {slave_switch_delay}s...")
                await asyncio.sleep(slave_switch_delay)

                # Perform a lightweight slave validation read using a stable model or BMU register.
                validation_reg = get_slave_validation_register(group)
                print(f"  Performing slave validation read for Slave {slave_id} at register {validation_reg}...")
                validation_cmd = build_slave_validation_command(group, device_protocol)
                validation_success = False
                try:
                    future = await client.perform(validation_cmd)
                    await future
                    validation_success = True
                except BadConnectionError as e:
                    # For BMU/battery slaves, try the alternate V2 form if plaintext first fails.
                    if isinstance(validation_cmd, ReadHoldingRegisters) and device_protocol == "v2":
                        try:
                            alt_cmd = ReadHoldingRegistersV2(validation_reg, 1, slave_id=slave_id)
                            future = await client.perform_with_fallback(alt_cmd, device_protocol)
                            await future
                            validation_success = True
                        except Exception:
                            pass
                    if not validation_success:
                        print(f"  Slave validation read ignored: {e}")
                except Exception as e:
                    print(f"  Slave validation read ignored: {e}")
                await asyncio.sleep(0.05)

        previous_slave_id = slave_id

        # Handle Trigger Register Write if defined for this group
        if group.get('trigger_reg') is not None:
            t_reg = group['trigger_reg']
            t_val = group['trigger_val']
            print(f"  --- TRIGGER START ---")
            print(f"  Action: Write {t_val} to {t_reg} (Slave {slave_id})")

            if group['encrypted'] and HAS_CRYPTO and slave_id not in plaintext_slaves:
                # Show the full Modbus PDU that will be encrypted
                pdu = struct.pack('!BBHH', slave_id, 6, t_reg, t_val)
                print(f"    Plaintext PDU: {pdu.hex()}")
                trigger_cmd = WriteSingleRegisterV2(t_reg, t_val, slave_id=slave_id)
            else:
                pdu = struct.pack('!HH', t_reg, t_val)
                print(f"    Plaintext Payload: {pdu.hex()}")
                trigger_cmd = WriteSingleRegister(t_reg, t_val, slave_id=slave_id)

            tx_packet = bytes(trigger_cmd)
            tx_type = "Encrypted" if (group['encrypted'] and HAS_CRYPTO and slave_id not in plaintext_slaves) else "Plaintext"
            if slave_id in plaintext_slaves:
                tx_type += f" (forced for slave {slave_id})"
            print(f"    TX Packet ({tx_type}): {tx_packet.hex()}")

            try:
                t_future = await client.perform_with_fallback(trigger_cmd, device_protocol)
                t_res = cast(bytes, await t_future)

                if t_res:
                    print(f"    RX Packet: {t_res.hex()}")
                print("    Result: Success (Write accepted)")
                await asyncio.sleep(0.1) # Brief pause before reading stats
            except Exception as e:
                print(f"    Result: Failed - {e}")
            print(f"  --- TRIGGER END ---")

        if group['encrypted'] and HAS_CRYPTO and slave_id not in plaintext_slaves:
            command = ReadHoldingRegistersV2(group['start_reg'], group['num_regs'], slave_id=slave_id)
        else:
            command = ReadHoldingRegisters(group['start_reg'], group['num_regs'], slave_id=slave_id)

        tx_type = "Encrypted" if (group['encrypted'] and HAS_CRYPTO and slave_id not in plaintext_slaves) else "Plaintext"
        if slave_id in plaintext_slaves:
            tx_type += f" (forced for slave {slave_id})"
        print(f"  TX Packet ({tx_type}): {bytes(command).hex()}")
        try:
            print(f"  Attempting {tx_type} command...")
            future = await client.perform_with_fallback(command, device_protocol)
            response = cast(bytes, await future)
            print(f"  ✓ {tx_type} command succeeded")

            if len(response) > 0:
                 print(f"  RX Packet: SlaveID={response[0]} Func={response[1]} Len={len(response)}")

            if len(response) > 0 and response[0] != slave_id:
                print(f"  [WARN] Response Unit ID {response[0]} does not match requested {slave_id}!")

            group_data = command.parse_response(response)
            print(f"Read group (Slave {slave_id}) starting at {group['start_reg']} raw: {group_data.hex()}")

            for command_info in group['commands']:
                try:
                    register = command_info['reg']
                    length = command_info.get('len', 1)
                    is_ascii = command_info.get('ascii', False)
                    is_split = 'outputs' in command_info
                    outputs = command_info.get('outputs', [command_info])

                    # Determine number of registers for this command and slice data
                    num_registers = length // 16 if not is_ascii and length >= 16 else length
                    start_byte = (register - group['start_reg']) * 2
                    end_byte = start_byte + (num_registers * 2)
                    data = group_data[start_byte:end_byte]
                    process_and_publish(command_info, data, device_name, mqtt_client, group['encrypted'])
                except Exception as e:
                    print(f"An error occurred while processing register {command_info.get('reg')}: {e}")

        except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
            print(f"Error polling group starting at {group['start_reg']}: {e}. Falling back to individual polling.")
            # Fallback: Try polling each command in the group individually
            for command_info in group['commands']:
                try:
                    # Recalculate if this specific command is encrypted
                    cmd_encrypted = group['encrypted'] # Assume same as group
                    register = command_info['reg']
                    length = command_info.get('len', 1)
                    is_ascii = command_info.get('ascii', False)
                    num_registers = length // 16 if not is_ascii and length >= 16 else length
                    slave_id = get_target_slave_id(command_info)

                    if cmd_encrypted and HAS_CRYPTO and slave_id not in plaintext_slaves:
                        single_command = ReadHoldingRegistersV2(register, num_registers, slave_id=slave_id)
                    else:
                        single_command = ReadHoldingRegisters(register, num_registers, slave_id=slave_id)

                    tx_type = "Encrypted" if (cmd_encrypted and HAS_CRYPTO and slave_id not in plaintext_slaves) else "Plaintext"
                    if slave_id in plaintext_slaves:
                        tx_type += f" (forced for slave {slave_id})"
                    print(f"  TX Packet ({tx_type}): {bytes(single_command).hex()}")
                    future = await client.perform(single_command)
                    response = cast(bytes, await future)
                    if len(response) > 0:
                        print(f"  RX Packet: SlaveID={response[0]} Func={response[1]} Len={len(response)}")
                    data = single_command.parse_response(response)
                    process_and_publish(command_info, data, device_name, mqtt_client, cmd_encrypted)
                except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
                    print(f"Error individual polling register {command_info['reg']}: {e}")

                    # Fallback 2: If encrypted failed, try plaintext
                    success_plaintext = False
                    if cmd_encrypted and HAS_CRYPTO:
                        try:
                            print(f"Retrying register {command_info['reg']} with plaintext...")
                            single_command = ReadHoldingRegisters(register, num_registers, slave_id=slave_id)
                            print(f"  TX Packet (Plaintext): {bytes(single_command).hex()}")
                            future = await client.perform(single_command)
                            response = cast(bytes, await future)
                            if len(response) > 0:
                                print(f"  RX Packet: SlaveID={response[0]} Func={response[1]} Len={len(response)}")
                            data = single_command.parse_response(response)
                            process_and_publish(command_info, data, device_name, mqtt_client, False)
                            success_plaintext = True
                        except Exception as e2:
                            print(f"Error plaintext fallback register {command_info['reg']}: {e2}")

                    if success_plaintext:
                        continue
                    publish_invalid(command_info, device_name, mqtt_client, cmd_encrypted)

    # Calculate duration
    end_time = time.perf_counter()
    duration = end_time - start_time
    return duration


async def async_main():  # noqa: C901
    """Main program function."""
    parser = ArgumentParser(
        description="Scans for Bluetti devices and connects to them to poll registers"
    )
    parser.add_argument(
        "config", type=str, help="Path to the debugger JSON config file"
    )
    parser.add_argument("address", nargs="?", help="Bluetti device address to connect to")
    parser.add_argument("--scan-interval", type=int, default=60, help="Scan interval in seconds")
    parser.add_argument("--mqtt-broker", type=str, default="localhost", help="MQTT broker address")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-username", type=str, help="MQTT username")
    parser.add_argument("--mqtt-password", type=str, help="MQTT password")
    parser.add_argument("--slave-switch-delay", type=float, default=2.0, help="Delay in seconds when switching slave IDs")
    parser.add_argument("--disconnect-on-slave-change", action="store_true", help="Disconnect and reconnect Bluetooth when switching slaves (slow but reliable)")
    parser.add_argument("--max-group-size", type=int, default=32, help="Max registers per Modbus read command")
    parser.add_argument("--force-protocol", choices=["v1", "v2"], help="Force specific protocol version (v1=plaintext, v2=encrypted)")
    parser.add_argument("--plaintext-slaves", type=str, help="Comma-separated list of slave IDs to force plaintext protocol (e.g., '41,42,43')")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    # Configure logging to catch the background client errors and suppress tracebacks
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=log_level, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S')
    logging.getLogger().addFilter(BriefConnectionErrors())

    mqtt_client = mqtt.Client()
    if args.mqtt_username:
        mqtt_client.username_pw_set(args.mqtt_username, args.mqtt_password)
    mqtt_client.connect(args.mqtt_broker, args.mqtt_port, 60)
    mqtt_client.loop_start()

    try:
        device = None
        while not device:
            if args.address:
                print(f"Checking for device at {args.address}...")
                devices = await check_addresses({args.address})
            else:
                print("Scanning for devices...")
                devices = await scan_devices()

            if devices:
                device = devices[0]
            else:
                print("No devices found. Retrying in 60 seconds...")
                await asyncio.sleep(60)

        display_name = f"{device.type} {device.sn} debug"
        print(f"Connecting to {display_name} at {device.address}...")
        client = BluetoothClient(device.address, device_name=display_name, debug_logging=args.debug)
        client_task = asyncio.create_task(client.run())
        device_name = display_name.replace(" ", "_").lower()

        last_config_mtime = 0
        commands_to_poll = []
        waiting_for_connection = False

        while True:
            if not client.is_ready:
                if not waiting_for_connection:
                    print("Waiting for connection...")
                    waiting_for_connection = True
                await asyncio.sleep(1)
                continue
            
            if waiting_for_connection:
                 print("Connected!")
                 waiting_for_connection = False

            try:
                current_config_mtime = os.path.getmtime(args.config)
                if current_config_mtime != last_config_mtime:
                    print("Config file has changed. Reloading and running discovery...")
                    last_config_mtime = current_config_mtime
                    commands_to_poll = get_command_fields(args)

                    # Perform Home Assistant discovery
                    print(f"Publishing {len(commands_to_poll)} Home Assistant auto-discovery configs...")
                    for command_info in commands_to_poll:
                        register = command_info['reg']
                        outputs = command_info.get('outputs', [command_info])
                        is_split = 'outputs' in command_info

                        for output in outputs:
                            output_name = output['name']
                            slave_id = get_target_slave_id(command_info)
                            slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
                            topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
                            id_suffix = f"_{output.get('offset', 0)}" if is_split else ""
                            unique_id = f"{device_name}_{register}{id_suffix}{slave_suffix}"
                            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
                            state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}{slave_suffix}/state"

                            payload = {
                                "name": f"{register} {output_name}",
                                "state_topic": state_topic,
                                "unique_id": unique_id,
                                "json_attributes_topic": state_topic,
                                "value_template": "{{ value_json.value }}",
                                "device": {
                                    "identifiers": [device.address],
                                    "name": display_name,
                                    "model": device.type,
                                    "manufacturer": "Bluetti"
                                }
                            }
                            if 'device_class' in output:
                                payload['device_class'] = output['device_class']
                            if 'unit' in output:
                                payload['unit_of_measurement'] = output['unit']

                            mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)

            except Exception as e:
                print(f"Error loading or processing config file: {e}")
                await asyncio.sleep(args.scan_interval)
                continue

            # Parse plaintext slaves
            plaintext_slaves = set()
            if hasattr(args, 'plaintext_slaves') and args.plaintext_slaves:
                try:
                    plaintext_slaves = set(int(s.strip()) for s in args.plaintext_slaves.split(','))
                except ValueError:
                    logging.warning(f"Invalid plaintext-slaves format: {args.plaintext_slaves}")

            # Poll device registers using the extracted function
            duration = await poll_device_registers(
                client=client,
                client_task=client_task,
                commands_to_poll=commands_to_poll,
                device_name=device_name,
                mqtt_client=mqtt_client,
                device_address=device.address,
                slave_switch_delay=args.slave_switch_delay,
                disconnect_on_slave_change=args.disconnect_on_slave_change,
                max_group_size=args.max_group_size,
                force_protocol=args.force_protocol,
                debug_logging=args.debug,
                plaintext_slaves=plaintext_slaves,
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Polling complete in {duration:.2f} seconds. Waiting for {args.scan_interval} seconds...")
            await asyncio.sleep(args.scan_interval)

    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("MQTT client disconnected.")


def main():
    """Synchronous entry point for the debugger."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nDebugger stopped by user.")

if __name__ == "__main__":
    main()