from typing import List
from ..commands import ReadHoldingRegisters
from .bluetti_device import BluettiDevice
from .struct import DeviceStruct
# ---------- inside class EP2000 ----------

def _get_uint(self, name_or_addr):
    """Return uint value by field name or address. Prefer name if present."""
    # DeviceStruct.parse returns dict keyed by name; your code likely stores parsed results
    # Replace `self.parsed` with the actual dict your code uses after parse.
    parsed = getattr(self, "parsed", None)
    if parsed is None:
        return None
    if isinstance(name_or_addr, str):
        return parsed.get(name_or_addr)
    return None

def _read_raw(self, addr_name):
    """Helper wrapper to read parsed value by field name."""
    return self._get_uint(addr_name)

def decode_pv_strings(self, parsed):
    pv1_p = parsed.get("pv1_power_w")
    pv1_v = parsed.get("pv1_voltage_v")
    pv1_i = parsed.get("pv1_current_a")
    pv2_p = parsed.get("pv2_power_w")
    pv2_v = parsed.get("pv2_voltage_v")
    pv2_i = parsed.get("pv2_current_a")
    return {
        "pv1": {"power_w": signed16(pv1_p) if pv1_p is not None else None,
                "voltage_v": float(pv1_v) if pv1_v is not None else None,
                "current_a": float(pv1_i) if pv1_i is not None else None},
        "pv2": {"power_w": signed16(pv2_p) if pv2_p is not None else None,
                "voltage_v": float(pv2_v) if pv2_v is not None else None,
                "current_a": float(pv2_i) if pv2_i is not None else None},
    }

def decode_inverter_phases(self, parsed):
    def phase(prefix, p_reg, v_reg, i_reg):
        p_raw = parsed.get(p_reg)
        v = parsed.get(v_reg)
        i_raw = parsed.get(i_reg)
        p = signed16(p_raw) if p_raw is not None else None
        v_f = float(v) if v is not None else None
        i_f = float(i_raw) if i_raw is not None else None
        if i_f is None and p is not None and v_f:
            i_f = safe_div(abs(p), v_f)
        return {"power_w": p, "voltage_v": v_f, "current_a": round(i_f, 2) if i_f is not None else None}
    return {
        "inv_l1": phase("l1", "ac_output_power_phase1_raw", "ac_output_voltage_phase1_v", "ac_output_current_phase1_a"),
        "inv_l2": phase("l2", "ac_output_power_phase2_raw", "ac_output_voltage_phase2_v", "ac_output_current_phase2_a"),
        "inv_l3": phase("l3", "ac_output_power_phase3_raw", "ac_output_voltage_phase3_v", "ac_output_current_phase3_a"),
    }

def decode_adl400_candidates(self, parsed):
    def ac_phase(prefix, p_raw_name, v_name, i_name):
        p_raw = parsed.get(p_raw_name)
        v = parsed.get(v_name)
        i = parsed.get(i_name)
        if p_raw is None and v is None and i is None:
            return None
        p = signed16(p_raw) if p_raw is not None else None
        v_f = float(v) if v is not None else None
        i_f = float(i) if i is not None else None
        return {"power_w": p, "voltage_v": v_f, "current_a": i_f}
    return {
        "pv_ac_l1": ac_phase("l1", "pv_ac_l1_power_raw", "pv_ac_l1_voltage_v", "pv_ac_l1_current_a"),
        "pv_ac_l2": ac_phase("l2", "pv_ac_l2_power_raw", "pv_ac_l2_voltage_v", "pv_ac_l2_current_a"),
        "pv_ac_l3": ac_phase("l3", "pv_ac_l3_power_raw", "pv_ac_l3_voltage_v", "pv_ac_l3_current_a"),
    }

def decode_grid(self, parsed):
    # grid_power_all_low/high were registered at 144/145 etc; combine here
    gp_low = parsed.get("grid_power_all_low")
    gp_high = parsed.get("grid_power_all_high")
    grid_total = combine_32_swapped(gp_low, gp_high)
    # per-phase grid raw values
    g1 = signed16(parsed.get("grid_power_phase1_raw")) if parsed.get("grid_power_phase1_raw") is not None else None
    g2 = signed16(parsed.get("grid_power_phase2_raw")) if parsed.get("grid_power_phase2_raw") is not None else None
    g3 = signed16(parsed.get("grid_power_phase3_raw")) if parsed.get("grid_power_phase3_raw") is not None else None
    return {"grid_power_w": grid_total, "grid_phase1_w": g1, "grid_phase2_w": g2, "grid_phase3_w": g3}

def compute_flows_from_parsed(self, parsed):
    decoded = {}
    decoded.update(self.decode_pv_strings(parsed))
    decoded.update(self.decode_inverter_phases(parsed))
    decoded.update(self.decode_adl400_candidates(parsed))
    grid = self.decode_grid(parsed)
    decoded["grid_power_w"] = grid.get("grid_power_w") or 0

    # totals
    pv_dc_total = 0
    for k in ("pv1", "pv2"):
        p = decoded.get(k, {}).get("power_w")
        if p is not None:
            pv_dc_total += p

    pv_ac_total = 0
    for k in ("pv_ac_l1", "pv_ac_l2", "pv_ac_l3"):
        p = decoded.get(k, {}).get("power_w")
        if p is not None:
            pv_ac_total += p

    inv_sum = 0
    for k in ("inv_l1", "inv_l2", "inv_l3"):
        p = decoded.get(k, {}).get("power_w")
        if p is not None:
            inv_sum += p

    pv_total = pv_dc_total + pv_ac_total
    grid_power = decoded.get("grid_power_w", 0) or 0

    # Convention: grid_power > 0 = import, grid_power < 0 = export
    exported = -grid_power if grid_power < 0 else 0
    load_est = inv_sum + pv_ac_total + pv_dc_total - exported
    self_consumption = pv_total - exported

    # attach computed values
    decoded.update({
        "pv_dc_total_w": int(pv_dc_total),
        "pv_ac_total_w": int(pv_ac_total),
        "pv_total_w": int(pv_total),
        "inv_sum_w": int(inv_sum),
        "grid_power_w": int(grid_power),
        "load_est_w": int(load_est),
        "self_consumption_w": int(self_consumption),
        "exported_w": int(exported),
    })
    return decoded



class EP2000(BluettiDevice):
    def __init__(self, address: str, sn: str):
        self.struct = DeviceStruct()

        # --- Identity / battery ---
        self.struct.add_uint_field("battery_soc", 100)                       # raw % (uint16)
        self.struct.add_decimal_field("battery_power_w_raw", 101, 0)         # raw W (uint16) — signed interpretation in decode
        self.struct.add_uint_field("total_battery_percent", 102)             # duplicate SOC if needed

        self.struct.add_swap_string_field("device_type", 110, 6)
        self.struct.add_sn_field("serial_number", 116)
        self.struct.add_swap_string_field("model_code", 1101, 6)

        # --- Totals (32-bit swapped) ---
        # DeviceStruct does not have a 32-bit field helper, so store low/high words and combine later.
        self.struct.add_uint_field("pv_input_power_all_low", 144)            # low word of 32-bit swapped Total PV power
        self.struct.add_uint_field("pv_input_power_all_high", 145)           # high word

        self.struct.add_uint_field("consumption_power_all_low", 142)         # low word of 32-bit swapped Total AC load
        self.struct.add_uint_field("consumption_power_all_high", 143)        # high word

        self.struct.add_uint_field("grid_power_all_low", 146)                # low word of 32-bit swapped signed Grid power
        self.struct.add_uint_field("grid_power_all_high", 147)               # high word

        # --- Energy statistics (32-bit swapped, scaled) ---
        self.struct.add_uint_field("total_ac_consumption_low", 152)          # low word (32-bit swapped; /10 => kWh)
        self.struct.add_uint_field("total_ac_consumption_high", 153)         # high word

        self.struct.add_uint_field("total_grid_consumption_low", 156)        # low word (32-bit swapped; /10 => kWh)
        self.struct.add_uint_field("total_grid_consumption_high", 157)       # high word

        self.struct.add_uint_field("total_grid_feed_low", 158)               # low word (32-bit swapped; /10 => kWh)
        self.struct.add_uint_field("total_grid_feed_high", 159)              # high word

        # --- PV DC strings (confirmed) ---
        self.struct.add_uint_field("pv1_power_w", 1212)                      # raw W (uint16) — signed interpretation if needed
        self.struct.add_decimal_field("pv1_voltage_v", 1213, 1)             # /10 V
        self.struct.add_decimal_field("pv1_current_a", 1214, 1)             # /10 A

        self.struct.add_uint_field("pv2_power_w", 1220)                      # raw W (uint16)
        self.struct.add_decimal_field("pv2_voltage_v", 1221, 1)             # /10 V
        self.struct.add_decimal_field("pv2_current_a", 1222, 1)             # /10 A

        # --- ADL400 AC‑coupled PV (candidate offsets) ---
        # Keep these candidate offsets; discovery may change them. Power is signed 16-bit in practice.
        self.struct.add_uint_field("pv_ac_l1_power_raw", 1228)               # candidate: ADL400 L1 power (uint16; signed in decode)
        self.struct.add_decimal_field("pv_ac_l1_voltage_v", 1229, 1)        # candidate: /10 V
        self.struct.add_decimal_field("pv_ac_l1_current_a", 1230, 1)        # candidate: /10 A

        self.struct.add_uint_field("pv_ac_l2_power_raw", 1236)               # candidate: ADL400 L2 power
        self.struct.add_decimal_field("pv_ac_l2_voltage_v", 1237, 1)        # candidate: /10 V
        self.struct.add_decimal_field("pv_ac_l2_current_a", 1238, 1)        # candidate: /10 A

        self.struct.add_uint_field("pv_ac_l3_power_raw", 1244)               # candidate: ADL400 L3 power
        self.struct.add_decimal_field("pv_ac_l3_voltage_v", 1245, 1)        # candidate: /10 V
        self.struct.add_decimal_field("pv_ac_l3_current_a", 1246, 1)        # candidate: /10 A

        # --- Grid data (phase grid values) ---
        self.struct.add_decimal_field("grid_frequency_hz", 1300, 1)         # /10 Hz

        # Phase 1 grid
        self.struct.add_uint_field("grid_power_phase1_raw", 1313)           # raw W (uint16) — signed in decode if needed
        self.struct.add_decimal_field("grid_voltage_phase1_v", 1314, 1)     # /10 V
        self.struct.add_decimal_field("grid_current_phase1_a", 1315, 1)     # /10 A

        # Phase 2 grid
        self.struct.add_uint_field("grid_power_phase2_raw", 1319)           # raw W
        self.struct.add_decimal_field("grid_voltage_phase2_v", 1320, 1)     # /10 V
        self.struct.add_decimal_field("grid_current_phase2_a", 1321, 1)     # /10 A

        # Phase 3 grid
        self.struct.add_uint_field("grid_power_phase3_raw", 1325)           # raw W
        self.struct.add_decimal_field("grid_voltage_phase3_v", 1326, 1)     # /10 V
        self.struct.add_decimal_field("grid_current_phase3_a", 1327, 1)     # /10 A

        # --- AC output / inverter phases (inverter contribution to AC bus) ---
        self.struct.add_uint_field("ac_output_power_phase1_raw", 1510)      # raw W (uint16) — signed in decode
        self.struct.add_decimal_field("ac_output_voltage_phase1_v", 1511, 1)# /10 V
        self.struct.add_decimal_field("ac_output_current_phase1_a", 1512, 1)# /10 A (if present)

        self.struct.add_uint_field("ac_output_power_phase2_raw", 1517)      # raw W
        self.struct.add_decimal_field("ac_output_voltage_phase2_v", 1518, 1)# /10 V
        self.struct.add_decimal_field("ac_output_current_phase2_a", 1519, 1)# /10 A

        self.struct.add_uint_field("ac_output_power_phase3_raw", 1524)      # raw W
        self.struct.add_decimal_field("ac_output_voltage_phase3_v", 1525, 1)# /10 V
        self.struct.add_decimal_field("ac_output_current_phase3_a", 1526, 1)# /10 A

        # --- House consumption (per-phase load) ---
        self.struct.add_uint_field("consumption_power_phase1_raw", 1430)    # raw W (uint16) — signed in decode if negative export possible
        self.struct.add_decimal_field("consumption_voltage_phase1_v", 1431, 1)
        self.struct.add_decimal_field("consumption_current_phase1_a", 1432, 1)

        self.struct.add_uint_field("consumption_power_phase2_raw", 1436)    # raw W
        self.struct.add_decimal_field("consumption_voltage_phase2_v", 1437, 1)
        self.struct.add_decimal_field("consumption_current_phase2_a", 1438, 1)

        self.struct.add_uint_field("consumption_power_phase3_raw", 1442)    # raw W
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

        # --- Notes: computed fields are not added to DeviceStruct here.
        # Compute these in your decode/publish path after parsing the registers:
        #  - pv_dc_total_w  = pv1_power_w + pv2_power_w
        #  - pv_ac_total_w  = combine pv_ac_l?_power_raw (apply signed)
        #  - pv_total_w     = pv_dc_total_w + pv_ac_total_w
        #  - inv_sum_w      = sum of ac_output_power_phase?_raw (apply signed)
        #  - grid_power_all = combine grid_power_all_low/high (32-bit swapped signed)
        #  - total_* energy values = combine low/high and divide by 10 where applicable
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


    
    def parse(self, address: int, data: bytes):
        # Let the struct parse all the raw values
        parsed = self.struct.parse(address, data)

        # Manually combine 32-bit unsigned energy values
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

        # Get all the fancy flow values
        flows = self.decode_flows()

        # Flatten PV strings
        if flows.get('pv1'):
            parsed['pv1_power'] = flows['pv1'].get('power_w')
            parsed['pv1_voltage'] = flows['pv1'].get('voltage_v')
            parsed['pv1_current'] = flows['pv1'].get('current_a')
        if flows.get('pv2'):
            parsed['pv2_power'] = flows['pv2'].get('power_w')
            parsed['pv2_voltage'] = flows['pv2'].get('voltage_v')
            parsed['pv2_current'] = flows['pv2'].get('current_a')

        # Flatten inverter phases
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
            
        # Flatten ADL400 AC PV
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

        # Add computed flow values, renaming where necessary
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

    def _combine_u32_swapped(self, low: int, high: int):
        if low is None or high is None:
            return None
        return (high << 16) | (low & 0xFFFF)


    
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
