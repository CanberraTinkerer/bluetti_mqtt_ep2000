import argparse
import asyncio
import base64
from bleak import BleakError
from io import TextIOWrapper
import json
import sys
import textwrap
import time
from typing import cast, List, Optional
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
# Helpers for decoding register blocks
# ---------------------------------------------------------------------------

def bytes_to_regs(body: bytes) -> List[int]:
    """Convert Modbus response body (big-endian) to list of 16-bit registers."""
    return [int.from_bytes(body[i:i+2], 'big') for i in range(0, len(body), 2)]


def decode_pvi_tuple(regs: List[int], base_index: int) -> Optional[tuple]:
    """
    Decode a generic P/V/I tuple from regs[base_index:].
    Assumes:
      regs[base_index+1] = power (signed 16-bit, W)
      regs[base_index+2] = voltage (V * 10)
      regs[base_index+3] = current (A * 10)
    """
    if base_index + 3 >= len(regs):
        return None

    p = regs[base_index + 1]
    if p > 32767:
        p -= 65535 + 1  # signed 16-bit

    v = regs[base_index + 2] / 10.0
    c = regs[base_index + 3] / 10.0
    return p, v, c


def print_pvi(label: str, pvi: Optional[tuple]):
    if pvi is None:
        print(f"{label}: (unable to decode P/V/I from this block)")
        return
    p, v, c = pvi
    print(f"{label}: P={p}W  V={v:.1f}V  I={c:.1f}A")


def decode_grid(start: int, regs: List[int]):
    # Grid tuple confirmed at 1324–1329 → base_index = 0 when start == 1324
    if start <= 1324 and (1324 - start) + 3 < len(regs):
        idx = 1324 - start
        pvi = decode_pvi_tuple(regs, idx)
        print_pvi("GRID", pvi)


def decode_pv(start: int, regs: List[int]):
    # PV1: 1208–1210, PV2: 1216–1218
    # We treat them as P/V/I tuples starting at those bases.
    # Exact mapping: base_index = register - start
    # PV1
    if start <= 1208 and (1208 - start) + 3 < len(regs):
        idx1 = 1208 - start
        pvi1 = decode_pvi_tuple(regs, idx1)
        print_pvi("PV1", pvi1)
    # PV2
    if start <= 1216 and (1216 - start) + 3 < len(regs):
        idx2 = 1216 - start
        pvi2 = decode_pvi_tuple(regs, idx2)
        print_pvi("PV2", pvi2)


def decode_inverter(start: int, regs: List[int]):
    # Inverter output per phase:
    # L1: 1509–1511, L2: 1516–1518, L3: 1523–1525
    # We treat each as a P/V/I tuple.
    # L1
    if start <= 1509 and (1509 - start) + 3 < len(regs):
        idx1 = 1509 - start
        pvi1 = decode_pvi_tuple(regs, idx1)
        print_pvi("INV-L1", pvi1)
    # L2
    if start <= 1516 and (1516 - start) + 3 < len(regs):
        idx2 = 1516 - start
        pvi2 = decode_pvi_tuple(regs, idx2)
        print_pvi("INV-L2", pvi2)
    # L3
    if start <= 1523 and (1523 - start) + 3 < len(regs):
        idx3 = 1523 - start
        pvi3 = decode_pvi_tuple(regs, idx3)
        print_pvi("INV-L3", pvi3)


def decode_battery(start: int, regs: List[int]):
    # Battery P/V/I tuple inferred at 2006–2009:
    # 2006: voltage (V * 10)
    # 2008: current (A * 10, signed)
    # We'll treat it as a P/V/I-like tuple with a synthetic layout:
    #   power = voltage * current (approx), but we mainly care about V/I.
    if start <= 2006 and (2006 - start) + 3 < len(regs):
        idx = 2006 - start
        # Here we don't have a direct power register; we approximate or just show V/I.
        v = regs[idx] / 10.0
        # current at 2008
        if start <= 2008 and (2008 - start) < len(regs):
            ci = 2008 - start
            c = regs[ci]
            if c > 32767:
                c -= 65535 + 1
            c = c / 10.0
            # approximate power
            p = int(v * c)
            print(f"BATTERY: P≈{p}W  V={v:.1f}V  I={c:.1f}A")
        else:
            print(f"BATTERY: V={v:.1f}V (current not in this block)")


def decode_load(start: int, regs: List[int]):
    # Load block around 1400–1439.
    # From your 1420–1439 dump:
    #   1420–1429: zeros
    #   1430: 0003 (status)
    #   1431: 0000
    #   1432: 0985 (likely power)
    #   1433–1436: zeros
    #   1437: 09AF (voltage)
    #   1438–1439: zeros
    #
    # This is not a clean contiguous P/V/I tuple, but we can still decode P and V.
    if start <= 1430 and (1437 - start) < len(regs):
        idx_status = 1430 - start
        idx_power = 1432 - start
        idx_voltage = 1437 - start

        status = regs[idx_status]
        p = regs[idx_power]
        if p > 32767:
            p -= 65535 + 1
        v = regs[idx_voltage] / 10.0

        # Current is not explicitly exposed in this sub-block (zeros in your dump),
        # so we omit it rather than guessing.
        print(f"LOAD: status={status}  P={p}W  V={v:.1f}V (I not exposed in this block)")


def decode_tuple_generic(start: int, regs: List[int]):
    # Generic 6-register P/V/I tuple starting at 'start'
    # This is mainly for blocks like 1324–1329 when you just want a quick decode.
    if len(regs) >= 6:
        pvi = decode_pvi_tuple(regs, 0)
        print_pvi(f"TUPLE@{start}", pvi)


def decode_auto(start: int, regs: List[int]):
    # Heuristic auto-decoder based on known ranges.
    # Order matters: we try the most specific first.
    if 1324 <= start <= 1324 + 10 or (start < 1324 and 1324 - start < len(regs)):
        decode_grid(start, regs)
    if 1200 <= start <= 1220 or (start < 1208 and 1208 - start < len(regs)):
        decode_pv(start, regs)
    if 1500 <= start <= 1530 or (start < 1509 and 1509 - start < len(regs)):
        decode_inverter(start, regs)
    if 1400 <= start <= 1440 or (start < 1430 and 1430 - start < len(regs)):
        decode_load(start, regs)
    if 2000 <= start <= 2030 or (start < 2006 and 2006 - start < len(regs)):
        decode_battery(start, regs)
    # As a fallback, if the block is exactly 6 registers, treat it as a generic tuple.
    if len(regs) == 6:
        decode_tuple_generic(start, regs)


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
async def watch_registers(address: str, start: int, count: int, interval: float, decode_mode: Optional[str]):
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
                # Always print raw hex (your choice B)
                ts = time.strftime('%H:%M:%S')
                hex_str = body.hex()
                print(f"[{ts}] {hex_str}")
                last = body

                # Decode if requested
                if decode_mode:
                    regs = bytes_to_regs(body)
                    if decode_mode == "grid":
                        decode_grid(start, regs)
                    elif decode_mode == "pv":
                        decode_pv(start, regs)
                    elif decode_mode == "inverter":
                        decode_inverter(start, regs)
                    elif decode_mode == "battery":
                        decode_battery(start, regs)
                    elif decode_mode == "load":
                        decode_load(start, regs)
                    elif decode_mode == "tuple":
                        decode_tuple_generic(start, regs)
                    elif decode_mode == "auto":
                        decode_auto(start, regs)

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
        description='Bluetti Logger with scan + watch modes (enhanced decode)',
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --scan-start 100 --scan-count 60 AA:BB:CC:DD:EE:FF
              %(prog)s --watch-start 1300 --watch-count 20 --watch-interval 1 --decode auto AA:BB:CC:DD:EE:FF
              %(prog)s --watch-start 1324 --watch-count 6 --watch-interval 1 --decode grid AA:BB:CC:DD:EE:FF
              %(prog)s --log log.txt AA:BB:CC:DD:EE:FF
            """))

    parser.add_argument('--scan-start', type=int, help='Start register for raw scan')
    parser.add_argument('--scan-count', type=int, help='Number of registers to scan')

    parser.add_argument('--watch-start', type=int, help='Start register for watch mode')
    parser.add_argument('--watch-count', type=int, help='Number of registers to watch')
    parser.add_argument('--watch-interval', type=float, default=1.0, help='Polling interval (seconds)')

    parser.add_argument(
        '--decode',
        choices=['auto', 'grid', 'pv', 'load', 'inverter', 'battery', 'tuple'],
        help='Decode known EP2000 blocks in watch mode (raw hex is always printed as well)'
    )

    parser.add_argument('--log', metavar='PATH', help='Log to file')

    parser.add_argument('address', metavar='ADDRESS', nargs='?', help='Device MAC address')

    args = parser.parse_args()

    if args.scan_start is not None and args.scan_count is not None:
        asyncio.run(scan_registers(args.address, args.scan_start, args.scan_count))
    elif args.watch_start is not None and args.watch_count is not None:
        asyncio.run(
            watch_registers(
                args.address,
                args.watch_start,
                args.watch_count,
                args.watch_interval,
                args.decode
            )
        )
    elif args.log:
        asyncio.run(log(args.address, args.log))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
