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
        self.struct.add_uint_field("ac_output_power_phase1", 1510)
        self.struct.add_uint_field("ac_output_power_phase2", 1517)
        self.struct.add_uint_field("ac_output_power_phase3", 1524)

        self.struct.add_decimal_field("ac_output_voltage_phase1", 1511, 1)
        self.struct.add_decimal_field("ac_output_voltage_phase2", 1518, 1)
        self.struct.add_decimal_field("ac_output_voltage_phase3", 1525, 1)

        self.struct.add_decimal_field("ac_output_current_phase1", 1512, 1)
        self.struct.add_decimal_field("ac_output_current_phase2", 1519, 1)
        self.struct.add_decimal_field("ac_output_current_phase3", 1526, 1)

        # house consumption
        self.struct.add_uint_field("consumption_power_phase1", 1430)
        self.struct.add_uint_field("consumption_power_phase2", 1436)
        self.struct.add_uint_field("consumption_power_phase3", 1442)

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
    # ---------- helpers (place near top of ep2000.py) ----------
def _to_signed16(v: int) -> int:
    return v - 65536 if v > 0x7FFF else v

def _to_signed32_swapped(low: int, high: int) -> int:
    # EP2000 uses low-word first, high-word second in many 32-bit totals
    val = (high << 16) | (low & 0xFFFF)
    return val - (1 << 32) if val & (1 << 31) else val

# ---------- inside class EP2000 (add these methods) ----------
def _read_field_safe(self, name: str):
    """Return raw value from DeviceStruct or None if missing."""
    try:
        return self.struct.get(name)
    except Exception:
        return None

def decode_phase_tuple(self, power_reg: int, voltage_reg: int, current_reg: int = None):
    """
    Decode a phase tuple where:
      - power_reg is a signed 16-bit raw W (or 32-bit if you change)
      - voltage_reg is stored as value/10
      - current_reg is optional (value/10)
    Returns dict with power_w, voltage_v, current_a (computed if missing).
    """
    p_raw = self.struct.get_uint(power_reg) if hasattr(self.struct, "get_uint") else None
    v_raw = self.struct.get_uint(voltage_reg) if hasattr(self.struct, "get_uint") else None

    if p_raw is None or v_raw is None:
        return None

    p = _to_signed16(p_raw)
    v = v_raw / 10.0
    i = None
    if current_reg is not None:
        cur_raw = self.struct.get_uint(current_reg)
        if cur_raw is not None:
            i = cur_raw / 10.0
    if i is None and v > 0:
        i = abs(p) / v
    return {"power_w": int(p), "voltage_v": round(v, 1), "current_a": round(i, 2) if i is not None else None}

def decode_grid_power32(self, low_reg: int, high_reg: int):
    """Decode a 32-bit swapped signed grid power value."""
    low = self.struct.get_uint(low_reg)
    high = self.struct.get_uint(high_reg)
    if low is None or high is None:
        return None
    return int(_to_signed32_swapped(low, high))

def decode_pv_strings(self):
    """Return pv1 and pv2 dicts using 1212/1213/1214 and 1220/1221/1222."""
    pv1 = self.decode_phase_tuple(1212, 1213, 1214)
    pv2 = self.decode_phase_tuple(1220, 1221, 1222)
    return {"pv1": pv1, "pv2": pv2}

def decode_inverter_phases(self):
    """Decode inverter AC output phases (signed power at 1510/1517/1524)."""
    l1 = self.decode_phase_tuple(1510, 1511, 1512)
    l2 = self.decode_phase_tuple(1517, 1518, 1519)
    l3 = self.decode_phase_tuple(1524, 1525, 1526)
    return {"inv_l1": l1, "inv_l2": l2, "inv_l3": l3}

def decode_adl400_ac(self):
    """
    ADL400 (AC-coupled) fields — these registers are firmware-dependent.
    Keep these entries; if discovery finds different offsets, update them.
    """
    # Phase1 candidate: power at 1228, voltage 1229 (/10), current 1230 (/10)
    p1 = None
    try:
        p1_raw = self.struct.get_uint(1228)
        v1_raw = self.struct.get_uint(1229)
        i1_raw = self.struct.get_uint(1230)
        if p1_raw is not None and v1_raw is not None and i1_raw is not None:
            p1 = {"power_w": _to_signed16(p1_raw), "voltage_v": v1_raw / 10.0, "current_a": i1_raw / 10.0}
    except Exception:
        p1 = None

    # Phase2 candidate: 1236/1237/1238
    p2 = None
    try:
        p2_raw = self.struct.get_uint(1236)
        v2_raw = self.struct.get_uint(1237)
        i2_raw = self.struct.get_uint(1238)
        if p2_raw is not None and v2_raw is not None and i2_raw is not None:
            p2 = {"power_w": _to_signed16(p2_raw), "voltage_v": v2_raw / 10.0, "current_a": i2_raw / 10.0}
    except Exception:
        p2 = None

    # Phase3 candidate: 1244/1245/1246
    p3 = None
    try:
        p3_raw = self.struct.get_uint(1244)
        v3_raw = self.struct.get_uint(1245)
        i3_raw = self.struct.get_uint(1246)
        if p3_raw is not None and v3_raw is not None and i3_raw is not None:
            p3 = {"power_w": _to_signed16(p3_raw), "voltage_v": v3_raw / 10.0, "current_a": i3_raw / 10.0}
    except Exception:
        p3 = None

    return {"pv_ac_l1": p1, "pv_ac_l2": p2, "pv_ac_l3": p3}

def compute_flows(self, decoded: dict):
    """
    Compute derived flows:
      - pv_dc_total_w
      - pv_ac_total_w
      - pv_total_w
      - inv_sum_w
      - grid_power_w
      - load_est_w (estimate)
      - self_consumption_w (estimate)
    Returns a dict of computed values.
    """
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

    # Convention: grid_power > 0 means importing from grid; negative means exporting.
    # load_est = inverter contribution + PV injected on AC bus - exported_to_grid
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
    """
    High-level decode entry: returns a dict with raw decoded tuples and computed flows.
    Use this in your publish path to create MQTT payloads.
    """
    decoded = {}
    decoded.update(self.decode_pv_strings())
    decoded.update(self.decode_inverter_phases())
    # grid 32-bit swapped at 1324/1325 (example)
    grid_p = self.decode_grid_power32(1324, 1325)
    decoded["grid_power_w"] = grid_p if grid_p is not None else 0
    decoded.update(self.decode_adl400_ac())
    computed = self.compute_flows(decoded)
    decoded.update(computed)
    return decoded


    
    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        return [
            ReadHoldingRegisters(100, 40),     # battery + identity + model_code
            ReadHoldingRegisters(1200, 100),   # PV status, MPPT, ADL400 candidate area (1200-1299)
            ReadHoldingRegisters(1300, 40),    # grid + phase data (1300-1339)
            ReadHoldingRegisters(1400, 60),    # counters / load area (1400-1459)
            ReadHoldingRegisters(1509, 30),    # inverter AC output phases (1509-1538)
            ReadHoldingRegisters(2000, 60),    # settings / user controls
            ReadHoldingRegisters(2240, 20),    # generator control region
            ReadHoldingRegisters(2400, 40),    # grid limits region
            ReadHoldingRegisters(12002, 16),   # WiFi name
        ]


    @property
    def logging_commands(self) -> List[ReadHoldingRegisters]:
        return self.polling_commands
