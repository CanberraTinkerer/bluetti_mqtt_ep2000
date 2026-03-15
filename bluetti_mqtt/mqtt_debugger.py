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


def get_command_fields(args: Namespace) -> List[Dict[str, Any]]:
    with open(args.config, "r") as config_file:
        config = json.load(config_file)
        return config


def group_commands(commands_to_poll: List[Dict[str, Any]], max_gap: int = 5, max_group_size: int = 32) -> List[Dict[str, Any]]:
    """Groups individual register commands into larger reads to improve polling efficiency."""
    if not commands_to_poll:
        return []

    # Sort commands by register to enable grouping
    sorted_commands = sorted(commands_to_poll, key=lambda x: x['reg'])
    
    groups = []
    current_group = []

    def get_num_regs(cmd):
        length = cmd.get('len', 1)
        is_ascii = cmd.get('ascii', False)
        # For non-ascii, length is in bits, so we divide by 16 to get register count
        return length // 16 if not is_ascii and length >= 16 else length

    for cmd in sorted_commands:
        if not current_group:
            current_group.append(cmd)
            continue

        group_start_reg = current_group[0]['reg']
        last_cmd_in_group = current_group[-1]
        group_end_reg = last_cmd_in_group['reg'] + get_num_regs(last_cmd_in_group)

        gap = cmd['reg'] - group_end_reg
        new_group_size = (cmd['reg'] + get_num_regs(cmd)) - group_start_reg

        # Group if the new command is close to the last one and the total group size is within limits
        if gap >= 0 and gap <= max_gap and new_group_size <= max_group_size:
            current_group.append(cmd)
        else:
            groups.append(current_group)
            current_group = [cmd]

    if current_group:
        groups.append(current_group)

    # Finalize group structure with start address and total register count for each group
    final_groups = []
    for group in groups:
        start_reg = group[0]['reg']
        
        # Find the end register of the group
        end_reg = 0
        for cmd in group:
            cmd_end = cmd['reg'] + get_num_regs(cmd)
            if cmd_end > end_reg:
                end_reg = cmd_end

        final_groups.append({
            'start_reg': start_reg,
            'num_regs': end_reg - start_reg,
            'commands': group
        })

    return final_groups


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
                command = ReadHoldingRegisters(group['start_reg'], group['num_regs'])
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
                                words = bytes_to_words(data)
                                base_value = 0
                                for i, word in enumerate(words):
                                    base_value |= word << (i * 16)
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
                                state_payload = {"value": value, "PossibleName": output['name'], "modbus_register": f"{register}{topic_suffix}"}
                                if 'notes' in output: state_payload["notes"] = output['notes']

                                mqtt_client.publish(state_topic, json.dumps(state_payload))
                                print(f"Published {register}{topic_suffix} ({output['name']}): {value}")

                        except Exception as e:
                            print(f"An error occurred while processing register {command_info.get('reg')}: {e}")

                except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
                    print(f"Error polling group starting at {group['start_reg']}: {e}")
                    # Mark all commands in the failed group as invalid
                    for command_info in group['commands']:
                        register = command_info['reg']
                        is_split = 'outputs' in command_info
                        outputs = command_info.get('outputs', [command_info])
                        for output in outputs:
                            topic_suffix = f".{output.get('offset', 0)}" if is_split else ""
                            state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"
                            state_payload = {"value": "INVALID", "PossibleName": output['name'], "modbus_register": f"{register}{topic_suffix}"}
                            mqtt_client.publish(state_topic, json.dumps(state_payload))

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