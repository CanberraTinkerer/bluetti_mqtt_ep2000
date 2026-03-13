import argparse
import asyncio
import base64
from bleak import BleakError
from io import TextIOWrapper
import json
import sys
import textwrap
from decimal import Decimal
from enum import Enum
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
    return [int.from_bytes(body[i:i+2], 'big') for i in range(0, len(body), 2)]

def serialize_value(val):
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, Enum):
        return val.name
    if isinstance(val, bytes):
        # For string fields
        return val.decode('ascii', errors='ignore')
    if isinstance(val, list):
        # For array fields
        return [serialize_value(v) for v in val]
    return val


def decode_pvi_tuple(regs: List[int], base_index: int) -> Optional[tuple]:
    if base_index + 3 >= len(regs):
        return None
    p = regs[base_index + 1]
    if p > 32767:
        p -= 65535 + 1
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
    if start <= 1324 and (1324 - start) + 3 < len(regs):
        idx = 1324 - start
        pvi = decode_pvi_tuple(regs, idx)
        print_pvi("GRID", pvi)


def decode_pv(start: int, regs: List[int]):
    if start <= 1208 and (1208 - start) + 3 < len(regs):
        idx1 = 1208 - start
        pvi1 = decode_pvi_tuple(regs, idx1)
        print_pvi("PV1", pvi1)
    if start <= 1216 and (1216 - start) + 3 < len(regs):
        idx2 = 1216 - start
        pvi2 = decode_pvi_tuple(regs, idx2)
        print_pvi("PV2", pvi2)


def decode_inverter(start: int, regs: List[int]):
    if start <= 1509 and (1509 - start) + 3 < len(regs):
        idx1 = 1509 - start
        pvi1 = decode_pvi_tuple(regs, idx1)
        print_pvi("INV-L1", pvi1)
    if start <= 1516 and (1516 - start) + 3 < len(regs):
        idx2 = 1516 - start
        pvi2 = decode_pvi_tuple(regs, idx2)
        print_pvi("INV-L2", pvi2)
    if start <= 1523 and (1523 - start) + 3 < len(regs):
        idx3 = 1523 - start
        pvi3 = decode_pvi_tuple(regs, idx3)
        print_pvi("INV-L3", pvi3)


def decode_battery(start: int, regs: List[int]):
    if start <= 2006 and (2006 - start) + 3 < len(regs):
        idx = 2006 - start
        v = regs[idx] / 10.0
        if start <= 2008 and (2008 - start) < len(regs):
            ci = 2008 - start
            c = regs[ci]
            if c > 32767:
                c -= 65535 + 1
            c = c / 10.0
            p = int(v * c)
            print(f"BATTERY: P≈{p}W  V={v:.1f}V  I={c:.1f}A")


def decode_load(start: int, regs: List[int]):
    if start <= 1430 and (1437 - start) < len(regs):
        idx_status = 1430 - start
        idx_power = 1432 - start
        idx_voltage = 1437 - start
        status = regs[idx_status]
        p = regs[idx_power]
        if p > 32767:
            p -= 65535 + 1
        v = regs[idx_voltage] / 10.0
        print(f"LOAD: status={status}  P={p}W  V={v:.1f}V")


def decode_auto(start: int, regs: List[int]):
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


# ---------------------------------------------------------------------------
# MODES
# ---------------------------------------------------------------------------

async def deep_scan_registers(address: str, start_reg: int, end_reg: int, output_path: str):
    # Get device info for smart parsing
    devices = await check_addresses({address})
    if not devices:
        print('Warning: Device not found or unsupported, falling back to basic register scan.')
        device = None
        client = BluetoothClient(address)
    else:
        device = devices[0]
        client = BluetoothClient(device.address)

    asyncio.get_running_loop().create_task(client.run())
    while not client.is_ready: await asyncio.sleep(1)

    print(f"--- DEEP SCAN: {start_reg} to {end_reg} for device {device.type if device else 'Unknown'} ---")
    current = start_reg
    
    with open(output_path, 'a') as f:
        while current <= end_reg:
            print(f"Checking Register {current}...", end='\r')

            # Check if the current register is a known field
            field = None
            if device:
                field = next((f for f in device.struct.fields if f.address == current), None)

            if field:
                # Smart scan for a known field
                cmd = ReadHoldingRegisters(current, field.size)
                try:
                    fut = await client.perform(cmd)
                    res = cast(bytes, await asyncio.wait_for(fut, timeout=5.0))
                    body = cmd.parse_response(res)
                    parsed_val = field.parse(body)
                    log_entry = {
                        'regs': list(range(current, current + field.size)),
                        'field_name': field.name,
                        'val': serialize_value(parsed_val),
                        'hex': body.hex(),
                        'ts': time.time()
                    }
                    f.write(json.dumps(log_entry) + '\n')
                    f.flush()
                    current += field.size
                except Exception as e:
                    f.write(json.dumps({'reg': current, 'val': 'invalid', 'error': str(e), 'ts': time.time()}) + '\n')
                    f.flush()
                    current += 1
            else:
                # Dumb scan for an unknown register
                cmd = ReadHoldingRegisters(current, 1)
                try:
                    fut = await client.perform(cmd)
                    res = cast(bytes, await asyncio.wait_for(fut, timeout=5.0))
                    body = cmd.parse_response(res)
                    val = int.from_bytes(body, 'big')
                    signed_val = val - 65536 if val > 0x7FFF else val
                    log_entry = {
                        'reg': current,
                        'val': val,
                        'signed_val': signed_val,
                        'hex': body.hex(),
                        'ts': time.time()
                    }
                    f.write(json.dumps(log_entry) + '\n')
                    f.flush()
                    current += 1
                except Exception as e:
                    f.write(json.dumps({'reg': current, 'val': 'invalid', 'error': str(e), 'ts': time.time()}) + '\n')
                    f.flush()
                    current += 1
                
            # Tiny sleep to let the BLE radio breathe
            await asyncio.sleep(0.05)

    print(f"\n--- SCAN FINISHED ---")


async def scan_registers(address: str, start: int, count: int):
    devices = await check_addresses({address})
    if not devices: sys.exit('Device not found')
    client = BluetoothClient(devices[0].address)
    asyncio.get_running_loop().create_task(client.run())
    while not client.is_ready: await asyncio.sleep(1)
    
    command = ReadHoldingRegisters(start, count)
    fut = await client.perform(command)
    try:
        res = cast(bytes, await fut)
        body = command.parse_response(res)
        print(f"\n--- RAW SCAN @ {start} ---\n{body.hex()}")
    except Exception as e: print(f"Error: {e}")


async def watch_registers(address: str, start: int, count: int, interval: float, decode_mode: Optional[str]):
    devices = await check_addresses({address})
    if not devices: sys.exit('Device not found')
    client = BluetoothClient(devices[0].address)
    asyncio.get_running_loop().create_task(client.run())
    while not client.is_ready: await asyncio.sleep(1)

    last = None
    while True:
        cmd = ReadHoldingRegisters(start, count)
        fut = await client.perform(cmd)
        try:
            res = cast(bytes, await fut)
            body = cmd.parse_response(res)
            if body != last:
                print(f"[{time.strftime('%H:%M:%S')}] {body.hex()}")
                if decode_mode == 'auto': decode_auto(start, bytes_to_regs(body))
                last = body
        except Exception as e: print(f"Error: {e}")
        await asyncio.sleep(interval)


async def log(address: str, path: str):
    devices = await check_addresses({address})
    if not devices: sys.exit('Device not found')
    device = devices[0]
    client = BluetoothClient(device.address)
    asyncio.get_running_loop().create_task(client.run())
    with open(path, 'a') as f:
        while not client.is_ready: await asyncio.sleep(1)
        while True:
            for cmd in device.logging_commands:
                await log_command(client, device, cmd, f)
            await asyncio.sleep(10)


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description='EP2000 Logger')
    parser.add_argument('--deep-scan', action='store_true', help='Perform a deep scan of registers to the file specified by --log')
    parser.add_argument('--deep-scan-start', type=int, default=1)
    parser.add_argument('--deep-scan-count', type=int, default=31111)
    parser.add_argument('--scan-start', type=int)
    parser.add_argument('--scan-count', type=int)
    parser.add_argument('--watch-start', type=int)
    parser.add_argument('--watch-count', type=int)
    parser.add_argument('--watch-interval', type=float, default=1.0)
    parser.add_argument('--decode', choices=['auto', 'grid', 'pv', 'load', 'inverter', 'battery', 'tuple'])
    parser.add_argument('--log', metavar='PATH')
    parser.add_argument('address', metavar='ADDRESS', nargs='?')

    args = parser.parse_args()

    if args.deep_scan:
        if not args.log:
            parser.error('argument --log is required when using --deep-scan')
        asyncio.run(deep_scan_registers(args.address, args.deep_scan_start, args.deep_scan_start + args.deep_scan_count - 1, args.log))
    elif args.scan_start is not None:
        asyncio.run(scan_registers(args.address, args.scan_start, args.scan_count))
    elif args.watch_start is not None:
        asyncio.run(watch_registers(args.address, args.watch_start, args.watch_count, args.watch_interval, args.decode))
    elif args.log:
        asyncio.run(log(args.address, args.log))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
