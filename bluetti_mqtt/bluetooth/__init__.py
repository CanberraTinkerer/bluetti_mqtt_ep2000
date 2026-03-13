import logging
import re
from typing import Set
from bleak import BleakScanner
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bluetti_mqtt.core import BluettiDevice, AC200M, AC300, AC500, AC60, EP500, EP500P, EP600, EB3A, EP2000
from .client import BluetoothClient
from .exc import BadConnectionError, ModbusError, ParseError
from .manager import MultiDeviceManager

DEVICE_NAME_RE = re.compile(r'^(AC200M|AC300|AC500|AC60|EP500P|EP500|EP600|EB3A|EP2000|EBOX)(\d+)$')

RESPONSE_TIMEOUT = 5
WRITE_UUID = '0000ff02-0000-1000-8000-00805f9b34fb'
NOTIFY_UUID = '0000ff01-0000-1000-8000-00805f9b34fb'

async def send_command(client: BleakClient, command: BluettiDevice):
    """Sends a command to the Bluetti device and returns the response."""
    
    notify_future = asyncio.get_running_loop().create_future()
    notify_response = bytearray()

    def notification_handler(_sender: int, data: bytearray):
        """Handle notification responses."""
        # Ignore notifications we don't expect
        if not notify_future or notify_future.done():
            return

        # If something went wrong, we might get weird data.
        if data == b'AT+NAME?\r' or data == b'AT+ADV?\r':
            err = BadConnectionError('Got AT+ notification')
            notify_future.set_exception(err)
            return

        # Save data
        notify_response.extend(data)

        if len(notify_response) == command.response_size():
            if command.is_valid_response(notify_response):
                notify_future.set_result(notify_response)
            else:
                notify_future.set_exception(ParseError('Failed checksum'))
        elif command.is_exception_response(notify_response):
            # We got a MODBUS command exception
            msg = f'MODBUS Exception {command}: {notify_response[2]}'
            notify_future.set_exception(ModbusError(msg))

    await client.start_notify(NOTIFY_UUID, notification_handler)
    
    await client.write_gatt_char(WRITE_UUID, bytes(command))
    
    try:
        return await asyncio.wait_for(notify_future, timeout=RESPONSE_TIMEOUT)
    finally:
        await client.stop_notify(NOTIFY_UUID)

async def scan_devices():
    print('Scanning....')
    devices = await BleakScanner.discover()
    if len(devices) == 0:
        print('0 devices found - something probably went wrong')
    else:
        bluetti_devices = [d for d in devices if d.name and DEVICE_NAME_RE.match(d.name)]
        for d in bluetti_devices:
            print(f'Found {d.name}: address {d.address}')


def build_device(address: str, name: str):
    match = DEVICE_NAME_RE.match(name)
    if match[1] == 'AC200M':
        return AC200M(address, match[2])
    if match[1] == 'AC300':
        return AC300(address, match[2])
    if match[1] == 'AC500':
        return AC500(address, match[2])
    if match[1] == 'AC60':
        return AC60(address, match[2])
    if match[1] == 'EP500':
        return EP500(address, match[2])
    if match[1] == 'EP500P':
        return EP500P(address, match[2])
    if match[1] == 'EP600':
        return EP600(address, match[2])
    if match[1] == 'EB3A':
        return EB3A(address, match[2])
    if match[1] in ('EP2000', 'EBOX'):
        return EP2000(address, match[2])
        


async def check_addresses(addresses: Set[str]):
    logging.debug(f'Checking we can connect: {addresses}')
    devices = await BleakScanner.discover()
    filtered = [d for d in devices if d.address in addresses]
    logging.debug(f'Found devices: {filtered}')

    if len(filtered) != len(addresses):
        return []

    return [build_device(d.address, d.name) for d in filtered]
