import logging
from typing import List, Dict
from ..commands import ReadHoldingRegisters
from .bluetti_device import BluettiDevice
from .struct import DeviceStruct

def _to_signed16(v: int) -> int:
    if v is None:
        return None
    return v - 65536 if v > 0x7FFF else v

def _to_signed32_swapped(low: int, high: int) -> int:
    if low is None or high is None:
        return None
    # EP2000 uses low-word first, high-word second in many 32-bit totals
    val = (high << 16) | (low & 0xFFFF)
    return val - (1 << 32) if val & (1 << 31) else val

class EP2000(BluettiDevice):
    # Class-level constant for fields that are calculated in the parse() method
    CALCULATED_FIELDS = {
        'total_ac_consumption', 'total_grid_consumption', 'total_grid_feed',
        'total_inverter_power', 'total_dc_energy', 'total_dc_power',
        'pv_input_power_all', 'grid_power_all', 'consumption_power_all',
        'pv_dc_total_power', 'pv_ac_total_power', 'inverter_sum_power',
        'self_consumption_power'
    }

    # Mapping of calculated fields to their source MODBUS registers for Home Assistant
    CALCULATED_FIELD_REGISTERS = {
        'total_ac_consumption': [152, 153],
        'total_grid_consumption': [156, 157],
        'total_grid_feed': [158, 159],
        'total_dc_power': [140, 141],
        'total_inverter_power': [148, 149],
        'total_dc_energy': [150, 151],
        'pv_input_power_all': [144, 145],
        'grid_power_all': [146, 147],
        'consumption_power_all': [142, 143],
        # These are sums, so their registers are the components' registers
        'pv_dc_total_power': [1212, 1220],
        'pv_ac_total_power': [1228, 1236, 1244],
        'inverter_sum_power': [1510, 1517, 1524],
        # Self consumption is PV total generation minus grid export (negative grid power)
        'self_consumption_power': [1212, 1220, 1228, 1236, 1244, 146, 147],
    }

    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # --- Identity / battery ---
        self.struct.add_decimal_field("pack_total_voltage", 100, 1)
        self.struct.add_decimal_field("pack_total_current", 101, 1)
        self.struct.add_uint_field("total_battery_percent", 102)

        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)
        self.struct.add_swap_string_field("model_code", 1101, 6)
 
        # --- Totals (32-bit swapped) ---
        self.struct.add_uint_field("total_dc_power_low", 140)
        self.struct.add_uint_field("total_dc_power_high", 141)
        self.struct.add_uint_field("consumption_power_all_low", 142)
        self.struct.add_uint_field("consumption_power_all_high", 143)
        self.struct.add_uint_field("pv_input_power_all_low", 144)
        self.struct.add_uint_field("pv_input_power_all_high", 145)
        self.struct.add_uint_field("grid_power_all_low", 146)
        self.struct.add_uint_field("grid_power_all_high", 147)
        self.struct.add_uint_field("total_inverter_power_low", 148)
        self.struct.add_uint_field("total_inverter_power_high", 149)

        # --- Energy statistics (32-bit swapped, scaled) ---
        self.struct.add_uint_field("total_dc_energy_low", 150)
        self.struct.add_uint_field("total_dc_energy_high", 151)
        self.struct.add_uint_field("total_ac_consumption_low", 152)
        self.struct.add_uint_field("total_ac_consumption_high", 153)

        self.struct.add_uint_field("total_grid_consumption_low", 156)
        self.struct.add_uint_field("total_grid_consumption_high", 157)

        self.struct.add_uint_field("total_grid_feed_low", 158)
        self.struct.add_uint_field("total_grid_feed_high", 159)

        # --- PV DC strings ---
        self.struct.add_sint_field("pv1_power", 1212)
        self.struct.add_decimal_field("pv1_voltage", 1213, 1)
        self.struct.add_decimal_field("pv1_current", 1214, 1)

        self.struct.add_sint_field("pv2_power", 1220)
        self.struct.add_decimal_field("pv2_voltage", 1221, 1)
        self.struct.add_decimal_field("pv2_current", 1222, 1)

        # --- ADL400 AC-coupled PV ---
        self.struct.add_sint_field("adl400_ac_input_power_phase1", 1228)
        self.struct.add_decimal_field("adl400_ac_input_voltage_phase1", 1229, 1)
        self.struct.add_decimal_field("adl400_ac_input_current_phase1", 1230, 1)

        self.struct.add_sint_field("adl400_ac_input_power_phase2", 1236)
        self.struct.add_decimal_field("adl400_ac_input_voltage_phase2", 1237, 1)
        self.struct.add_decimal_field("adl400_ac_input_current_phase2", 1238, 1)

        self.struct.add_sint_field("adl400_ac_input_power_phase3", 1244)
        self.struct.add_decimal_field("adl400_ac_input_voltage_phase3", 1245, 1)
        self.struct.add_decimal_field("adl400_ac_input_current_phase3", 1246, 1)

        # --- Grid data (phase grid values) ---
        self.struct.add_decimal_field("grid_frequency_hz", 1300, 1)

        self.struct.add_sint_field("grid_power_phase1", 1313)
        self.struct.add_decimal_field("grid_voltage_phase1_v", 1314, 1)
        self.struct.add_decimal_field("grid_current_phase1_a", 1315, 1)

        self.struct.add_sint_field("grid_power_phase2", 1319)
        self.struct.add_decimal_field("grid_voltage_phase2_v", 1320, 1)
        self.struct.add_decimal_field("grid_current_phase2_a", 1321, 1)

        self.struct.add_sint_field("grid_power_phase3", 1325)
        self.struct.add_decimal_field("grid_voltage_phase3_v", 1326, 1)
        self.struct.add_decimal_field("grid_current_phase3_a", 1327, 1)

        # --- AC output / inverter phases (inverter contribution to AC bus) ---
        self.struct.add_sint_field("ac_output_power_phase1", 1510)
        self.struct.add_decimal_field("ac_output_voltage_phase1", 1511, 1)
        self.struct.add_decimal_field("ac_output_current_phase1", 1512, 1)

        self.struct.add_sint_field("ac_output_power_phase2", 1517)
        self.struct.add_decimal_field("ac_output_voltage_phase2", 1518, 1)
        self.struct.add_decimal_field("ac_output_current_phase2", 1519, 1)

        self.struct.add_sint_field("ac_output_power_phase3", 1524)
        self.struct.add_decimal_field("ac_output_voltage_phase3", 1525, 1)
        self.struct.add_decimal_field("ac_output_current_phase3", 1526, 1)

        # --- House consumption (per-phase load) ---
        self.struct.add_sint_field("consumption_power_phase1", 1430)
        self.struct.add_decimal_field("consumption_voltage_phase1_v", 1431, 1)
        self.struct.add_decimal_field("consumption_current_phase1_a", 1432, 1)

        self.struct.add_sint_field("consumption_power_phase2", 1436)
        self.struct.add_decimal_field("consumption_voltage_phase2_v", 1437, 1)
        self.struct.add_decimal_field("consumption_current_phase2_a", 1438, 1)

        self.struct.add_sint_field("consumption_power_phase3", 1442)
        self.struct.add_decimal_field("consumption_voltage_phase3_v", 1443, 1)
        self.struct.add_decimal_field("consumption_current_phase3_a", 1444, 1)

        # --- Controls / battery range ---
        self.struct.add_bool_field("ac_control_enabled", 2011) # TODO: Check if this is correct
        self.struct.add_uint_field("battery_range_start", 2022)
        self.struct.add_uint_field("battery_range_end", 2023)
        self.struct.add_bool_field("generator_control_enabled", 2246) # TODO: Check if this is correct

        # --- Grid limits (decimal scaling) ---
        self.struct.add_decimal_field("grid_reconnect_voltage_low_limit_v", 2435, 1)
        self.struct.add_decimal_field("grid_reconnect_voltage_high_limit_v", 2436, 1)
        self.struct.add_decimal_field("grid_reconnect_frequency_low_limit_hz", 2437, 2)
        self.struct.add_decimal_field("grid_reconnect_frequency_high_limit_hz", 2438, 2)
 
        # --- WiFi name ---
        self.struct.add_swap_string_field("wifi_name", 12002, 16)

        super().__init__(address, "EP2000", sn)

    def has_field(self, name: str) -> bool:
        # Check struct fields first, then calculated fields
        return any(f.name == name for f in self.struct.fields) or name in self.CALCULATED_FIELDS

    def get_field_registers(self, field_name: str) -> List[int]:
        # Check calculated fields first
        if field_name in self.CALCULATED_FIELD_REGISTERS:
            return self.CALCULATED_FIELD_REGISTERS[field_name]

        # Then try to find in struct for other fields
        field_def = next((f for f in self.struct.fields if f.name == field_name), None)
        if field_def:
            return list(range(field_def.address, field_def.address + field_def.size))

        return []
    
    def parse(self, address: int, data: bytes):
        # Directly parse registers into a dictionary
        parsed = self.struct.parse(address, data)
        logging.debug(f'Raw parsed data from struct: {parsed}')

        # Combine 32-bit values
        self._combine_and_scale_u32(parsed, 'total_ac_consumption', 'total_ac_consumption_low', 'total_ac_consumption_high', 0.1)
        self._combine_and_scale_u32(parsed, 'total_grid_consumption', 'total_grid_consumption_low', 'total_grid_consumption_high', 0.1)
        self._combine_and_scale_u32(parsed, 'total_grid_feed', 'total_grid_feed_low', 'total_grid_feed_high', 0.1)
        self._combine_and_scale_u32(parsed, 'total_dc_energy', 'total_dc_energy_low', 'total_dc_energy_high', 0.1)

        self._combine_u32(parsed, 'total_dc_power', 'total_dc_power_low', 'total_dc_power_high')
        self._combine_u32(parsed, 'total_inverter_power', 'total_inverter_power_low', 'total_inverter_power_high')
        self._combine_u32(parsed, 'consumption_power_all', 'consumption_power_all_low', 'consumption_power_all_high')
        self._combine_u32(parsed, 'pv_input_power_all', 'pv_input_power_all_low', 'pv_input_power_all_high')
        self._combine_s32(parsed, 'grid_power_all', 'grid_power_all_low', 'grid_power_all_high')

        # Calculate summed power values
        pv_dc_total = parsed.get('pv1_power', 0) + parsed.get('pv2_power', 0)
        parsed['pv_dc_total_power'] = pv_dc_total

        pv_ac_total = (
            parsed.get('adl400_ac_input_power_phase1', 0) +
            parsed.get('adl400_ac_input_power_phase2', 0) +
            parsed.get('adl400_ac_input_power_phase3', 0)
        )
        parsed['pv_ac_total_power'] = pv_ac_total

        parsed['inverter_sum_power'] = (
            parsed.get('ac_output_power_phase1', 0) +
            parsed.get('ac_output_power_phase2', 0) +
            parsed.get('ac_output_power_phase3', 0)
        )

        # Calculate self-consumption = Total PV Generation - Power Exported to Grid
        grid_power = parsed.get('grid_power_all', 0)
        exported_power = -grid_power if grid_power < 0 else 0
        total_pv_power = pv_dc_total + pv_ac_total
        parsed['self_consumption_power'] = total_pv_power - exported_power

        logging.debug(f'Final parsed data: {parsed}')
        return parsed


    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 60),
            ReadHoldingRegisters(1100, 60),
            ReadHoldingRegisters(1200, 100),
            ReadHoldingRegisters(1300, 40),
            ReadHoldingRegisters(1400, 60),
            ReadHoldingRegisters(1509, 30),
            ReadHoldingRegisters(2000, 60),
            ReadHoldingRegisters(2240, 20),
            ReadHoldingRegisters(2400, 40),
            ReadHoldingRegisters(12002, 16),
        ]

    @property
    def logging_commands(self) -> List[ReadHoldingRegisters]:
        return self.polling_commands
    
    def _combine_u32(self, parsed: Dict, name: str, low_name: str, high_name: str):
        low = parsed.get(low_name)
        high = parsed.get(high_name)
        if low is not None and high is not None:
            parsed[name] = (high << 16) | (low & 0xFFFF)

    def _combine_s32(self, parsed: Dict, name: str, low_name: str, high_name: str):
        low = parsed.get(low_name)
        high = parsed.get(high_name)
        if low is not None and high is not None:
            parsed[name] = _to_signed32_swapped(low, high)

    def _combine_and_scale_u32(self, parsed: Dict, name: str, low_name: str, high_name: str, scale: float):
        low = parsed.get(low_name)
        high = parsed.get(high_name)
        if low is not None and high is not None:
            val = (high << 16) | (low & 0xFFFF)
            parsed[name] = round(val * scale, 2)
