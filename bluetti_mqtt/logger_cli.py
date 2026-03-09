import argparse
import asyncio
import base64
from bleak import BleakError
from io import TextIOWrapper
import json
import sys
import textwrap
import time
from typing import cast
from bluetti_mqtt.bluetooth import (
    check_addresses, scan_devices, BluetoothClient, ModbusError,
    ParseError, BadConnectionError
)
from bluetti_mqtt.core import (
    BluettiDevice, ReadHoldingRegisters, DeviceCommand
)


def log_packet(output: TextIOWrapper, data: bytes, command: DeviceCommand):
    log_entry = {
        'type': 'client',
        'time': time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime()),
        'data': base64.b64encode(data).decode('ascii'),
        'command': base64.b64encode(bytes(command)).decode('ascii'),
    }
    output.write(json.dumps(log_entry) + '\n')


def log_invalid(output: TextIOWrapper, err: Exception, command: DeviceCommand):
    log_entry = {
        'type': 'client',
        'time': time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime()),
        'error': err.args[0],
        'command': base64.b64encode(bytes(command)).decode('ascii'),
    }
    output.write(json.dumps(log_entry) + '\n')


async def log_command(client: BluetoothClient, device: BluettiDevice, command: DeviceCommand, log_file: TextIOWrapper):
    response_future = await client.perform(command)
    try:
        response = cast(bytes, await response_future)
        if isinstance(command, ReadHoldingRegisters):
            body = command.parse_response(response)
            parsed = device.parse(command.starting_address, body)
            print(parsed)
        log_packet(log_file, response, command)
    except (BadConnectionError, BleakError, ModbusError, ParseError) as err:
        print(f'Got an error running command {command}: {err}')
        log_invalid(log_file, err, command)


# ---------------------------------------------------------------------------
# RAW SCAN MODE
# ---------------------------------------------------------------------------
async def scan_registers(address: str, start: int, count: int):
    devices = await check_addresses({address})
    if len(devices) == 0:
        sys.exit('Could not find the given device to connect to')
    device = devices[0]

    print(f'Connecting to {device.address}')
    client = BluetoothClient(device.address)
    asyncio.get_running_loop().create_task(client.run())

    while not client.is_ready:
        print('Waiting for connection...')
        await asyncio.sleep(1)

    print(f"\n--- RAW SCAN: start={start}, count={count} ---\n")

    command = ReadHoldingRegisters(start, count)
    response_future = await client.perform(command)

    try:
        response = cast(bytes, await response_future)
        body = command.parse_response(response)
        print(f"Raw register dump ({len(body)} bytes):")
        print(body.hex())
    except Exception as err:
        print(f"Error scanning registers: {err}")


# ---------------------------------------------------------------------------
# WATCH MODE
# ---------------------------------------------------------------------------
async def watch_registers(address: str, start: int, count: int, interval: float):
    devices = await check_addresses({address})
    if len(devices) == 0:
        sys.exit('Could not find the given device to connect to')
    device = devices[0]

    print(f'Connecting to {device.address}')
    client = BluetoothClient(device.address)
    asyncio.get_running_loop().create_task(client.run())

    while not client.is_ready:
        print('Waiting for connection...')
        await asyncio.sleep(1)

    print(f"\n--- WATCH MODE: start={start}, count={count}, interval={interval}s ---\n")

    last = None

    while True:
        command = ReadHoldingRegisters(start, count)
        response_future = await client.perform(command)

        try:
            response = cast(bytes, await response_future)
            body = command.parse_response(response)

            if last != body:
                print(f"[{time.strftime('%H:%M:%S')}] {body.hex()}")
                last = body

        except Exception as err:
            print(f"Error watching registers: {err}")

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# NORMAL LOGGING MODE
# ---------------------------------------------------------------------------
async def log(address: str, path: str):
    devices = await check_addresses({address})
    if len(devices) == 0:
        sys.exit('Could not find the given device to connect to')
    device = devices[0]

    print(f'Connecting to {device.address}')
    client = BluetoothClient(device.address)
    asyncio.get_running_loop().create_task(client.run())

    with open(path, 'a') as log_file:
        while not client.is_ready:
            print('Waiting for connection...')
            await asyncio.sleep(1)

        while True:
            for command in device.logging_commands:
                await log_command(client, device, command, log_file)

            if len(device.pack_logging_commands) == 0:
                continue

            for pack in range(1, device.pack_num_max + 1):
                if device.pack_num_max > 1:
                    command = device.build_setter_command('pack_num', pack)
                    await log_command(client, device, command, log_file)
                    await asyncio.sleep(10)

                for command in device.pack_logging_commands:
                    await log_command(client, device, command, log_file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Bluetti Logger with scan + watch modes',
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --scan-start 100 --scan-count 60 AA:BB:CC:DD:EE:FF
              %(prog)s --watch-start 1300 --watch-count 20 --watch-interval 1 AA:BB:CC:DD:EE:FF
              %(prog)s --log log.txt AA:BB:CC:DD:EE:FF
            """))

    parser.add_argument('--scan-start', type=int, help='Start register for raw scan')
    parser.add_argument('--scan-count', type=int, help='Number of registers to scan')

    parser.add_argument('--watch-start', type=int, help='Start register for watch mode')
    parser.add_argument('--watch-count', type=int, help='Number of registers to watch')
    parser.add_argument('--watch-interval', type=float, default=1.0, help='Polling interval (seconds)')

    parser.add_argument('--log', metavar='PATH', help='Log to file')

    parser.add_argument('address', metavar='ADDRESS', nargs='?', help='Device MAC address')

    args = parser.parse_args()

    if args.scan_start is not None and args.scan_count is not None:
        asyncio.run(scan_registers(args.address, args.scan_start, args.scan_count))
    elif args.watch_start is not None and args.watch_count is not None:
        asyncio.run(watch_registers(args.address, args.watch_start, args.watch_count, args.watch_interval))
    elif args.log:
        asyncio.run(log(args.address, args.log))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
