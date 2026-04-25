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

# Track discovered dynamic registers to avoid redundant discovery messages
DISCOVERED_DYNAMIC_REGS = set()

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
    return swap_bytes(response_bytes).decode('ascii', errors='replace').strip('\x00')


def get_topic_suffix(output: Dict[str, Any], is_split: bool) -> str:
    """Calculate suffix based on bit offset only."""
    if not is_split:
        return ""
    if 'offset' in output:
        return f".{output['offset']}"
    
    # If this is a split register (is_split=True) but we DON'T have a 
    # register offset, we use .0 as a safety suffix to prevent collisions 
    # with the base register topic. If we DO have a reg_offset, the 
    # display_reg already handles uniqueness.
    if 'reg_offset' not in output:
        return ".0"
    return ""


def get_id_suffix(output: Dict[str, Any], is_split: bool) -> str:
    """Calculate Unique ID suffix (underscores instead of dots)."""
    return get_topic_suffix(output, is_split).replace('.', '_')


def get_display_register(base_reg: Any, output: Dict[str, Any]) -> str:
    """Calculate the display register address including offsets."""
    reg_offset = output.get('reg_offset', 0)
    if reg_offset == 0:
        return str(base_reg)

    if isinstance(base_reg, str) and '.' in base_reg:
        parts = base_reg.split('.')
        try:
            actual_reg = int(parts[0]) + reg_offset
            return ".".join([str(actual_reg)] + parts[1:])
        except ValueError:
            pass

    if isinstance(base_reg, int):
        return str(base_reg + reg_offset)

    return f"{base_reg}.{reg_offset}"


class ReadHoldingRegistersV2(ReadHoldingRegisters):
    # KEY is now provided per instance from ECDH session

    def __init__(self, starting_address: int, quantity: int, slave_id: int = 1, session_key: bytes = None):
        if not HAS_CRYPTO:
            raise ImportError("Crypto library required for V2 protocol")
        
        super().__init__(starting_address, quantity, slave_id=slave_id)
        self.slave_id = slave_id # Store slave_id as an instance attribute
        
        # Use provided session key or fallback to hardcoded (for testing)
        self.key = session_key or b"sxd_aiot_key_001"
        
        # Generate IV for this command
        self.iv = generate_iv()
        
        # Get the MODBUS PDU from parent class
        pdu = bytes(self.cmd)  # The full PDU with CRC from parent
        print(f"DEBUG V2 Read: Plaintext PDU: {pdu.hex()}")
        
        # Zero-pad to 16-byte boundary for AES
        padded_pdu = zero_pad(pdu)
        print(f"DEBUG V2 Read: Padded PDU (zero-padding): {padded_pdu.hex()}")
        
        # Create AES cipher for encryption
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        encrypted_payload = cipher.encrypt(padded_pdu)
        print(f"DEBUG V2 Read: Encrypted payload: {encrypted_payload.hex()}")

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
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
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
    # KEY is now provided per instance from ECDH session

    def __init__(self, address: int, value: int, slave_id: int = 1, session_key: bytes = None):
        if not HAS_CRYPTO:
            raise ImportError("Crypto library required for V2 protocol")
        
        super().__init__(address, value, slave_id=slave_id)
        self.slave_id = slave_id # Store slave_id as an instance attribute
        
        # Use provided session key or fallback to hardcoded (for testing)
        self.key = session_key or b"sxd_aiot_key_001"
        
        # Generate IV for this command
        self.iv = generate_iv()
        
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
        
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
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
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
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
        if not isinstance(config, list):
            return []

        flat_config = []

        def process_bulk_read(bulk_item: Dict[str, Any], parent_slave: int, triggers: List = None, trigger_val: Any = None, default_delay: float = 0.2):
            """Helper to extract registers from a bulk/grouped read container."""
            regs = []
            b_slave = bulk_item.get("slave_id", bulk_item.get("slave", parent_slave))
            b_delay = bulk_item.get("delay", default_delay)
            child_regs = bulk_item.get("registers", [])
            
            for r in child_regs:
                # Copy to avoid modifying the original list if shared
                r_copy = r.copy()
                # Apply metadata from trigger/bulk context
                r_copy.update({
                    "triggers": triggers or [],
                    "trigger_val": trigger_val,
                    "trigger_delay": b_delay
                })
                if triggers:
                    r_copy["trigger_reg"] = triggers[0]["reg"]
                if "slave_id" not in r_copy and "slave" not in r_copy:
                    r_copy["slave_id"] = b_slave
                regs.append(r_copy)
            return regs

        for item in config:
            if not isinstance(item, dict):
                continue

            # Handle Standalone Bulk/Grouped Reads
            if "bulk_read" in item or "grouped_read" in item:
                bulk_data = item.get("bulk_read", item.get("grouped_read"))
                flat_config.extend(process_bulk_read(bulk_data, item.get("slave_id", item.get("slave", 1))))
            
            # Handle Trigger Writes (which can now contain bulk_reads)
            elif "trigger_write" in item:
                trigger_data = item.get("trigger_write", [])
                triggers, delay, regs, p_info = [], 0.2, [], None 
                item_slave = item.get("slave_id", item.get("slave", 1))
                
                for component in trigger_data:
                    if "trigger_metadata" in component:
                        m = component["trigger_metadata"]
                        # Detect Pagination
                        if "pagination_selector" in m:
                            p_info = {
                                "selector": m["pagination_selector"], 
                                "count_reg": m["pagination_count_reg"],
                                "slave_id": m.get("slave_id", m.get("slave", item_slave))
                            }
                        
                        reg = m.get("trigger_reg")
                        val = m.get("trigger_value", m.get("trigger_val"))
                        t_slave = m.get("slave_id", m.get("slave", item_slave))
                        if reg is not None:
                            triggers.append({"reg": reg, "val": val, "slave_id": t_slave})

                    # Support both the old post_trigger_read and the new bulk_read keyword
                    for k in ["bulk_read", "grouped_read", "post_trigger_read"]:
                        if k in component:
                            primary_val = triggers[0]["val"] if triggers else None
                            # Category 2026 is often the primary selector for unique topics
                            for t in triggers:
                                if t["reg"] == 2026:
                                    primary_val = t["val"]
                                    break
                            
                            # Handle case where 'registers' is a sibling of the keyword (common in existing config)
                            bulk_data = component[k]
                            if "registers" not in bulk_data and "registers" in component:
                                bulk_data = bulk_data.copy()
                                bulk_data["registers"] = component["registers"]
                            
                            extracted = process_bulk_read(bulk_data, item_slave, triggers, primary_val)
                            if p_info:
                                for r in extracted:
                                    r.update({
                                        "pagination_selector": p_info["selector"], 
                                        "pagination_count_reg": p_info["count_reg"],
                                        "pagination_slave": p_info["slave_id"]
                                    })
                            regs.extend(extracted)
                flat_config.extend(regs)
            else:
                flat_config.append(item)
        return flat_config


def get_target_slave_id(cmd: Dict[str, Any]) -> int:
    """Get the target slave ID for a command, defaulting to 1."""
    return cmd.get('slave_id', cmd.get('slave', 1))


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


def build_slave_validation_command(group: Dict[str, Any], device_protocol: str, client: BluetoothClient = None):
    """Build the best validation read command for a slave switch."""
    slave_id = group.get('slave_id', 1)
    validation_reg = get_slave_validation_register(group)
    # BMU/Battery range on v2 devices often responds to plaintext reads
    if 41 <= slave_id <= 56:
        return ReadHoldingRegisters(validation_reg, 1, slave_id=slave_id)

    return ReadHoldingRegisters(validation_reg, 1, slave_id=slave_id)


def group_commands(commands_to_poll: List[Dict[str, Any]], max_gap: int = 5, max_group_size: int = 32) -> List[Dict[str, Any]]:
    """Groups individual register commands into larger reads to improve polling efficiency."""
    if not commands_to_poll:
        return []

    def get_trigger_key(cmd):
        return tuple((t['reg'], t['val']) for t in cmd.get('triggers', []))

    # Sort commands by register to enable grouping
    sorted_commands = sorted(commands_to_poll, key=lambda x: (
        get_target_slave_id(x), 
        get_trigger_key(x),
        x['reg']
    ))
    
    groups = []
    current_group = []
    current_group_encrypted = False
    current_group_slave_id = 1
    current_group_triggers = None

    def get_num_regs(cmd):
        length = cmd.get('len', 1)
        is_ascii = cmd.get('ascii', False)
        # For non-ascii, length is in bits, so we divide by 16 to get register count
        return length // 16 if not is_ascii and length >= 16 else length
    
    def is_encrypted(cmd):
        # Ignore any encryption metadata in the JSON config.
        # All Modbus traffic should be sent as plaintext.
        return False

    for cmd in sorted_commands:
        cmd_encrypted = is_encrypted(cmd)
        cmd_slave_id = get_target_slave_id(cmd)
        cmd_triggers = get_trigger_key(cmd)
        cmd_pag_selector = cmd.get('pagination_selector')

        if not current_group:
            current_group.append(cmd)
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id
            current_group_triggers = cmd_triggers
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
            cmd_triggers == current_group_triggers and
            cmd_pag_selector == current_group[0].get('pagination_selector')):
            current_group.append(cmd)
        else:
            groups.append(current_group)
            current_group = [cmd]
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id
            current_group_triggers = cmd_triggers

    if current_group:
        groups.append(current_group)

    # Finalize group structure with start address and total register count for each group
    final_groups = []
    for group in groups:
        start_reg = group[0]['reg']
        encrypted = is_encrypted(group[0])
        slave_id = get_target_slave_id(group[0])
        triggers = group[0].get('triggers', [])
        trigger_delay = group[0].get('trigger_delay', 0.2)
        p_selector = group[0].get('pagination_selector')
        p_count_reg = group[0].get('pagination_count_reg')
        
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
            'triggers': triggers,
            'trigger_delay': trigger_delay,
            'pagination_selector': p_selector,
            'pagination_count_reg': p_count_reg,
            'pagination_slave': group[0].get('pagination_slave')
        })

    return final_groups


def process_and_publish(command_info: Dict[str, Any], data: bytes, device_name: str, mqtt_client: mqtt.Client, encrypted: bool, discovery_info: Dict[str, Any] = None):
    try:
        register = command_info['reg']

        # Extract numeric base and optional suffix (e.g. "3002.p1" -> base=3002, suffix=".p1")
        # This allows arithmetic on register addresses even if they have been paginated.
        calc_reg = register
        reg_suffix = ""
        if isinstance(register, str) and '.' in register:
            parts = register.split('.')
            try:
                calc_reg = int(parts[0])
                reg_suffix = "." + ".".join(parts[1:])
            except ValueError:
                pass

        slave_id = get_target_slave_id(command_info)
        trigger_reg = command_info.get('trigger_reg')
        trigger_val = command_info.get('trigger_val')
        length = command_info.get('len', 1)
        if not isinstance(length, int): # Handle cases where len might be malformed
            length = 1
        is_ascii = command_info.get('ascii', False)
        is_split = 'outputs' in command_info
        outputs = command_info.get('outputs', [command_info])
        output_type = command_info.get('type')

        # Handle blocks driven by a count register (e.g. SysPhaseNumber)
        if output_type == 'repeating_count':
            count_offset = command_info.get('count_offset', 0)
            start_offset = command_info.get('start_offset', 1)
            block_regs = command_info.get('block_regs', 1)
            block_size = block_regs * 2

            # Read the loop count from the data
            count_data = data[count_offset*2 : count_offset*2 + 2]
            count = int.from_bytes(count_data, 'big')
            
            # Fallback: If count is 0, calculate based on payload length
            if count == 0:
                count = (len(data) // 2 - start_offset) // block_regs

            # Publish the count register itself first
            count_info = command_info.copy()
            count_info['type'] = 'numeric' # Prevent recursion
            process_and_publish(count_info, count_data, device_name, mqtt_client, encrypted, discovery_info)

            # Process each block
            for i in range(count):
                current_byte_start = (start_offset * 2) + (i * block_size)
                chunk = data[current_byte_start : current_byte_start + block_size]
                if len(chunk) < block_size:
                    break

                block_idx = i + 1
                # Calculate the actual starting register for this specific block
                block_reg = f"{calc_reg + start_offset + (i * block_regs)}{reg_suffix}.p{block_idx}"

                # Handle dynamic Home Assistant discovery for this new block
                _handle_dynamic_discovery(discovery_info, device_name, block_reg, slave_id, trigger_val, trigger_reg, outputs, is_split, mqtt_client)

                # Recursively process this chunk as a standalone command
                block_info = command_info.copy()
                block_info['type'] = 'processed_block' # Prevent infinite loop
                block_info['reg'] = block_reg
                process_and_publish(block_info, chunk, device_name, mqtt_client, encrypted, discovery_info)
            return

        # Handle interleaved/segmented tables (e.g. 7200 BMU block)
        if output_type == 'segmented_repeating':
            count = command_info.get('count', 1)
            segments = command_info.get('segments', [])
            
            # Process each Pack/Node
            for i in range(count):
                pack_idx = i + 1
                # We use a synthetic register name for the pack summary
                pack_reg_base = f"{register}.n{pack_idx}"
                
                # Iterate through defined segments for this specific node
                for seg in segments:
                    s_start = seg.get('start_offset', 0)
                    s_stride = seg.get('stride', 1)
                    s_outputs = seg.get('outputs', [])
                    
                    # Calculate where this node's data sits in this segment
                    # node_offset = start_of_segment + (node_index * registers_per_node)
                    node_offset = s_start + (i * s_stride)
                    
                    chunk = data[node_offset*2 : (node_offset + s_stride)*2]
                    if not chunk:
                        continue

                    # Handle discovery for this segment's outputs
                    if discovery_info:
                        _handle_dynamic_discovery(
                            discovery_info, 
                            device_name, 
                            pack_reg_base, 
                            slave_id, 
                            trigger_val, 
                            trigger_reg, 
                            s_outputs, 
                            True, 
                            mqtt_client
                        )

                    # Process the chunk as a standalone block
                    seg_info = command_info.copy()
                    seg_info.update({'type': 'processed_block', 'reg': pack_reg_base, 'outputs': s_outputs})
                    process_and_publish(seg_info, chunk, device_name, mqtt_client, encrypted, discovery_info)
            return

        # Handle dynamic packed arrays (e.g., BMU cells and NTCs, or any similar structure)
        if output_type == 'dynamic_packed_array':
            # Get configuration for the arrays
            arrays_config = command_info.get('arrays', [])
            
            current_offset = 0  # Track position in the data
            
            for array_config in arrays_config:
                array_name = array_config.get('name', 'Array')
                count_reg_offset = array_config.get('count_reg_offset', 0)
                count_byte = array_config.get('count_byte', 'low')  # 'low' or 'high'
                items_per_register = array_config.get('items_per_register', 1)
                array_outputs = array_config.get('outputs', [])
                
                # Read the count from the specified offset
                count_data_offset = count_reg_offset * 2
                if count_data_offset + 2 <= len(data):
                    count_word = data[count_data_offset:count_data_offset + 2]
                    # Extract count from the specified byte (low=byte 1, high=byte 0 in big-endian)
                    item_count = count_word[1] if count_byte == 'low' else count_word[0]
                else:
                    item_count = 0
                
                # Publish the count itself
                count_info = {"reg": f"{calc_reg + count_reg_offset}{reg_suffix}", "name": f"{array_name} Count"}
                process_and_publish(count_info, data[count_data_offset:count_data_offset + 2], device_name, mqtt_client, encrypted, discovery_info)
                
                # Process items based on items_per_register
                if items_per_register == 1:
                    # One item per register (e.g., cell voltages)
                    for i in range(item_count):
                        item_idx = i + 1
                        item_reg = f"{calc_reg + current_offset + i}{reg_suffix}"
                        chunk = data[(current_offset + i)*2 : (current_offset + i + 1)*2]
                        
                        # Create item-specific outputs with item number in the name
                        item_specific_outputs = []
                        for output in array_outputs:
                            output_copy = output.copy()
                            if 'name' in output_copy:
                                # Replace placeholder or append number
                                if '{n}' in output_copy['name']:
                                    output_copy['name'] = output_copy['name'].replace('{n}', str(item_idx))
                                else:
                                    output_copy['name'] = output_copy['name'].replace(array_name, f"{array_name} {item_idx}")
                            item_specific_outputs.append(output_copy)
                        
                        _handle_dynamic_discovery(discovery_info, device_name, item_reg, slave_id, trigger_val, trigger_reg, item_specific_outputs, True, mqtt_client)
                        
                        block_info = command_info.copy()
                        block_info.update({'type': 'processed_block', 'reg': item_reg, 'outputs': item_specific_outputs})
                        process_and_publish(block_info, chunk, device_name, mqtt_client, encrypted, discovery_info)
                    
                    current_offset += item_count
                    
                else:
                    # Multiple items per register (e.g., NTC temperatures packed in bytes)
                    num_registers = (item_count + items_per_register - 1) // items_per_register
                    
                    for i in range(item_count):
                        item_idx = i + 1
                        reg_within_block = i // items_per_register
                        item_within_reg = i % items_per_register
                        
                        # Get the register containing this item
                        chunk = data[(current_offset + reg_within_block)*2 : (current_offset + reg_within_block + 1)*2]
                        if len(chunk) < 2:
                            break
                        
                        # For byte-packed items, extract the specific byte
                        # Assuming big-endian: chunk[0] = high byte, chunk[1] = low byte
                        # item 0 = low byte, item 1 = high byte
                        if items_per_register == 2:
                            val = chunk[1] if item_within_reg == 0 else chunk[0]
                        else:
                            val = chunk[item_within_reg] if item_within_reg < len(chunk) else 0
                        
                        # Use actual register number with item suffix
                        actual_reg = calc_reg + current_offset + reg_within_block
                        item_suffix = f".{item_within_reg}"
                        block_reg = f"{actual_reg}{reg_suffix}{item_suffix}"
                        
                        # Create item-specific outputs
                        item_specific_outputs = []
                        for output in array_outputs:
                            output_copy = output.copy()
                            if 'name' in output_copy:
                                if '{n}' in output_copy['name']:
                                    output_copy['name'] = output_copy['name'].replace('{n}', str(item_idx))
                                else:
                                    output_copy['name'] = output_copy['name'].replace(array_name, f"{array_name} {item_idx}")
                            item_specific_outputs.append(output_copy)
                        
                        # Handle Discovery
                        if discovery_info:
                            unique_id = f"{device_name}_{block_reg.replace('.', '_')}_s{slave_id}"
                            if unique_id not in DISCOVERED_DYNAMIC_REGS:
                                _handle_dynamic_discovery(discovery_info, device_name, block_reg, slave_id, trigger_val, trigger_reg, item_specific_outputs, False, mqtt_client)
                        
                        # Publish the value
                        state_topic = f"bluetti_debugger/{device_name}/{block_reg}/state"
                        # Apply subtract if specified
                        subtract_val = array_outputs[0].get('subtract', 0) if array_outputs else 0
                        processed_val = val - subtract_val
                        state_payload = {
                            "value": processed_val,
                            "PossibleName": item_specific_outputs[0]['name'] if item_specific_outputs else f"{array_name} {item_idx}",
                            "modbus_register": block_reg,
                            "valid": True
                        }
                        if array_outputs and 'unit' in array_outputs[0]:
                            state_payload['unit'] = array_outputs[0]['unit']
                        if mqtt_client:
                            mqtt_client.publish(state_topic, json.dumps(state_payload))
                    
                    current_offset += num_registers
            
            return


        # Handle generic repeating patterns (e.g. Node Lists, Cell Data)
        if output_type == 'repeating_nonzero':
            block_regs = command_info.get('block_regs', 1)
            check_reg_offset = command_info.get('check_reg_offset', 0)
            block_size = block_regs * 2

            for i in range(0, len(data), block_size):
                chunk = data[i:i + block_size]
                if len(chunk) < block_size:
                    break

                # Check the designated register for a non-zero value
                check_val = int.from_bytes(chunk[check_reg_offset*2 : check_reg_offset*2 + 2], 'big')
                if check_val == 0:
                    continue

                block_idx = (i // block_size) + 1
                block_reg = f"{register}.b{block_idx}"

                # Handle dynamic Home Assistant discovery for this new block
                if discovery_info:
                    slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
                    trigger_suffix = f"_t{trigger_val}" if trigger_reg is not None else ""
                    
                    for output in outputs:
                        topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
                        unique_id = f"{device_name}_{block_reg}{topic_suffix.replace('.', '_')}{slave_suffix}{trigger_suffix}"
                        
                        if unique_id not in DISCOVERED_DYNAMIC_REGS:
                            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
                            state_topic = f"bluetti_debugger/{device_name}/{block_reg}{topic_suffix}{slave_suffix}{trigger_suffix}/state"
                            
                            payload = {
                                "name": f"{block_reg} {output['name']}",
                                "state_topic": state_topic,
                                "unique_id": unique_id,
                                "json_attributes_topic": state_topic,
                                "value_template": "{{ value_json.value }}",
                                "device": {
                                    "identifiers": [discovery_info['address']],
                                    "name": discovery_info['display_name'],
                                    "model": discovery_info['type'],
                                    "manufacturer": "Bluetti"
                                }
                            }
                            if 'device_class' in output:
                                payload['device_class'] = output['device_class']
                            if 'unit' in output:
                                payload['unit_of_measurement'] = output['unit']

                            if mqtt_client:
                                mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
                            DISCOVERED_DYNAMIC_REGS.add(unique_id)
                            print(f"Sent dynamic discovery for {block_reg} ({output['name']})")

                # Recursively process this chunk as a standalone command
                # We append a block suffix to the register name for MQTT topic uniqueness
                block_info = command_info.copy()
                block_info['type'] = 'processed_block' # Prevent infinite loop
                block_info['reg'] = block_reg
                process_and_publish(block_info, chunk, device_name, mqtt_client, encrypted, discovery_info)
            return

        # Parse the sliced data
        base_value = None
        output_format = command_info.get('format')

        if output_type == 'processed_block':
            # When processing a block chunk, we don't calculate a single base_value.
            # The loop below will extract individual fields using reg_offset.
            base_value = 0 
        elif output_type == 'float':
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
            if output_type == 'processed_block' or (is_split and 'reg_offset' in output):
                # Extract field-specific data from the chunk/block
                f_offset = output.get('reg_offset', 0)
                f_len = output.get('len', 16)
                f_num_regs = f_len // 16 if not output.get('ascii') and f_len >= 16 else f_len
                f_data = data[f_offset*2 : (f_offset + f_num_regs)*2]
                
                # Support nested splitting (e.g. bitmasks inside a repeating block)
                if 'outputs' in output:
                    sub_info = output.copy()
                    sub_info['type'] = 'processed_block'
                    sub_info['reg'] = get_display_register(register, output)
                    process_and_publish(sub_info, f_data, device_name, mqtt_client, encrypted, discovery_info)
                    continue

                if output.get('ascii'): 
                    value = bytes_to_ascii(f_data)
                else:
                    value = int.from_bytes(f_data, 'little' if output.get('byte_swap') else 'big')
            else:
                value = base_value

            if not is_ascii and isinstance(value, int):
                if 'offset' in output: value >>= output['offset']
                if 'mask' in output: value &= output['mask']
                if output.get('signed', False) and not is_split:
                    if length == 32: value = to_32bit_signed(value)
                    elif length == 16: value = to_signed(value)
                if 'subtract' in output: value -= output['subtract']
                if output.get('absolute', False): value = abs(value)
                if 'scale' in output: value = apply_scale(value, output['scale'])
                if output.get('type') == 'decimal': value = str(value)
                if 'values' in output and isinstance(value, int) and 0 <= value < len(output['values']):
                    value = output['values'][value]

            slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
            trigger_suffix = f"_t{trigger_val}" if trigger_reg is not None else ""
            display_reg = get_display_register(register, output)
            topic_suffix = get_topic_suffix(output, is_split)
            state_topic = f"bluetti_debugger/{device_name}/{display_reg}{topic_suffix}{slave_suffix}{trigger_suffix}/state"
            state_payload = {
                "value": value, 
                "PossibleName": output['name'], 
                "modbus_register": f"{display_reg}{topic_suffix}",
                "slave_id": slave_id,
                "encrypted": encrypted,
                "valid": True
            }
            if 'notes' in output: state_payload["notes"] = output['notes']

            if mqtt_client:
                mqtt_client.publish(state_topic, json.dumps(state_payload))
            # print(f"Published {register}{topic_suffix} (Slave {slave_id}) ({output['name']}): {value}")

    except Exception as e:
        print(f"An error occurred while processing register {command_info.get('reg')}: {e}")


def _handle_dynamic_discovery(discovery_info, device_name, block_reg, slave_id, trigger_val, trigger_reg, outputs, is_split, mqtt_client):
    """Helper to register dynamic entities with Home Assistant."""
    if not discovery_info:
        return

    slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
    trigger_suffix = f"_t{trigger_val}" if trigger_reg is not None else ""
    
    for output in outputs:
        display_reg = get_display_register(block_reg, output)
        topic_suffix = get_topic_suffix(output, is_split)
        id_suffix = get_id_suffix(output, is_split)
        unique_id = f"{device_name}_{display_reg.replace('.', '_')}{id_suffix}{slave_suffix}{trigger_suffix}"
        
        if unique_id not in DISCOVERED_DYNAMIC_REGS:
            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
            state_topic = f"bluetti_debugger/{device_name}/{display_reg}{topic_suffix}{slave_suffix}{trigger_suffix}/state"
            
            payload = {
                "name": f"{display_reg} {output['name']}",
                "state_topic": state_topic,
                "unique_id": unique_id,
                "json_attributes_topic": state_topic,
                "value_template": "{{ value_json.value }}",
                "device": {
                    "identifiers": [discovery_info['address']],
                    "name": discovery_info['display_name'],
                    "model": discovery_info['type'],
                    "manufacturer": "Bluetti"
                }
            }
            if 'device_class' in output: payload['device_class'] = output['device_class']
            if 'unit' in output: payload['unit_of_measurement'] = output['unit']

            if mqtt_client:
                mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
            DISCOVERED_DYNAMIC_REGS.add(unique_id)
            print(f"Sent dynamic discovery for {display_reg} ({output['name']})")


def publish_invalid(command_info: Dict[str, Any], device_name: str, mqtt_client: mqtt.Client, encrypted: bool):
    register = command_info['reg']
    slave_id = get_target_slave_id(command_info)
    trigger_reg = command_info.get('trigger_reg')
    trigger_val = command_info.get('trigger_val')
    is_split = 'outputs' in command_info
    outputs = command_info.get('outputs', [command_info])
    for output in outputs:
        slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
        trigger_suffix = f"_t{trigger_val}" if trigger_reg is not None else ""
        topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}{slave_suffix}{trigger_suffix}/state"
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
        if mqtt_client:
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
    slave_switch_delay: float = 0.8,
    disconnect_on_slave_change: bool = False,
    max_group_size: int = 32,
    force_protocol: Optional[str] = None,
    debug_logging: bool = False,
    plaintext_slaves: Set[int] = set(),
    device_type: str = "Bluetti Device",
    display_name: str = "Bluetti Device"
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

    discovery_info = {
        "address": device_address,
        "display_name": display_name,
        "type": device_type
    }

    # Parse plaintext slaves list
    if plaintext_slaves:
        logging.info(f"Forcing plaintext for slaves: {sorted(plaintext_slaves)}")

    # --- SESSION KEEP-ALIVE (App Mimicry) ---
    # Replicate the official app routine to prevent the device from entering low-power mode
    # and clearing the peripheral registers (Slave 41, Slave 0).
    if device_protocol == "v2":
        print("Refreshing Session Lock (Keep-Alive Heartbeat)...")
        heartbeats = [
            (190, 1, 1),     # 1. Master Session Lock (Slave 1)
            (30001, 1, 0),   # 2. Global Mesh Refresh (Broadcast 0 - No Response)
            (21000, 6, 1)    # 3. Peripheral Refresh (Slave 1)
        ]
        for reg, val, sid in heartbeats:
            try:
                cmd = WriteSingleRegister(reg, val, slave_id=sid)
                if sid == 0:
                    # Broadcast writes often don't return a response.
                    # We await the future with a short timeout to "retrieve" any potential
                    # exception (like Modbus Error 2) to avoid asyncio warnings.
                    future = await client.perform(cmd)
                    try:
                        await asyncio.wait_for(future, timeout=0.5)
                    except (asyncio.TimeoutError, Exception):
                        pass
                    await asyncio.sleep(0.1)
                else:
                    future = await client.perform_with_fallback(cmd, device_protocol)
                    await future
            except Exception as e:
                logging.debug(f"Heartbeat write to {reg} ignored: {e}")
        
        await asyncio.sleep(0.5) # Wait for Inverter to bridge the refresh

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
                validation_cmd = build_slave_validation_command(group, device_protocol, client)
                validation_success = False
                try:
                    future = await client.perform(validation_cmd)
                    await future
                    validation_success = True
                except BadConnectionError as e:
                    if not validation_success:
                        print(f"  Slave validation read ignored: {e}")
                except Exception as e:
                    print(f"  Slave validation read ignored: {e}")
                await asyncio.sleep(0.05)

        previous_slave_id = slave_id

        # --- PAGINATION LOGIC --- 
        p_selector = group.get('pagination_selector')
        p_count_reg = group.get('pagination_count_reg')
        p_slave = group.get('pagination_slave', slave_id)
        total_pages = 1

        if p_selector and p_count_reg:
            print(f"  --- PAGINATION DISCOVERY (Selector {p_selector}) ---")
            try:
                # 1. Write Page 1 to trigger the count
                trigger_cmd = WriteSingleRegister(p_selector, 1, slave_id=p_slave)
                await (await client.perform_with_fallback(trigger_cmd, device_protocol))
                await asyncio.sleep(0.8)

                # 2. Read the Page Count register
                read_count_cmd = ReadHoldingRegisters(p_count_reg, 1, slave_id=p_slave)
                count_res = cast(bytes, await (await client.perform_with_fallback(read_count_cmd, device_protocol)))
                total_pages = int.from_bytes(read_count_cmd.parse_response(count_res), 'big')
                print(f"    Found {total_pages} pages in register {p_count_reg} (Slave {p_slave})")
            except Exception as e:
                print(f"    Pagination discovery failed: {e}")
                total_pages = 1

        for page_idx in range(1, total_pages + 1):
            if total_pages > 1:
                print(f"  --- POLLING PAGE {page_idx} of {total_pages} ---")
                # Write the specific page index to the selector
                page_trigger = WriteSingleRegister(p_selector, page_idx, slave_id=p_slave)
                try:
                    await (await client.perform_with_fallback(page_trigger, device_protocol))
                    await asyncio.sleep(0.8)
                except Exception as e:
                    print(f"    Page switch failed: {e}")

            # Handle Static Trigger Register Write if defined for this group
            if group.get('triggers'):
                print(f"  --- TRIGGER START ---")
                for t in group['triggers']:
                    t_reg, t_val = t['reg'], t['val']
                    t_slave = t.get('slave_id', slave_id)
                    print(f"  Action: Write {t_val} to {t_reg} (Slave {t_slave})")
                    trigger_cmd = WriteSingleRegister(t_reg, t_val, slave_id=t_slave)
                    try:
                        t_future = await client.perform_with_fallback(trigger_cmd, device_protocol)
                        await t_future
                        print("    Result: Success (Write accepted)")
                    except Exception as e:
                        print(f"    Result: Failed - {e}")
                
                delay = group.get('trigger_delay', 0.8)
                print(f"  Waiting {delay}s for Commit/Paging...")
                await asyncio.sleep(delay)
                print(f"  --- TRIGGER END ---")

            command = ReadHoldingRegisters(group['start_reg'], group['num_regs'], slave_id=slave_id)
            tx_type = "Plaintext"
            print(f"  TX Packet ({tx_type}): {bytes(command).hex()}")
            try:
                print(f"  Attempting {tx_type} command...")
                future = await client.perform_with_fallback(command, device_protocol)
                response = cast(bytes, await future)
                print(f"  ✓ {tx_type} command succeeded")

                group_data = command.parse_response(response)
                print(f"Read group (Slave {slave_id}) starting at {group['start_reg']} raw: {group_data.hex()}")

                for command_info in group['commands']:
                    try:
                        # If paginating, modify the register identity so topics remain unique
                        cmd_info_copy = command_info.copy()
                        if total_pages > 1:
                            base_reg = cmd_info_copy['reg']
                            cmd_info_copy['reg'] = f"{base_reg}.p{page_idx}"
                            
                            # Handle dynamic discovery for paginated registers
                            outputs = cmd_info_copy.get('outputs', [cmd_info_copy])
                            is_split = 'outputs' in cmd_info_copy
                            trigger_reg = cmd_info_copy.get('trigger_reg')
                            trigger_val = cmd_info_copy.get('trigger_val')
                            _handle_dynamic_discovery(discovery_info, device_name, cmd_info_copy['reg'], slave_id, trigger_val, trigger_reg, outputs, is_split, mqtt_client)

                        register = command_info['reg']
                        length = command_info.get('len', 1)
                        is_ascii = command_info.get('ascii', False)

                        num_registers = length // 16 if not is_ascii and length >= 16 else length
                        start_byte = (register - group['start_reg']) * 2
                        end_byte = start_byte + (num_registers * 2)
                        data = group_data[start_byte:end_byte]
                        process_and_publish(cmd_info_copy, data, device_name, mqtt_client, False, discovery_info)
                    except Exception as e:
                        print(f"An error occurred while processing register {command_info.get('reg')}: {e}")

            except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
                print(f"Error polling group starting at {group['start_reg']}: {e}. Falling back to individual polling.")
                # Fallback: Try polling each command in the group individually
                for command_info in group['commands']:
                    try:
                        cmd_info_copy = command_info.copy()
                        if total_pages > 1:
                            base_reg = cmd_info_copy['reg']
                            cmd_info_copy['reg'] = f"{base_reg}.p{page_idx}"
                            
                            # Handle dynamic discovery for paginated registers
                            outputs = cmd_info_copy.get('outputs', [cmd_info_copy])
                            is_split = 'outputs' in cmd_info_copy
                            trigger_reg = cmd_info_copy.get('trigger_reg')
                            trigger_val = cmd_info_copy.get('trigger_val')
                            _handle_dynamic_discovery(discovery_info, device_name, cmd_info_copy['reg'], slave_id, trigger_val, trigger_reg, outputs, is_split, mqtt_client)

                        register = command_info['reg']
                        length = command_info.get('len', 1)
                        is_ascii = command_info.get('ascii', False)
                        num_registers = length // 16 if not is_ascii and length >= 16 else length
                        slave_id = get_target_slave_id(command_info)

                        single_command = ReadHoldingRegisters(register, num_registers, slave_id=slave_id)
                        tx_type = "Plaintext"
                        print(f"  TX Packet ({tx_type}): {bytes(single_command).hex()}")
                        future = await client.perform(single_command)
                        response = cast(bytes, await future)
                        if len(response) > 0:
                            print(f"  RX Packet: SlaveID={response[0]} Func={response[1]} Len={len(response)}")
                        data = single_command.parse_response(response)
                        process_and_publish(cmd_info_copy, data, device_name, mqtt_client, False, discovery_info)
                    except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
                        print(f"Error individual polling register {command_info['reg']}: {e}")

                        # Fallback 2: If encrypted failed, try plaintext
                        success_plaintext = False
                        if HAS_CRYPTO:
                            try:
                                print(f"Retrying register {command_info['reg']} with plaintext...")
                                single_command = ReadHoldingRegisters(register, num_registers, slave_id=slave_id)
                                print(f"  TX Packet (Plaintext): {bytes(single_command).hex()}")
                                future = await client.perform(single_command)
                                response = cast(bytes, await future)
                                if len(response) > 0:
                                    print(f"  RX Packet: SlaveID={response[0]} Func={response[1]} Len={len(response)}")
                                data = single_command.parse_response(response)
                                process_and_publish(cmd_info_copy, data, device_name, mqtt_client, False, discovery_info)
                                success_plaintext = True
                            except Exception as e2:
                                print(f"Error plaintext fallback register {command_info['reg']}: {e2}")

                        if success_plaintext:
                            continue
                        publish_invalid(command_info, device_name, mqtt_client, False)

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
    parser.add_argument("--slave-switch-delay", type=float, default=0.8, help="Delay in seconds when switching slave IDs")
    parser.add_argument("--disconnect-on-slave-change", action="store_true", help="Disconnect and reconnect Bluetooth when switching slaves (slow but reliable)")
    parser.add_argument("--max-group-size", type=int, default=250, help="Max registers per Modbus read command")
    parser.add_argument("--force-protocol", choices=["v1", "v2"], help="Force specific protocol version (v1=plaintext, v2=legacy mode; encryption is ignored)")
    parser.add_argument("--plaintext-slaves", type=str, help="Comma-separated list of slave IDs to force plaintext protocol (e.g., '41,42,43')")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--no-mqtt", action="store_true", help="Disable MQTT publishing (for testing)")
    parser.add_argument("--run-once", action="store_true", help="Run polling once and exit (for testing)")

    args = parser.parse_args()

    # Configure logging to catch the background client errors and suppress tracebacks
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=log_level, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S')
    logging.getLogger().addFilter(BriefConnectionErrors())

    mqtt_client = None
    if not args.no_mqtt:
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
                    DISCOVERED_DYNAMIC_REGS.clear()
                    commands_to_poll = get_command_fields(args)

                    # Perform Home Assistant discovery
                    print(f"Publishing {len(commands_to_poll)} Home Assistant auto-discovery configs...")
                    for command_info in commands_to_poll:
                        # Skip dynamic types that register themselves during the polling loop
                        if command_info.get('type') in ['dynamic_bmu_block', 'repeating_nonzero', 'repeating_count']:
                            continue
                        
                        # Skip paginated registers - they will be discovered dynamically during polling
                        if command_info.get('pagination_selector') is not None:
                            continue

                        register = command_info.get('reg')
                        if register is None:
                            continue

                        outputs = command_info.get('outputs', [command_info])
                        is_split = 'outputs' in command_info

                        for output in outputs:
                            output_name = output.get('name')
                            if not output_name:
                                continue

                            slave_id = get_target_slave_id(command_info)
                            trigger_reg = command_info.get('trigger_reg')
                            trigger_val = command_info.get('trigger_val')
                            slave_suffix = f"_s{slave_id}" if slave_id != 1 else ""
                            trigger_suffix = f"_t{trigger_val}" if trigger_reg is not None else ""
                            display_reg = get_display_register(register, output)
                            topic_suffix = get_topic_suffix(output, is_split)
                            id_suffix = get_id_suffix(output, is_split)
                            unique_id = f"{device_name}_{display_reg.replace('.', '_')}{id_suffix}{slave_suffix}{trigger_suffix}"
                            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
                            state_topic = f"bluetti_debugger/{device_name}/{display_reg}{topic_suffix}{slave_suffix}{trigger_suffix}/state"

                            payload = {
                                "name": f"{display_reg} {output_name}",
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

                            if mqtt_client:
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
                device_type=device.type,
                display_name=display_name
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Polling complete in {duration:.2f} seconds. Waiting for {args.scan_interval} seconds...")
            
            if args.run_once:
                break
            
            await asyncio.sleep(args.scan_interval)

    finally:
        if mqtt_client:
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