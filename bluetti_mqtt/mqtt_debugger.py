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
from datetime import datetime
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, cast

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
from bluetti_mqtt.core.commands import ReadHoldingRegisters
from bluetti_mqtt.core.utils import modbus_crc

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


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
    """
    A V2-compatible ReadHoldingRegisters command that encrypts the payload
    (Address + Length) using AES-128-ECB before sending.
    """
    KEY = bytes.fromhex("459FC535808941F17091E0993EE3E93D")

    def __init__(self, starting_address: int, quantity: int, slave_id: int = 1):
        # Initialize parent just to set properties, we will overwrite self.cmd
        super().__init__(starting_address, quantity, slave_id=slave_id)

        if not HAS_CRYPTO:
            raise RuntimeError("pycryptodome is required for V2 encryption")

        # Create the plaintext payload: [Start_H, Start_L, Qty_H, Qty_L]
        payload = struct.pack('!HH', starting_address, quantity)
        
        # Pad to 16-byte block size (PKCS7 standard)
        padded_payload = pad(payload, 16)
        
        # Encrypt
        cipher = AES.new(self.KEY, AES.MODE_ECB)
        encrypted_payload = cipher.encrypt(padded_payload)

        # Build the full Modbus frame: [Addr][Func][Encrypted_Data][CRC]
        # Addr=1, Func=3
        self.cmd = bytearray(len(encrypted_payload) + 4)
        self.cmd[0] = slave_id
        self.cmd[1] = 3
        self.cmd[2:-2] = encrypted_payload
        
        # Calculate and append CRC
        struct.pack_into('<H', self.cmd, -2, modbus_crc(self.cmd[:-2]))

    def response_size(self):
        # The response contains: [Addr][Func][ByteCount][Encrypted_Data][CRC]
        # Encrypted_Data length is the plaintext data length (2 * qty) padded
        # to the next multiple of 16 using PKCS7. With PKCS7, if data is
        # already a multiple of block size, a full block of padding is added.
        data_len = 2 * self.quantity
        padded_len = (data_len // 16 + 1) * 16
        return 3 + padded_len + 2

    def parse_response(self, response: bytes):
        # Body is everything after [Addr][Func][ByteCount] and before [CRC]
        encrypted_body = response[3:-2]
        cipher = AES.new(self.KEY, AES.MODE_ECB)
        decrypted_body = cipher.decrypt(encrypted_body)
        # Remove padding to get back to the register data
        try:
            return unpad(decrypted_body, 16)
        except ValueError:
            # Fallback if unpad fails (e.g. wrong key or weird device behavior)
            return decrypted_body[:self.quantity * 2]


def get_command_fields(args: Namespace) -> List[Dict[str, Any]]:
    with open(args.config, "r") as config_file:
        config = json.load(config_file)
        return config


def get_target_slave_id(cmd: Dict[str, Any]) -> int:
    """
    Determines the Modbus Slave ID based on the register address,
    allowing for overrides in the command definition.
    """
    # 1. Check for explicit slave_id in the command definition
    if 'slave_id' in cmd:
        return cmd['slave_id']

    # 2. Fallback to range-based logic
    reg = cmd['reg']
    # Expansion Pack (BMS) ranges
    if 16100 <= reg < 16200: return 41
    if 21000 <= reg < 23000: return 41
    # Balcony PV range
    if reg >= 17400: return 31
    # Default Inverter
    return 1


def group_commands(commands_to_poll: List[Dict[str, Any]], max_gap: int = 5, max_group_size: int = 32) -> List[Dict[str, Any]]:
    """Groups individual register commands into larger reads to improve polling efficiency."""
    if not commands_to_poll:
        return []

    # Sort commands by register to enable grouping
    sorted_commands = sorted(commands_to_poll, key=lambda x: x['reg'])
    
    groups = []
    current_group = []
    current_group_encrypted = False
    current_group_slave_id = 1

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
        if not current_group:
            current_group.append(cmd)
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id
            continue

        group_start_reg = current_group[0]['reg']
        last_cmd_in_group = current_group[-1]
        group_end_reg = last_cmd_in_group['reg'] + get_num_regs(last_cmd_in_group)

        gap = cmd['reg'] - group_end_reg
        new_group_size = (cmd['reg'] + get_num_regs(cmd)) - group_start_reg

        # Group if gap/size are okay AND encryption status matches AND slave ID matches
        if (gap >= 0 and gap <= max_gap and 
            new_group_size <= max_group_size and 
            cmd_encrypted == current_group_encrypted and
            cmd_slave_id == current_group_slave_id):
            current_group.append(cmd)
        else:
            groups.append(current_group)
            current_group = [cmd]
            current_group_encrypted = cmd_encrypted
            current_group_slave_id = cmd_slave_id

    if current_group:
        groups.append(current_group)

    # Finalize group structure with start address and total register count for each group
    final_groups = []
    for group in groups:
        start_reg = group[0]['reg']
        encrypted = is_encrypted(group[0])
        slave_id = get_target_slave_id(group[0])
        
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
            'slave_id': slave_id
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
        elif length >= 32 and length % 16 == 0 and not is_ascii:
            # Legacy behavior: assume 32-bit+ values are word-swapped unless 'no_word_swap' is set.
            # This is for compatibility with older device definitions. V2 protocol likely uses standard big-endian.
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
                if 'values' in output and isinstance(value, int) and 0 <= value < len(output['values']):
                    value = output['values'][value]

            topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
            state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"
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
            print(f"Published {register}{topic_suffix} ({output['name']}): {value}")

    except Exception as e:
        print(f"An error occurred while processing register {command_info.get('reg')}: {e}")


def publish_invalid(command_info: Dict[str, Any], device_name: str, mqtt_client: mqtt.Client, encrypted: bool):
    register = command_info['reg']
    slave_id = get_target_slave_id(command_info)
    is_split = 'outputs' in command_info
    outputs = command_info.get('outputs', [command_info])
    for output in outputs:
        topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"
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

    args = parser.parse_args()

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
        client = BluetoothClient(device.address)
        asyncio.create_task(client.run())
        device_name = display_name.replace(" ", "_").lower()

        last_config_mtime = 0
        commands_to_poll = []

        while True:
            if not client.is_ready:
                print("Waiting for connection...")
                await asyncio.sleep(1)
                continue

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
                            topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
                            id_suffix = f"_{output.get('offset', 0)}" if is_split else ""
                            unique_id = f"{device_name}_{register}{id_suffix}"
                            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
                            state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"

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
            
            # Group commands for polling
            grouped_commands = group_commands(commands_to_poll)

            # Start the timer
            start_time = time.perf_counter()

            print(f"Polling {len(commands_to_poll)} registers in {len(grouped_commands)} groups...")
            for group in grouped_commands:
                slave_id = group.get('slave_id', 1)
                if group['encrypted'] and HAS_CRYPTO:
                    command = ReadHoldingRegistersV2(group['start_reg'], group['num_regs'], slave_id=slave_id)
                else:
                    command = ReadHoldingRegisters(group['start_reg'], group['num_regs'], slave_id=slave_id)
                
                try:
                    future = await client.perform(command)
                    response = cast(bytes, await future)
                    group_data = command.parse_response(response)
                    print(f"Read group starting at {group['start_reg']} raw: {group_data.hex()}")

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
                            
                            if cmd_encrypted and HAS_CRYPTO:
                                single_command = ReadHoldingRegistersV2(register, num_registers, slave_id=slave_id)
                            else:
                                single_command = ReadHoldingRegisters(register, num_registers, slave_id=slave_id)
                                
                            future = await client.perform(single_command)
                            response = cast(bytes, await future)
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
                                    future = await client.perform(single_command)
                                    response = cast(bytes, await future)
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