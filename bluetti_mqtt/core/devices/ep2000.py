from typing import List
from ..commands import ReadHoldingRegisters
from .bluetti_device import BluettiDevice
from .struct import DeviceStruct



class EP2000(BluettiDevice):
    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # Battery / identity
        self.struct.add_uint_field("battery_soc", 100)
        self.struct.add_decimal_field("battery_power", 101, 0)
        self.struct.add_uint_field("total_battery_percent", 102)

        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)

        # totals
        self.struct.add_uint_field("pv_input_power_all", 144)
        self.struct.add_uint_field("consumption_power_all", 142)
        self.struct.add_uint_field("grid_power_all", 146)

        # statistics
        self.struct.add_decimal_field("total_ac_consumption", 152, 1)
        self.struct.add_decimal_field("total_grid_consumption", 156, 1)
        self.struct.add_decimal_field("total_grid_feed", 158, 1)
        
        # NEW: model code from 1101 ("PE0200")
        self.struct.add_swap_string_field("model_code", 1101, 6)

        # PV
        self.struct.add_uint_field("pv1_power", 1212) #found
        self.struct.add_decimal_field("pv1_voltage", 1213, 1) #found
        self.struct.add_decimal_field("pv1_current", 1214, 1) #found
        self.struct.add_uint_field("pv2_power", 1220) #found
        self.struct.add_decimal_field("pv2_voltage", 1221, 1) #found
        self.struct.add_decimal_field("pv2_current", 1222, 1) #found

        # AC‑coupled PV (phase 1)
        self.struct.add_uint_field("adl400_ac_input_power_phase1", 1228)
        self.struct.add_decimal_field("adl400_ac_input_voltage_phase1", 1229, 1)
        self.struct.add_uint_field("adl400_ac_input_current_phase1", 1230)

        # AC‑coupled PV (phase 2 & 3) — EP2000 is 3‑phase, so include them
        self.struct.add_uint_field("adl400_ac_input_power_phase2", 1236)
        self.struct.add_uint_field("adl400_ac_input_power_phase3", 1244)

        self.struct.add_decimal_field("adl400_ac_input_voltage_phase2", 1237, 1)
        self.struct.add_decimal_field("adl400_ac_input_voltage_phase3", 1245, 1)

        self.struct.add_uint_field("adl400_ac_input_current_phase2", 1238)
        self.struct.add_uint_field("adl400_ac_input_current_phase3", 1246)

        # grid data
        self.struct.add_decimal_field("grid_frequency", 1300, 1)

        self.struct.add_uint_field("grid_power_phase1", 1313)
        self.struct.add_uint_field("grid_power_phase2", 1319)
        self.struct.add_uint_field("grid_power_phase3", 1325)

        self.struct.add_decimal_field("grid_voltage_phase1", 1314, 1)
        self.struct.add_decimal_field("grid_voltage_phase2", 1320, 1)
        self.struct.add_decimal_field("grid_voltage_phase3", 1326, 1)

        self.struct.add_decimal_field("grid_current_phase1", 1315, 1)
        self.struct.add_decimal_field("grid_current_phase2", 1321, 1)
        self.struct.add_decimal_field("grid_current_phase3", 1327, 1)

        self.struct.add_decimal_field("ac_output_frequency", 1500, 1)

        # AC output
        self.struct.add_int_field("ac_output_power_phase1", 1510)
        self.struct.add_int_field("ac_output_power_phase2", 1517)
        self.struct.add_int_field("ac_output_power_phase3", 1524)

        self.struct.add_decimal_field("ac_output_voltage_phase1", 1511, 1)
        self.struct.add_decimal_field("ac_output_voltage_phase2", 1518, 1)
        self.struct.add_decimal_field("ac_output_voltage_phase3", 1525, 1)

        self.struct.add_decimal_field("ac_output_current_phase1", 1512, 1)
        self.struct.add_decimal_field("ac_output_current_phase2", 1519, 1)
        self.struct.add_decimal_field("ac_output_current_phase3", 1526, 1)

        # house consumption
        self.struct.add_int_field("consumption_power_phase1", 1430)
        self.struct.add_int_field("consumption_power_phase2", 1436)
        self.struct.add_int_field("consumption_power_phase3", 1442)

        self.struct.add_decimal_field("consumption_voltage_phase1", 1431, 1)
        self.struct.add_decimal_field("consumption_voltage_phase2", 1437, 1)
        self.struct.add_decimal_field("consumption_voltage_phase3", 1443, 1)

        self.struct.add_decimal_field("consumption_current_phase1", 1432, 1)
        self.struct.add_decimal_field("consumption_current_phase2", 1438, 1)
        self.struct.add_decimal_field("consumption_current_phase3", 1444, 1)

      
        # Controls / battery range
        self.struct.add_bool_field("ac_control_enabled", 2011)
        self.struct.add_uint_field("battery_range_start", 2022) #found
        self.struct.add_uint_field("battery_range_end", 2023) #found

        # NEW: generator control (2246)
        self.struct.add_bool_field("generator_control_enabled", 2246)

        # NEW: grid limits (2435–2438)
        self.struct.add_decimal_field("grid_reconnect_voltage_low_limit", 2435, 1)
        self.struct.add_decimal_field("grid_reconnect_voltage_high_limit", 2436, 1)
        self.struct.add_decimal_field("grid_reconnect_frequency_low_limit", 2437, 2)
        self.struct.add_decimal_field("grid_reconnect_frequency_high_limit", 2438, 2)

        # WiFi name
        self.struct.add_swap_string_field("wifi_name", 12002, 16)

        super().__init__(address, "EP2000", sn)

    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 40),     # battery + identity + model_code
            ReadHoldingRegisters(1212, 20),    # PV1 + PV2
            ReadHoldingRegisters(2000, 30),    # AC control + battery range
            ReadHoldingRegisters(2240, 10),    # generator control region
            ReadHoldingRegisters(2400, 40),    # grid limits region
            ReadHoldingRegisters(12002, 16),   # WiFi name
        ]

    @property
    def logging_commands(self) -> List[ReadHoldingRegisters]:
        return self.polling_commands
