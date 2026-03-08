from typing import List
from ..commands import ReadHoldingRegisters
from .bluetti_device import BluettiDevice
from .struct import DeviceStruct


class EP2000(BluettiDevice):
    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # --- Core system info ---
        self.struct.add_uint_field("battery_soc", 100)
        self.struct.add_decimal_field("battery_power", 101)  # W (+ discharge / - charge)
        self.struct.add_uint_field("total_battery_percent", 102)

        # --- PV inputs ---
        self.struct.add_uint_field("pv1_voltage", 103)
        self.struct.add_uint_field("pv1_current", 104)
        self.struct.add_uint_field("pv1_power", 105)

        self.struct.add_uint_field("pv2_voltage", 106)
        self.struct.add_uint_field("pv2_current", 107)
        self.struct.add_uint_field("pv2_power", 108)

        # --- Grid import/export per phase ---
        self.struct.add_decimal_field("grid_l1_power", 109)
        self.struct.add_decimal_field("grid_l2_power", 110)
        self.struct.add_decimal_field("grid_l3_power", 111)

        # --- AC output / load ---
        self.struct.add_uint_field("ac_output_voltage_l1", 112)
        self.struct.add_uint_field("ac_output_voltage_l2", 113)
        self.struct.add_uint_field("ac_output_voltage_l3", 114)

        self.struct.add_uint_field("ac_output_current_l1", 115)
        self.struct.add_uint_field("ac_output_current_l2", 116)
        self.struct.add_uint_field("ac_output_current_l3", 117)

        self.struct.add_uint_field("ac_output_power_l1", 118)
        self.struct.add_uint_field("ac_output_power_l2", 119)
        self.struct.add_uint_field("ac_output_power_l3", 120)

        self.struct.add_uint_field("ac_output_total_power", 121)

        # --- AC coupling ---
        self.struct.add_decimal_field("ac_coupling_power", 122)

        # --- Temperatures ---
        self.struct.add_decimal_field("temperature_inverter", 123)
        self.struct.add_decimal_field("temperature_battery", 124)
        self.struct.add_decimal_field("temperature_mppt", 125)

        # --- Status flags ---
        self.struct.add_uint_field("status_flags", 126)
        self.struct.add_uint_field("warning_flags", 127)
        self.struct.add_uint_field("fault_flags", 128)

        # --- Device identity ---
        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)

        # --- Battery range controls ---
        self.struct.add_uint_field("battery_range_start", 2022)
        self.struct.add_uint_field("battery_range_end", 2023)

        super().__init__(address, "EP2000", sn)

    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 62),   # Main EMS block
            ReadHoldingRegisters(2022, 2),   # Battery range
        ]

    @property
    def logging_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 62),
            ReadHoldingRegisters(1100, 51),
            ReadHoldingRegisters(1200, 90),
            ReadHoldingRegisters(1300, 31),
            ReadHoldingRegisters(1400, 48),
            ReadHoldingRegisters(1500, 30),
            ReadHoldingRegisters(2000, 89),
            ReadHoldingRegisters(2200, 41),
            ReadHoldingRegisters(2300, 36),
            ReadHoldingRegisters(6000, 32),
            ReadHoldingRegisters(6100, 100),
            ReadHoldingRegisters(6300, 100),
        ]
