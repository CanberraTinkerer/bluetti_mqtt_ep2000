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


async def async_main():
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

        while True:
            if not client.is_ready:
                print("Waiting for connection...")
                await asyncio.sleep(1)
                continue

            try:
                commands_to_poll = get_command_fields(args)
            except Exception as e:
                print(f"Error loading config file: {e}")
                await asyncio.sleep(args.scan_interval)
                continue

            print(f"Polling {len(commands_to_poll)} registers...")
            for command_info in commands_to_poll:
                register = command_info['reg']
                length = command_info.get('len', 1)
                is_ascii = command_info.get('ascii', False)

                # Prepare outputs (support split fields)
                outputs = command_info.get('outputs', [command_info])
                is_split = 'outputs' in command_info

                # Home Assistant auto-discovery
                for output in outputs:
                    output_name = output['name']
                    # Generate suffixes
                    topic_suffix = ""
                    id_suffix = ""
                    if is_split:
                        offset = output.get('offset', 0)
                        topic_suffix = f".{offset}"
                        id_suffix = f"_{offset}"

                    unique_id = f"{device_name}_{register}{id_suffix}"
                    discovery_topic = f"homeassistant/sensor/{unique_id}/config"
                    state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"

                    payload = {
                        "name": f"{register} {output_name}" if is_split else str(register),
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

                # Determine number of registers to read
                if not is_ascii and length >= 16:
                    num_registers = length // 16
                else:
                    num_registers = length

                command = ReadHoldingRegisters(register, num_registers)
                try:
                    future = await client.perform(command)
                    response = cast(bytes, await future)
                    data = command.parse_response(response)
                    print(f"Read {register} raw: {data.hex()}")

                    base_value = None
                    if is_ascii:
                        base_value = bytes_to_ascii(data)
                    elif length >= 32 and length % 16 == 0 and not is_ascii:
                        words = bytes_to_words(data)
                        # Generic Little Endian Word Order (Word 0 is LSB)
                        base_value = 0
                        for i, word in enumerate(words):
                            base_value |= word << (i * 16)
                    else:
                        # Check for byte swap for 16-bit registers
                        if command_info.get('byte_swap', False):
                            base_value = int.from_bytes(data, 'little')
                        else:
                            base_value = int.from_bytes(data, 'big')

                    # Process and Publish Outputs
                    for output in outputs:
                        output_name = output['name']
                        value = base_value

                        if not is_ascii and isinstance(value, int):
                            if 'mask' in output:
                                value &= output['mask']
                            if 'offset' in output:
                                value >>= output['offset']

                            # Sign handling
                            is_signed = output.get('signed', False)
                            if is_signed and not is_split:
                                if length == 32:
                                    value = to_32bit_signed(value)
                                elif length == 16:
                                    value = to_signed(value)

                            if 'scale' in output:
                                value = apply_scale(value, output['scale'])

                        # Topic generation (must match discovery)
                        topic_suffix = ""
                        if is_split:
                            offset = output.get('offset', 0)
                            topic_suffix = f".{offset}"
                        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"

                        state_payload = {"value": value, "PossibleName": output_name, "modbus_register": f"{register}{topic_suffix}"}
                        if 'notes' in output:
                            state_payload["notes"] = output['notes']

                        mqtt_client.publish(state_topic, json.dumps(state_payload))
                        print(f"Published {register}{topic_suffix} ({output_name}): {value}")

                except (BadConnectionError, BleakError, ModbusError, ParseError) as e:
                    print(f"Error polling register {register}: {e}")
                    for output in outputs:
                        topic_suffix = ""
                        if is_split:
                            offset = output.get('offset', 0)
                            topic_suffix = f".{offset}"
                        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"
                        state_payload = {"value": "INVALID", "PossibleName": output['name'], "modbus_register": f"{register}{topic_suffix}"}
                        mqtt_client.publish(state_topic, json.dumps(state_payload))
                except Exception as e:
                    print(f"An error occurred while polling register {register}: {e}")
                    for output in outputs:
                        topic_suffix = ""
                        if is_split:
                            offset = output.get('offset', 0)
                            topic_suffix = f".{offset}"
                        state_topic = f"bluetti_debugger/{device_name}/{register}{topic_suffix}/state"
                        state_payload = {"value": "INVALID", "PossibleName": output['name'], "modbus_register": f"{register}{topic_suffix}"}
                        mqtt_client.publish(state_topic, json.dumps(state_payload))

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Polling complete. Waiting for {args.scan_interval} seconds...")
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