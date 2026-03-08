from typing import List
from ..commands import ReadHoldingRegisters
from .bluetti_device import BluettiDevice
from .struct import DeviceStruct


class EP2000(BluettiDevice):
    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # -------------------------
        # Battery / Identity Fields
        # -------------------------
        self.struct.add_uint_field("battery_soc", 100)
        self.struct.add_decimal_field("battery_power", 101, 0)
        self.struct.add_uint_field("total_battery_percent", 102)

        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)

        self.struct.add_uint_field("battery_range_start", 2022)
        self.struct.add_uint_field("battery_range_end", 2023)

        # -------------------------
        # Patrick’s Verified EP2000 EMS Fields
        # -------------------------

        # PV1
        self.struct.add_uint_field("pv1_power", 1212)
        self.struct.add_decimal_field("pv1_voltage", 1213, 1)
        self.struct.add_decimal_field("pv1_current", 1214, 1)

        # PV2
        self.struct.add_uint_field("pv2_power", 1220)
        self.struct.add_decimal_field("pv2_voltage", 1221, 1)
        self.struct.add_decimal_field("pv2_current", 1222, 1)

        # Grid
        self.struct.add_decimal_field("grid_frequency", 1300, 1)
        self.struct.add_decimal_field("grid_voltage_l1", 1314, 1)
        self.struct.add_decimal_field("grid_voltage_l2", 1320, 1)
        self.struct.add_decimal_field("grid_voltage_l3", 1326, 1)

        # AC Output
        self.struct.add_decimal_field("ac_output_frequency", 1500, 1)
        self.struct.add_decimal_field("ac_output_voltage_l1", 1511, 1)
        self.struct.add_decimal_field("ac_output_voltage_l2", 1518, 1)
        self.struct.add_decimal_field("ac_output_voltage_l3", 1525, 1)

        # Controls
        self.struct.add_bool_field("ac_control_enabled", 2011)
        self.struct.add_bool_field("generator_control_enabled", 2246)

        # Grid limits
        self.struct.add_decimal_field("grid_voltage_min", 2435, 1)
        self.struct.add_decimal_field("grid_voltage_max", 2436, 1)
        self.struct.add_decimal_field("grid_freq_min", 2437, 2)
        self.struct.add_decimal_field("grid_freq_max", 2438, 2)

        # WiFi name
        self.struct.add_swap_string_field("wifi_name", 12002, 16)

        super().__init__(address, "EP2000", sn)

    # -------------------------
    # Correct Polling Blocks
    # -------------------------
    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 30),     # battery + identity
            ReadHoldingRegisters(1212, 20),    # PV1 + PV2
            ReadHoldingRegisters(1300, 40),    # grid + AC output
            ReadHoldingRegisters(2000, 50),    # controls + limits
            ReadHoldingRegisters(12002, 16),   # WiFi name
        ]

    @property
    def logging_commands(self) -> List[ReadHoldingRegisters]:
        return self.polling_commands
