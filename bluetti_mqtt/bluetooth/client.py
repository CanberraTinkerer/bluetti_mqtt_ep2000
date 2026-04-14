import asyncio
from enum import Enum, auto, unique
import logging
from typing import Union
import os
import hashlib
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from bleak import BleakClient, BleakError
from bluetti_mqtt.core import DeviceCommand
from .exc import BadConnectionError, ModbusError, ParseError


@unique
class ClientState(Enum):
    NOT_CONNECTED = auto()
    CONNECTED = auto()
    READY = auto()
    PERFORMING_COMMAND = auto()
    COMMAND_ERROR_WAIT = auto()
    DISCONNECTING = auto()


class BluetoothClient:
    RESPONSE_TIMEOUT = 3
    # Standard Bluetti BLE characteristics (FF00 service)
    SERVICE_UUID = '0000ff00-0000-1000-8000-00805f9b34fb'
    WRITE_UUID = '0000ff02-0000-1000-8000-00805f9b34fb'
    NOTIFY_UUID = '0000ff01-0000-1000-8000-00805f9b34fb'
    DEVICE_NAME_UUID = '00002a00-0000-1000-8000-00805f9b34fb'

    name: Union[str, None]
    current_command: DeviceCommand
    notify_future: asyncio.Future
    notify_response: bytearray
    session_key: Union[bytes, None]  # ECDH-derived session key for V2 encryption

    def __init__(self, address: str, response_timeout: float = None, debug_logging: bool = False, device_name: str = None):
        self.address = address
        self.response_timeout = response_timeout or self.RESPONSE_TIMEOUT
        self.debug_logging = debug_logging
        self.device_name = device_name
        self.state = ClientState.NOT_CONNECTED
        self.name = None
        self.client = BleakClient(self.address)
        self.command_queue = asyncio.Queue()
        self.notify_future = None
        self.loop = asyncio.get_running_loop()
        self.session_key = None  # Will be set after ECDH handshake

        # Set logging level based on debug flag
        if self.debug_logging:
            logging.getLogger().setLevel(logging.DEBUG)

    @property
    def is_ready(self):
        return self.state == ClientState.READY or self.state == ClientState.PERFORMING_COMMAND

    async def perform(self, cmd: DeviceCommand):
        future = self.loop.create_future()
        await self.command_queue.put((cmd, future))
        return future

    async def perform_with_fallback(self, cmd: DeviceCommand, device_protocol: str = "v2"):
        """Perform command with protocol fallback logic.

        If device_protocol == "v1": treat all reads/writes as plaintext.
        If device_protocol == "v2": try V2 first, then try V1 on timeout.
        """
        from bluetti_mqtt.mqtt_debugger import ReadHoldingRegisters, WriteSingleRegister
        from bluetti_mqtt.mqtt_debugger import ReadHoldingRegistersV2, WriteSingleRegisterV2

        def convert_to_v1(original_cmd: DeviceCommand):
            if isinstance(original_cmd, ReadHoldingRegistersV2):
                return ReadHoldingRegisters(original_cmd.starting_address, original_cmd.quantity, original_cmd.slave_id)
            if isinstance(original_cmd, WriteSingleRegisterV2):
                return WriteSingleRegister(original_cmd.address, original_cmd.value, original_cmd.slave_id)
            return original_cmd

        # If forced v1, convert and execute directly
        if device_protocol == "v1":
            v1_cmd = convert_to_v1(cmd)
            return await self.perform(v1_cmd)

        # Normal v2 behavior: try original cmd first
        try:
            return await self.perform(cmd)
        except BadConnectionError as e:
            if "too many retries" not in str(e):
                raise

            logging.info(f"V2 command failed for {self.address} (too many retries), trying V1 fallback")
            v1_cmd = convert_to_v1(cmd)

            try:
                logging.info(f"Attempting V1 fallback for {self.address}")
                return await self.perform(v1_cmd)
            except Exception:
                logging.error(f"V1 fallback also failed for {self.address}")
                raise e

    async def run(self):
        try:
            while True:
                if self.state == ClientState.NOT_CONNECTED:
                    await self._connect()
                elif self.state == ClientState.CONNECTED:
                    if not self.name:
                        await self._get_name()
                    elif not self.session_key:
                        await self._perform_ecdh_handshake()
                    else:
                        await self._start_listening()
                elif self.state == ClientState.READY:
                    await self._perform_command()
                elif self.state == ClientState.DISCONNECTING:
                    await self._disconnect()
                else:
                    logging.warn(f'Unexpected current state {self.state}')
                    self.state = ClientState.NOT_CONNECTED
        finally:
            # Ensure that we disconnect
            if self.client:
                await self.client.disconnect()

    async def _connect(self):
        """Establish connection to the bluetooth device"""
        try:
            await self.client.connect()
            self.state = ClientState.CONNECTED
            logging.info(f'Connected to device: {self.address}')
        except BleakError:
            logging.debug(f'Error connecting to device {self.address}: Not found')
        except (BleakError, EOFError, asyncio.TimeoutError):
            logging.exception(f'Error connecting to device {self.address}:')
            await asyncio.sleep(1)

    async def _get_name(self):
        """Get device name, which can be parsed for type"""
        try:
            name = await self.client.read_gatt_char(self.DEVICE_NAME_UUID)
            self.name = name.decode('ascii')
            logging.info(f'Device {self.address} has name: {self.name}')
        except BleakError:
            logging.exception(f'Error retrieving device name {self.address}:')
            self.state = ClientState.DISCONNECTING

    async def _start_listening(self):
        """Register for command response notifications"""
        try:
            await self.client.start_notify(
                self.NOTIFY_UUID,
                self._notification_handler)
            self.state = ClientState.READY
        except BleakError:
            self.state = ClientState.DISCONNECTING

    async def _perform_ecdh_handshake(self):
        """Perform ECDH key exchange handshake with the device"""
        try:
            logging.info(f'Starting ECDH handshake with {self.address}')

            # Generate our ECDH key pair (secp256r1)
            private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
            public_key = private_key.public_key()

            # Serialize public key to compressed format (64 bytes, no DER prefix)
            public_key_bytes = public_key.public_key_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.CompressedPoint
            )
            logging.debug(f'Generated public key: {public_key_bytes.hex()}')

            # Generate random 16-byte challenge
            challenge = os.urandom(16)
            logging.debug(f'Generated challenge: {challenge.hex()}')

            # Send handshake initiation: 2A 2A + challenge
            init_packet = b'\x2A\x2A' + challenge
            logging.debug(f'Sending handshake init: {init_packet.hex()}')

            # Set up notification for handshake response
            handshake_future = self.loop.create_future()
            handshake_response = bytearray()

            def handshake_handler(sender, data):
                handshake_response.extend(data)
                logging.debug(f'Handshake notification: {data.hex()} (total: {len(handshake_response)})')

                # Check if we have a complete response (device public key)
                if len(handshake_response) >= 64:  # 64-byte compressed public key
                    handshake_future.set_result(handshake_response[:64])

            # Start listening for handshake response
            await self.client.start_notify(self.NOTIFY_UUID, handshake_handler)

            # Send initiation packet
            await self.client.write_gatt_char(self.WRITE_UUID, init_packet)

            # Wait for device public key response
            device_public_key_bytes = await asyncio.wait_for(handshake_future, timeout=10.0)
            logging.debug(f'Received device public key: {device_public_key_bytes.hex()}')

            # Deserialize device public key
            device_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), device_public_key_bytes
            )

            # Send our public key to device
            await self.client.write_gatt_char(self.WRITE_UUID, public_key_bytes)
            logging.debug(f'Sent our public key to device')

            # Perform ECDH key agreement
            shared_secret = private_key.exchange(ec.ECDH(), device_public_key)
            logging.debug(f'ECDH shared secret: {shared_secret.hex()}')

            # Derive session key using HKDF (similar to Bluetti's KDF)
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=16,  # 16-byte AES key
                salt=challenge,  # Use challenge as salt
                info=b'bluetti_session_key',
                backend=default_backend()
            )
            self.session_key = hkdf.derive(shared_secret)
            logging.info(f'ECDH handshake complete, session key established: {self.session_key.hex()}')

            # Stop handshake notifications and restart normal notifications
            await self.client.stop_notify(self.NOTIFY_UUID)
            await self.client.start_notify(self.NOTIFY_UUID, self._notification_handler)

        except Exception as e:
            logging.error(f'ECDH handshake failed for {self.address}: {e}')
            self.state = ClientState.DISCONNECTING
            raise

    async def _perform_command(self):
        cmd, cmd_future = await self.command_queue.get()
        retries = 0
        while retries < 5:
            try:
                # Prepare to make request
                self.state = ClientState.PERFORMING_COMMAND
                self.current_command = cmd
                self.notify_future = self.loop.create_future()
                self.notify_response = bytearray()

                # Log command being sent
                cmd_bytes = bytes(self.current_command)
                is_v2 = len(cmd_bytes) > 2 and cmd_bytes[0] == 0x00 and cmd_bytes[1] == 0x17
                proto = "V2 Encrypted" if is_v2 else "Plaintext"
                logging.debug(f'Sending {proto} command to {self.address}: {cmd_bytes.hex()} (attempt {retries + 1}/5)')

                # Make request
                await self.client.write_gatt_char(
                    self.WRITE_UUID,
                    cmd_bytes)

                # Wait for response
                logging.debug(f'Waiting for response from {self.address} (timeout: {self.response_timeout}s)')
                res = await asyncio.wait_for(
                    self.notify_future,
                    timeout=self.response_timeout)

                logging.debug(f'Received response from {self.address}: {res.hex()}')
                if cmd_future:
                    cmd_future.set_result(res)

                # Success!
                self.state = ClientState.READY
                break
            except ParseError as e:
                logging.warning(f'ParseError on attempt {retries + 1}/5 for {self.address}: {e}. Response so far: {self.notify_response.hex() if self.notify_response else "None"}')
                # For safety, wait the full timeout before retrying again
                self.state = ClientState.COMMAND_ERROR_WAIT
                retries += 1
                await asyncio.sleep(self.response_timeout)
            except asyncio.TimeoutError:
                logging.warning(f'Timeout on attempt {retries + 1}/5 for {self.address}: No response within {self.response_timeout}s')
                self.state = ClientState.COMMAND_ERROR_WAIT
                retries += 1
            except ModbusError as err:
                logging.debug(f'ModbusError for {self.address}: {err}')
                if cmd_future:
                    cmd_future.set_exception(err)

                # Don't retry
                self.state = ClientState.READY
                break
            except (BleakError, EOFError, BadConnectionError) as err:
                logging.error(f'Connection error for {self.address}: {err}')
                if cmd_future:
                    cmd_future.set_exception(err)

                self.state = ClientState.DISCONNECTING
                break

        if retries == 5:
            logging.error(f'Command failed after 5 retries for {self.address}: {bytes(cmd).hex()}')
            err = BadConnectionError('too many retries')
            if cmd_future:
                cmd_future.set_exception(err)
            self.state = ClientState.DISCONNECTING

        self.command_queue.task_done()

    async def _disconnect(self):
        await self.client.disconnect()
        logging.warn(f'Delayed reconnect to {self.address} after error')
        await asyncio.sleep(5)
        self.state = ClientState.NOT_CONNECTED

    def _notification_handler(self, _sender: int, data: bytearray):
        # Ignore notifications we don't expect
        if not self.notify_future or self.notify_future.done():
            logging.debug(f'Ignoring unexpected notification from {self.address}: {data.hex()}')
            return

        # If something went wrong, we might get weird data.
        if data == b'AT+NAME?\r' or data == b'AT+ADV?\r':
            logging.warning(f'Received AT+ command response from {self.address}: {data}')
            err = BadConnectionError('Got AT+ notification')
            self.notify_future.set_exception(err)
            return

        # Save data
        self.notify_response.extend(data)
        logging.debug(f'Received notification chunk from {self.address}: {data.hex()} (total so far: {len(self.notify_response)}/{self.current_command.response_size()} bytes)')

        if len(self.notify_response) == self.current_command.response_size():
            if self.current_command.is_valid_response(self.notify_response):
                logging.debug(f'Valid complete response received from {self.address}')
                self.notify_future.set_result(self.notify_response)
            else:
                logging.warning(f'Invalid checksum for response from {self.address}: {self.notify_response.hex()}')
                self.notify_future.set_exception(ParseError('Failed checksum'))
        elif self.current_command.is_exception_response(self.notify_response):
            # We got a MODBUS command exception
            msg = f'MODBUS Exception {self.current_command}: {self.notify_response[2]}'
            logging.warning(f'{msg} from {self.address}')
            self.notify_future.set_exception(ModbusError(msg))
