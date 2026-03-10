from typing import List
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
    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # --- Identity / battery ---
        self.struct.add_uint_field("battery_soc", 100)
        self.struct.add_decimal_field("battery_power_w_raw", 101, 0)
        self.struct.add_uint_field("total_battery_percent", 102)

        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)
        self.struct.add_swap_string_field("model_code", 1101, 6)

        # --- Totals (32-bit swapped) ---
        self.struct.add_uint_field("pv_input_power_all_low", 144)
        self.struct.add_uint_field("pv_input_power_all_high", 145)

        self.struct.add_uint_field("consumption_power_all_low", 142)
        self.struct.add_uint_field("consumption_power_all_high", 143)

        self.struct.add_uint_field("grid_power_all_low", 146)
        self.struct.add_uint_field("grid_power_all_high", 147)

        # --- Energy statistics (32-bit swapped, scaled) ---
        self.struct.add_uint_field("total_ac_consumption_low", 152)
        self.struct.add_uint_field("total_ac_consumption_high", 153)

        self.struct.add_uint_field("total_grid_consumption_low", 156)
        self.struct.add_uint_field("total_grid_consumption_high", 157)

        self.struct.add_uint_field("total_grid_feed_low", 158)
        self.struct.add_uint_field("total_grid_feed_high", 159)

        # --- PV DC strings (confirmed) ---
        self.struct.add_uint_field("pv1_power_w", 1212)
        self.struct.add_decimal_field("pv1_voltage_v", 1213, 1)
        self.struct.add_decimal_field("pv1_current_a", 1214, 1)

        self.struct.add_uint_field("pv2_power_w", 1220)
        self.struct.add_decimal_field("pv2_voltage_v", 1221, 1)
        self.struct.add_decimal_field("pv2_current_a", 1222, 1)

        # --- ADL400 AC‑coupled PV (candidate offsets) ---
        self.struct.add_uint_field("pv_ac_l1_power_raw", 1228)
        self.struct.add_decimal_field("pv_ac_l1_voltage_v", 1229, 1)
        self.struct.add_decimal_field("pv_ac_l1_current_a", 1230, 1)

        self.struct.add_uint_field("pv_ac_l2_power_raw", 1236)
        self.struct.add_decimal_field("pv_ac_l2_voltage_v", 1237, 1)
        self.struct.add_decimal_field("pv_ac_l2_current_a", 1238, 1)

        self.struct.add_uint_field("pv_ac_l3_power_raw", 1244)
        self.struct.add_decimal_field("pv_ac_l3_voltage_v", 1245, 1)
        self.struct.add_decimal_field("pv_ac_l3_current_a", 1246, 1)

        # --- Grid data (phase grid values) ---
        self.struct.add_decimal_field("grid_frequency_hz", 1300, 1)

        self.struct.add_uint_field("grid_power_phase1_raw", 1313)
        self.struct.add_decimal_field("grid_voltage_phase1_v", 1314, 1)
        self.struct.add_decimal_field("grid_current_phase1_a", 1315, 1)

        self.struct.add_uint_field("grid_power_phase2_raw", 1319)
        self.struct.add_decimal_field("grid_voltage_phase2_v", 1320, 1)
        self.struct.add_decimal_field("grid_current_phase2_a", 1321, 1)

        self.struct.add_uint_field("grid_power_phase3_raw", 1325)
        self.struct.add_decimal_field("grid_voltage_phase3_v", 1326, 1)
        self.struct.add_decimal_field("grid_current_phase3_a", 1327, 1)

        # --- AC output / inverter phases (inverter contribution to AC bus) ---
        self.struct.add_uint_field("ac_output_power_phase1_raw", 1510)
        self.struct.add_decimal_field("ac_output_voltage_phase1_v", 1511, 1)
        self.struct.add_decimal_field("ac_output_current_phase1_a", 1512, 1)

        self.struct.add_uint_field("ac_output_power_phase2_raw", 1517)
        self.struct.add_decimal_field("ac_output_voltage_phase2_v", 1518, 1)
        self.struct.add_decimal_field("ac_output_current_phase2_a", 1519, 1)

        self.struct.add_uint_field("ac_output_power_phase3_raw", 1524)
        self.struct.add_decimal_field("ac_output_voltage_phase3_v", 1525, 1)
        self.struct.add_decimal_field("ac_output_current_phase3_a", 1526, 1)

        # --- House consumption (per-phase load) ---
        self.struct.add_uint_field("consumption_power_phase1_raw", 1430)
        self.struct.add_decimal_field("consumption_voltage_phase1_v", 1431, 1)
        self.struct.add_decimal_field("consumption_current_phase1_a", 1432, 1)

        self.struct.add_uint_field("consumption_power_phase2_raw", 1436)
        self.struct.add_decimal_field("consumption_voltage_phase2_v", 1437, 1)
        self.struct.add_decimal_field("consumption_current_phase2_a", 1438, 1)

        self.struct.add_uint_field("consumption_power_phase3_raw", 1442)
        self.struct.add_decimal_field("consumption_voltage_phase3_v", 1443, 1)
        self.struct.add_decimal_field("consumption_current_phase3_a", 1444, 1)

        # --- Controls / battery range ---
        self.struct.add_bool_field("ac_control_enabled", 2011)
        self.struct.add_uint_field("battery_range_start", 2022)
        self.struct.add_uint_field("battery_range_end", 2023)
        self.struct.add_bool_field("generator_control_enabled", 2246)

        # --- Grid limits (decimal scaling) ---
        self.struct.add_decimal_field("grid_reconnect_voltage_low_limit_v", 2435, 1)
        self.struct.add_decimal_field("grid_reconnect_voltage_high_limit_v", 2436, 1)
        self.struct.add_decimal_field("grid_reconnect_frequency_low_limit_hz", 2437, 2)
        self.struct.add_decimal_field("grid_reconnect_frequency_high_limit_hz", 2438, 2)

        # --- WiFi name ---
        self.struct.add_swap_string_field("wifi_name", 12002, 16)

        super().__init__(address, "EP2000", sn)

    def _read_field_safe(self, name: str):
        try:
            return self.struct.get(name)
        except Exception:
            return None

    def _combine_u32_swapped(self, low: int, high: int):
        if low is None or high is None:
            return None
        return (high << 16) | (low & 0xFFFF)
    
    def decode_phase_tuple(self, power_reg: int, voltage_reg: int, current_reg: int = None):
        p_raw = self.struct.get(power_reg)
        v_raw = self.struct.get(voltage_reg)

        if p_raw is None or v_raw is None:
            return None

        p = _to_signed16(p_raw)
        v = v_raw
        i = None
        if current_reg is not None:
            cur_raw = self.struct.get(current_reg)
            if cur_raw is not None:
                i = cur_raw
        if i is None and v > 0:
            i = abs(p) / v
        return {"power_w": int(p), "voltage_v": round(v, 1), "current_a": round(i, 2) if i is not None else None}

    def decode_grid_power32(self, low_reg: int, high_reg: int):
        low = self.struct.get(low_reg)
        high = self.struct.get(high_reg)
        if low is None or high is None:
            return None
        return int(_to_signed32_swapped(low, high))

    def decode_pv_strings(self):
        pv1 = self.decode_phase_tuple(1212, 1213, 1214)
        pv2 = self.decode_phase_tuple(1220, 1221, 1222)
        return {"pv1": pv1, "pv2": pv2}

    def decode_inverter_phases(self):
        l1 = self.decode_phase_tuple(1510, 1511, 1512)
        l2 = self.decode_phase_tuple(1517, 1518, 1519)
        l3 = self.decode_phase_tuple(1524, 1525, 1526)
        return {"inv_l1": l1, "inv_l2": l2, "inv_l3": l3}

    def decode_adl400_ac(self):
        p1, p2, p3 = None, None, None
        try:
            p1_raw = self.struct.get('pv_ac_l1_power_raw')
            v1_raw = self.struct.get('pv_ac_l1_voltage_v')
            i1_raw = self.struct.get('pv_ac_l1_current_a')
            if p1_raw is not None and v1_raw is not None and i1_raw is not None:
                p1 = {"power_w": _to_signed16(p1_raw), "voltage_v": v1_raw, "current_a": i1_raw}
        except KeyError:
            pass

        try:
            p2_raw = self.struct.get('pv_ac_l2_power_raw')
            v2_raw = self.struct.get('pv_ac_l2_voltage_v')
            i2_raw = self.struct.get('pv_ac_l2_current_a')
            if p2_raw is not None and v2_raw is not None and i2_raw is not None:
                p2 = {"power_w": _to_signed16(p2_raw), "voltage_v": v2_raw, "current_a": i2_raw}
        except KeyError:
            pass

        try:
            p3_raw = self.struct.get('pv_ac_l3_power_raw')
            v3_raw = self.struct.get('pv_ac_l3_voltage_v')
            i3_raw = self.struct.get('pv_ac_l3_current_a')
            if p3_raw is not None and v3_raw is not None and i3_raw is not None:
                p3 = {"power_w": _to_signed16(p3_raw), "voltage_v": v3_raw, "current_a": i3_raw}
        except KeyError:
            pass
            
        return {"pv_ac_l1": p1, "pv_ac_l2": p2, "pv_ac_l3": p3}

    def compute_flows(self, decoded: dict):
        pv_dc_total = 0
        for p in ("pv1", "pv2"):
            tup = decoded.get(p)
            if tup and tup.get("power_w") is not None:
                pv_dc_total += tup["power_w"]

        pv_ac_total = 0
        for k in ("pv_ac_l1", "pv_ac_l2", "pv_ac_l3"):
            tup = decoded.get(k)
            if tup and tup.get("power_w") is not None:
                pv_ac_total += tup["power_w"]

        inv_sum = 0
        for k in ("inv_l1", "inv_l2", "inv_l3"):
            tup = decoded.get(k)
            if tup and tup.get("power_w") is not None:
                inv_sum += tup["power_w"]

        grid_power = decoded.get("grid_power_w", 0)

        pv_total = pv_dc_total + pv_ac_total

        exported = -grid_power if grid_power < 0 else 0
        load_est = inv_sum + pv_ac_total + pv_dc_total - exported

        self_consumption = pv_total - exported

        return {
            "pv_dc_total_w": int(pv_dc_total),
            "pv_ac_total_w": int(pv_ac_total),
            "pv_total_w": int(pv_total),
            "inv_sum_w": int(inv_sum),
            "grid_power_w": int(grid_power),
            "load_est_w": int(load_est),
            "self_consumption_w": int(self_consumption),
            "exported_w": int(exported),
        }

    def decode_flows(self):
        decoded = {}
        decoded.update(self.decode_pv_strings())
        decoded.update(self.decode_inverter_phases())
        grid_p = self.decode_grid_power32(146, 147) # grid_power_all_low, grid_power_all_high
        decoded["grid_power_w"] = grid_p if grid_p is not None else 0
        decoded.update(self.decode_adl400_ac())
        computed = self.compute_flows(decoded)
        decoded.update(computed)
        return decoded
    
    def parse(self, address: int, data: bytes):
        parsed = self.struct.parse(address, data)

        total_ac_consumption_kwh = self._combine_u32_swapped(
            parsed.get('total_ac_consumption_low'),
            parsed.get('total_ac_consumption_high')
        )
        if total_ac_consumption_kwh is not None:
            parsed['total_ac_consumption'] = round(total_ac_consumption_kwh / 10.0, 2)

        total_grid_consumption_kwh = self._combine_u32_swapped(
            parsed.get('total_grid_consumption_low'),
            parsed.get('total_grid_consumption_high')
        )
        if total_grid_consumption_kwh is not None:
            parsed['total_grid_consumption'] = round(total_grid_consumption_kwh / 10.0, 2)
        
        total_grid_feed_kwh = self._combine_u32_swapped(
            parsed.get('total_grid_feed_low'),
            parsed.get('total_grid_feed_high')
        )
        if total_grid_feed_kwh is not None:
            parsed['total_grid_feed'] = round(total_grid_feed_kwh / 10.0, 2)

        flows = self.decode_flows()

        if flows.get('pv1'):
            parsed['pv1_power'] = flows['pv1'].get('power_w')
            parsed['pv1_voltage'] = flows['pv1'].get('voltage_v')
            parsed['pv1_current'] = flows['pv1'].get('current_a')
        if flows.get('pv2'):
            parsed['pv2_power'] = flows['pv2'].get('power_w')
            parsed['pv2_voltage'] = flows['pv2'].get('voltage_v')
            parsed['pv2_current'] = flows['pv2'].get('current_a')

        if flows.get('inv_l1'):
            parsed['ac_output_power_phase1'] = flows['inv_l1'].get('power_w')
            parsed['ac_output_voltage_phase1'] = flows['inv_l1'].get('voltage_v')
            parsed['ac_output_current_phase1'] = flows['inv_l1'].get('current_a')
        if flows.get('inv_l2'):
            parsed['ac_output_power_phase2'] = flows['inv_l2'].get('power_w')
            parsed['ac_output_voltage_phase2'] = flows['inv_l2'].get('voltage_v')
            parsed['ac_output_current_phase2'] = flows['inv_l2'].get('current_a')
        if flows.get('inv_l3'):
            parsed['ac_output_power_phase3'] = flows['inv_l3'].get('power_w')
            parsed['ac_output_voltage_phase3'] = flows['inv_l3'].get('voltage_v')
            parsed['ac_output_current_phase3'] = flows['inv_l3'].get('current_a')
            
        if flows.get('pv_ac_l1'):
            parsed['adl400_ac_input_power_phase1'] = flows['pv_ac_l1'].get('power_w')
            parsed['adl400_ac_input_voltage_phase1'] = flows['pv_ac_l1'].get('voltage_v')
            parsed['adl400_ac_input_current_phase1'] = flows['pv_ac_l1'].get('current_a')
        if flows.get('pv_ac_l2'):
            parsed['adl400_ac_input_power_phase2'] = flows['pv_ac_l2'].get('power_w')
            parsed['adl400_ac_input_voltage_phase2'] = flows['pv_ac_l2'].get('voltage_v')
            parsed['adl400_ac_input_current_phase2'] = flows['pv_ac_l2'].get('current_a')
        if flows.get('pv_ac_l3'):
            parsed['adl400_ac_input_power_phase3'] = flows['pv_ac_l3'].get('power_w')
            parsed['adl400_ac_input_voltage_phase3'] = flows['pv_ac_l3'].get('voltage_v')
            parsed['adl400_ac_input_current_phase3'] = flows['pv_ac_l3'].get('current_a')

        if flows.get('pv_total_w') is not None:
            parsed['pv_input_power_all'] = flows['pv_total_w']
        if flows.get('grid_power_w') is not None:
            parsed['grid_power_all'] = flows['grid_power_w']
        if flows.get('load_est_w') is not None:
            parsed['consumption_power_all'] = flows['load_est_w']
        if flows.get('pv_dc_total_w') is not None:
            parsed['pv_dc_total_power'] = flows['pv_dc_total_w']
        if flows.get('pv_ac_total_w') is not None:
            parsed['pv_ac_total_power'] = flows['pv_ac_total_w']
        if flows.get('inv_sum_w') is not None:
            parsed['inverter_sum_power'] = flows['inv_sum_w']
        if flows.get('self_consumption_w') is not None:
            parsed['self_consumption_power'] = flows['self_consumption_w']
        if flows.get('exported_w') is not None:
            parsed['exported_power'] = flows['exported_w']

        return parsed
    
    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 40),
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
