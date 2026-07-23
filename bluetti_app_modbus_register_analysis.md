# Bluetti EP2000 Modbus Protocol Analysis

## Overview

This document provides a comprehensive analysis of the Bluetti EP2000 communication protocol based on reverse engineering of the Android app version 3.0.6.

**Protocol Version:** 2.0+ (ProtocolAddrV2)  
**App Version:** 3.0.6  
**Analysis Date:** 2025-03-27

## Protocol Structure

The EP2000 uses a **custom encrypted protocol** that wraps Modbus-style operations with AES-CBC encryption and a custom CRC checksum.

### Command Types

| Code | Name | Description | Modbus Equivalent |
|------|------|-------------|-------------------|
| 0x17 | P0x17 | Read multiple registers | 0x03 (Read Holding Registers) |
| 0x18 | P0x18 | Write single register | 0x06 (Write Single Register) |
| 0x19 | P0x19 | Write multiple registers | 0x10 (Write Multiple Registers) |

### Security

- **Encryption:** AES-CBC with PKCS7 padding
- **Default Key:** `sxd_aiot_key_001`
- **Default IV:** `sxd_aiot_2022_01`
- **Key/IV:** Can be customized via parameters
- **Checksum:** Custom CRC using lookup tables (b/c.java)

### Packet Format

```
[Header: 10 bytes] [Encrypted Payload] [CRC: 2 bytes]
```

- Header contains protocol identifier, length, and control information
- Payload contains the actual Modbus-style frame (slave addr, function code, data)
- CRC is calculated over the entire packet (excluding CRC itself)

## Modbus Register Map

All register addresses are in **decimal** notation.

### System Control Registers (2000-2100)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 2001 | SYSTEM_TIME | uint32 | R/W | System time (Unix timestamp) |
| 2004 | SYSTEM_TIME_ZONE | int16 | R/W | Time zone offset (hours) |
| 2005 | WORKING_MODE | uint16 | R/W | Working mode: 0=SBU, 1=APL, 2=UPS, 3=PV priority |
| 2006 | CTRL_EVENT | bitmask | R/W | Control event flags (bitfield) |
| 2007 | CTRL_LED | uint16 | R/W | LED control brightness/behavior |
| 2008 | CTRL_METER | uint16 | R/W | Meter control configuration |
| 2010 | CTRL_INVERTER | uint16 | R/W | Inverter on/off (0=off, 1=on) |
| 2011 | AC_SWITCH | uint16 | R/W | AC output switch (0=off, 1=on) |
| 2012 | DC_SWITCH | uint16 | R/W | DC output switch |
| 2013 | SYSTEM_POWER_OFF | uint16 | R/W | System power off command |
| 2014 | CTRL_DC_ECO_MODE | uint16 | R/W | DC ECO mode: 0=normal, 1=eco |
| 2015 | DC_ECO_AUTO_OFF_TIME | uint16 | R/W | DC ECO auto off time (minutes) |
| 2016 | DC_ECO_POWER | uint16 | R/W | DC ECO power threshold (Watts) |
| 2017 | CTRL_AC_ECO_MODE | uint16 | R/W | AC ECO mode control |
| 2018 | AC_ECO_AUTO_OFF_TIME | uint16 | R/W | AC ECO auto off time (minutes) |
| 2019 | AC_ECO_POWER | uint16 | R/W | AC ECO power threshold (Watts) |
| 2020 | CHARGING_MODE | uint16 | R/W | Charging mode: 0=normal, 1=fast, 2=timed |
| 2021 | CTRL_SUPER_POWER_MODE | uint16 | R/W | Super power mode (0=off, 1=on) |
| 2022 | SYS_SOC_LOW_CAPACITY | uint16 | R/W | System low SOC capacity (%) |
| 2023 | SYS_SOC_HIGH_CAPACITY | uint16 | R/W | System high SOC capacity (%) |
| 2027 | SET_CURR_ENERGY_TYPE | uint16 | R/W | Set current energy type |
| 2030 | WORKING_TIME_START | uint16[] | R/W | Working time schedule (multiple registers) |
| 2075 | SOC_SET_LOW | uint16 | R/W | Low SOC setting (0-100%) |
| 2083 | SOC_SET_HIGH | uint16 | R/W | High SOC setting (0-100%) |

### Inverter Registers (1100-1700)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 1100 | INV_BASE_INFO | struct | R | Inverter base information (model, firmware, etc.) |
| 1200 | INV_PV_INFO | struct | R | PV input voltage, current, power |
| 1300 | INV_GRID_INFO | struct | R | Grid voltage, frequency, status |
| 1400 | INV_LOAD_INFO | struct | R | Load voltage, current, power |
| 1700 | INV_METER_INFO | struct | R | Meter readings (kWh, etc.) |
| 2200 | INV_ADVANCE_SETTINGS | struct | R/W | Advanced inverter settings |
| 3500 | INV_TOTAL_ENERGY_INFO | struct | R | Total energy statistics |

### Battery/Pack Registers (6000-7200)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 6000 | PACK_MAIN_INFO | struct | R | Pack main info (voltage, SOC, status) |
| 6100 | PACK_ITEM_INFO | struct[] | R | Individual pack item details |
| 6300 | PACK_SUB_PACK_INFO | struct | R | Sub-pack information |
| 7200 | PACK_BMU_INFO | struct[] | R | Battery Management Unit data |

### Grid Settings (2207-2218)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 2207 | CTRL_GRID | uint16 | R/W | Grid enable/disable (0=disable, 1=enable) |
| 2208 | CTRL_FEED | uint16 | R/W | Feed to grid enable/disable |
| 2213 | GRID_MAX_POWER | uint16 | R/W | Grid max input power (Watts) |
| 2214 | GRID_MAX_CURRENT | uint16 | R/W | Grid max current (Amps) |
| 2215 | FEED_MAX_POWER | uint16 | R/W | Feed max power (Watts) |
| 2216 | FEED_MAX_CURRENT | uint16 | R/W | Feed max current (Amps) |
| 2217 | GRID_OFF_AC_PV_POWER | uint16 | R/W | Grid off AC PV power limit |
| 2218 | USER_REGION_SETTING | uint16 | R/W | User region/country code |

### Advanced Settings (2200-2500)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 2206 | SYSTEM_FACTORY_RESET | uint16 | W | Factory reset command |
| 2231 | ADV_SETTINGS_CT_TEST | uint16 | R/W | CT test settings |
| 2241 | EMS_CTRL_MODE_SET | uint16 | R/W | EMS control mode |
| 2242 | ADV_SETTINGS_OTHER_2 | bitmask | R/W | Additional advanced settings |
| 2245 | ADV_AC_CT_TEST | uint16 | R/W | AC CT test |
| 2269 | ADV_PV_SET | uint16 | R/W | PV input configuration |
| 2271 | DC_OUTPUT_VOLT_LEVEL | uint16 | R/W | DC output voltage level: 0=Auto, 1=9V, 2=12V, 3=24V, 4=48V |
| 2280 | HEAT_PUMP_ENABLE | bitmask | R/W | Heat pump enable flags |
| 2500 | MICRO_INV_ADV_SETTINGS | struct | R/W | Micro inverter advanced settings |

### IOT/Communication (11000-13776)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 11000 | IOT_BASE_INFO | struct | R | IOT module base information |
| 11106 | WIFI_MULT_INFO | struct | R | Multi-WIFI configuration |
| 11127 | IOT_SERVER_BLE_SN | string | R | IOT server BLE serial number |
| 12002 | IOT_SETTINGS_INFO | struct | R/W | IOT settings |
| 12161 | IOT_ENABLE_INFO | bitmask | R/W | IOT enable configuration |
| 12162 | IOT_ENABLE_HI | bitmask | R/W | IOT high enable bits |
| 12174 | IOT_NETMASK_GATEWAY | struct | R/W | Network netmask/gateway |
| 12185 | IOT_BLE_SERVER_SET | struct | R/W | BLE server settings |
| 12195 | IOT_BLE_CLIENT_SET | struct | R/W | BLE client settings |
| 12205 | IOT_DISPLAY_SET | struct | R/W | Display settings |
| 13088 | IOT_MATTER_INFO | struct | R | Matter protocol info |
| 13120 | IOT_OTA_CTRL_ENABLE | uint16 | R/W | OTA control enable |
| 13500 | IOT_WIFI_MESH | struct | R/W | WiFi Mesh configuration |
| 13506 | WIFI_MESH_ENABLE | uint16 | R/W | WiFi Mesh enable |
| 13509 | WIFI_STATION_BSSID | string | R/W | WiFi station BSSID |
| 13600 | IOT_EXTENSION_SETTINGS | struct | R/W | Extension settings |
| 13603 | IOT_BLE_SERVER_KEY | string | R/W | BLE server key |
| 13611 | WIFI_STATION_MULT1 | struct | R/W | Multi-WIFI station 1 |
| 13624 | WIFI_STATION_MULT2 | struct | R/W | Multi-WIFI station 2 |
| 13776 | BLE_CLIENT_PAIR_SN | string | R/W | BLE client pairing serial number |

### Smart Plug (14500-14700)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 14500 | SMART_PLUG_INFO | struct[] | R | Smart plug information (multiple plugs) |
| 14700 | SMART_PLUG_SETTINGS | struct | R/W | Smart plug settings |
| 14701 | SMART_PLUG_SET_ENABLE_1 | bitmask | R/W | Smart plug enable bitmask (each bit = one plug) |

### DCDC Charger (15500-15634)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 15500 | DCDC_INFO | struct | R | DCDC charger information |
| 15600 | DCDC_SETTINGS | struct | R/W | DCDC settings |
| 15603 | DCDC_VOLT_SET_DC2 | uint16 | R/W | DCDC voltage set for DC2 (V * 10) |
| 15606 | DCDC_CURRENT_SET_DC3 | uint16 | R/W | DCDC current set for DC3 (A * 10) |
| 15614 | DCDC_CHG_MODE_1 | uint16 | R/W | DCDC charging mode 1 |
| 15621 | DCDC_POWER_DC3_SET | uint16 | R/W | DCDC power set for DC3 (W) |
| 15625 | DCDC_SET_4 | uint16 | R/W | DCDC setting 4 |
| 15626 | DCDC_SET_5 | uint16 | R/W | DCDC setting 5 |
| 15627 | DCDC_SET_POWER | uint16 | R/W | DCDC power set (W) |
| 15634 | DCDC_IOT_PCS_MODE | uint16 | R/W | DCDC IOT PCS mode |

### Panel (16000-16500)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 16000 | PANEL_BASE_INFO | struct | R | Panel base information |
| 16100 | PANEL_DC_INFO | struct | R | Panel DC output info |
| 16200 | PANEL_AC_INFO | struct | R | Panel AC output info |
| 16300 | PANEL_PROTECT_INFO | struct | R | Panel protection info |
| 16400 | PANEL_SETTINGS_BASE | struct | R/W | Panel base settings |
| 16404 | PANEL_SET_1 | uint16 | R/W | Panel setting 1 |
| 16421 | PANEL_SOC_SET_START_AC | uint16 | R/W | Panel SOC start threshold for AC |
| 16427 | PANEL_SOC_SET_START_DC | uint16 | R/W | Panel SOC start threshold for DC |
| 16500 | PANEL_SETTINGS_MAIN | struct | R/W | Panel main settings |

### EPAD/HMI (18000-18600)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 18000 | EPAD_BASE_INFO | struct | R | EPAD base information |
| 18300 | EPAD_BASE_SETTINGS | struct | R/W | EPAD settings |
| 18400 | EPAD_BASE_LIQUID_POINT1 | struct | R/W | Liquid sensor calibration point 1 |
| 18500 | EPAD_BASE_LIQUID_POINT2 | struct | R/W | Liquid sensor calibration point 2 |
| 18600 | EPAD_BASE_LIQUID_POINT3 | struct | R/W | Liquid sensor calibration point 3 |

### Community/Timer Settings (19000-26001)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 19000 | COMM_SOC_SETTINGS | struct[] | R/W | Community SOC threshold settings (up to 10 groups) |
| 19100 | COMM_DELAY_SETTINGS | struct | R/W | Community delay settings |
| 19200 | COMM_SCHEDULED_CHG_DSG | struct[] | R/W | Scheduled charge/discharge times |
| 19300 | COMM_TIMER_SETTINGS | struct[] | R/W | Community timer settings |
| 19305 | COMM_TIMER_SETTINGS_1 | struct[] | R/W | Community timer settings group 1 |
| 19425 | COMM_TIMER_SETTINGS_2 | struct[] | R/W | Community timer settings group 2 |
| 26000 | TOU_CTRL_ENABLE | uint16 | R/W | Time-of-use control enable |
| 26001 | TOU_CTRL | struct[] | R/W | TOU time periods and rates |

### Grid Protection (2400-2440)

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 2400 | CERT_SETTINGS_INFO | struct | R | Grid certification info |
| 2401 | GRID_CERT_COUNTRY | uint16 | R/W | Grid certification country code |
| 2402 | GRID_UV1_VAL | uint16 | R/W | Grid undervoltage 1 threshold (V) |
| 2403 | GRID_UV1_TIME | uint16 | R/W | Grid undervoltage 1 time (seconds) |
| 2404 | GRID_UV2_VAL | uint16 | R/W | Grid undervoltage 2 threshold |
| 2405 | GRID_UV2_TIME | uint16 | R/W | Grid undervoltage 2 time |
| 2411 | GRID_OV1_VAL | uint16 | R/W | Grid overvoltage 1 threshold (V) |
| 2412 | GRID_OV1_TIME | uint16 | R/W | Grid overvoltage 1 time (seconds) |
| 2413 | GRID_OV2_VAL | uint16 | R/W | Grid overvoltage 2 threshold |
| 2414 | GRID_OV2_TIME | uint16 | R/W | Grid overvoltage 2 time |
| 2419 | GRID_UF_VAL | uint16 | R/W | Grid underfrequency threshold (Hz) |
| 2420 | GRID_UF_TIME | uint16 | R/W | Grid underfrequency time |
| 2421 | GRID_UF2_VAL | uint16 | R/W | Grid underfrequency 2 threshold |
| 2422 | GRID_UF2_TIME | uint16 | R/W | Grid underfrequency 2 time |
| 2427 | GRID_OF_VAL | uint16 | R/W | Grid overfrequency threshold (Hz) |
| 2428 | GRID_OF_TIME | uint16 | R/W | Grid overfrequency time |
| 2429 | GRID_OF2_VAL | uint16 | R/W | Grid overfrequency 2 threshold |
| 2430 | GRID_OF2_TIME | uint16 | R/W | Grid overfrequency 2 time |
| 2435 | GRID_VOLT_MIN_VAL | uint16 | R/W | Grid minimum voltage |
| 2436 | GRID_VOLT_MAX_VAL | uint16 | R/W | Grid maximum voltage |
| 2437 | GRID_FREQ_MIN_VAL | uint16 | R/W | Grid minimum frequency |
| 2438 | GRID_FREQ_MAX_VAL | uint16 | R/W | Grid maximum frequency |
| 2439 | GRID_RETRY_TIME | uint16 | R/W | Grid retry time |
| 40187 | GRID_UV3_VAL | uint16 | R/W | Grid undervoltage 3 threshold |
| 40188 | GRID_UV3_TIME | uint16 | R/W | Grid undervoltage 3 time |
| 40189 | GRID_UV4_VAL | uint16 | R/W | Grid undervoltage 4 threshold |
| 40190 | GRID_UV4_TIME | uint16 | R/W | Grid undervoltage 4 time |
| 40191 | GRID_UV5_VAL | uint16 | R/W | Grid undervoltage 5 threshold |
| 40192 | GRID_UV5_TIME | uint16 | R/W | Grid undervoltage 5 time |
| 40199 | GRID_OV3_VAL | uint16 | R/W | Grid overvoltage 3 threshold |
| 40200 | GRID_OV3_TIME | uint16 | R/W | Grid overvoltage 3 time |
| 40201 | GRID_OV4_VAL | uint16 | R/W | Grid overvoltage 4 threshold |
| 40202 | GRID_OV4_TIME | uint16 | R/W | Grid overvoltage 4 time |
| 40203 | GRID_OV5_VAL | uint16 | R/W | Grid overvoltage 5 threshold |
| 40204 | GRID_OV5_TIME | uint16 | R/W | Grid overvoltage 5 time |

### Other Registers

| Address | Name | Type | Access | Description |
|---------|------|------|--------|-------------|
| 100 | APP_HOME_DATA | struct | R | App home screen data |
| 700 | OTA_SETTINGS | struct | R/W | OTA firmware settings |
| 720 | OTA_STATUS | struct | R | OTA update status |
| 3000 | LOG_HISTORY_INFO | struct | R | Fault/event history logs |
| 5000 | TIME_CTRL_INFO_START | struct[] | R/W | Time control schedule start |
| 5000 | TIME_CTRL_WEEK_MODE | struct | R/W | Weekly timer mode |
| 7000 | PACK_SETTINGS_INFO | struct | R/W | Pack settings |
| 7000 | PACK_SET_ID | uint16 | R/W | Pack set ID |
| 2080 | PACK_NUM_SET_SHOW | uint16 | R/W | Pack number display setting |
| 2081 | INV_NUM_SET | uint16 | R/W | Inverter number setting |
| 2084 | PV_ADV_SET | uint16 | R/W | PV advanced settings |
| 2086 | JA12_ENABLE | uint16 | R/W | JA12 enable flag |
| 2060 | PV_TYPE_SET | uint16 | R/W | PV type setting |
| 2067 | LCD_SCREEN_TIME | uint16 | R/W | LCD screen timeout |
| 2078 | LED_COLOR_SET | uint16 | R/W | LED color setting |
| 2246 | GEN_SET | bitmask | R/W | Generator settings |
| 2406 | POWER_FACTOR | uint16 | R/W | Power factor setting |
| 2409 | POWER_RATE_LIMIT | uint16 | R/W | Power rate limit |
| 2415 | POWER_REACTIVE_RATIO | uint16 | R/W | Power reactive ratio |
| 29770 | BOOT_UPGRADE_SUPPORT | struct | R | Bootloader upgrade support |
| 29772 | BOOT_SOFTWARE_INFO | struct | R | Bootloader software info |
| 30901 | TEST_SETTINGS | struct | R/W | Test/debug settings |
| 40000 | COMM_DATA_OTHER | struct | R/W | Other community data |
| 40044 | CERT_SETTINGS_EXT | struct | R/W | Extended certification settings |
| 40181 | ANTI_BACKFLOW_CERTIFICATION | struct | R | Anti-backflow certification |
| 17100 | AT1_BASE_INFO | struct | R | AT1 (Auto Transfer Switch) base info |
| 17000 | ATS_INFO | struct | R | ATS information |
| 21000 | NODE_INFO | struct | R/W | Node/device information |
| 30001 | ACTIVE_CODE_INPUT | string | R/W | Activation code input |

## Data Types

| Type | Size | Description | Encoding |
|------|------|-------------|----------|
| uint16 | 2 bytes | Unsigned 16-bit integer | Big-endian |
| int16 | 2 bytes | Signed 16-bit integer | Big-endian |
| uint32 | 4 bytes | Unsigned 32-bit integer | Two consecutive uint16 (high word first) |
| float | 4 bytes | IEEE 754 floating point | Two consecutive uint16 |
| string | variable | ASCII/UTF-8 string | Length-prefixed |
| bitmask | 2 bytes | Bit field | Each bit represents a flag |
| struct | variable | Compound data | Multiple registers |

## Modbus Slave Addressing

The protocol supports multiple Modbus slave addresses:

- **Default slave address:** 1 (for most operations)
- **Device-specific slaves:** Some devices use different slave IDs
- **Parameter passing:** Slave address is passed as a parameter to read/write functions
- **Multi-device support:** The `ConnectManager` tracks `modbusSlaveAddr` per device

### Example Usage

```java
// Read 10 registers from slave 1 at address 2005
ConnectManager.getReadTask(2005, 10, 1)

// Write single register to slave 1 at address 2011
ConnectManager.getSetTask(2011, 1, 1)

// Write multiple registers to slave 1 at address 2022
ProtocolParse.INSTANCE.getMutiRegSetTask(2022, "0100", 2, 1)
```

## Encryption Details

### AES-CBC Encryption

**Algorithm:** AES-CBC with PKCS7 padding  
**Key:** `sxd_aiot_key_001` (default, 16 bytes)  
**IV:** `sxd_aiot_2022_01` (default, 16 bytes)  
**Implementation:** `b/a.java`

```java
// Encryption (for sending)
Cipher cipher = Cipher.getInstance("AES/CBC/PKCS7Padding");
cipher.init(Cipher.ENCRYPT_MODE, secretKey, ivSpec);
encrypted = cipher.doFinal(payload);

// Decryption (for receiving)
Cipher cipher = Cipher.getInstance("AES/CBC/PKCS7Padding");
cipher.init(Cipher.DECRYPT_MODE, secretKey, ivSpec);
decrypted = cipher.doFinal(encryptedPayload);
```

### CRC Calculation

**Algorithm:** Custom CRC with lookup tables  
**Tables:** `f27a` and `f28b` in `b/c.java`  
**Implementation:** `b/c.java`

```java
public static int a(byte[] bArr) {
    int length = bArr.length;
    int i = 0;
    byte b2 = 255;
    int i2 = 255;
    while (i < length) {
        int i3 = (i2 ^ bArr[i]) & 255;
        int i4 = b2 ^ f27a[i3];
        byte b3 = f28b[i3];
        i++;
        i2 = i4;
        b2 = b3;
    }
    return ((b2 & 255) << 8) | (i2 & 255);
}
```

## Protocol Implementation Files

### Key Source Files

| File | Purpose |
|------|---------|
| `ProtocolAddrV2.java` | Register address definitions |
| `ProtocolModule.java` | UniApp module exposing P0x17/P0x18/P0x19 methods |
| `ProtocolTool.java` | High-level protocol utilities |
| `Param.java` | Parameter structure for multi-register operations |
| `c/a.java` | P0x17 packet builder (read multiple) |
| `c/b.java` | P0x18 packet builder (write single) |
| `c/c.java` | P0x19 packet builder (write multiple) |
| `b/a.java` | AES encryption/decryption |
| `b/b.java` | Byte utilities (conversion, concatenation) |
| `b/c.java` | CRC calculation and validation |

### Packet Building Flow

1. **Read (P0x17):**
   - Build Modbus frame: `[slave][func][addr_hi][addr_lo][count_hi][count_lo]`
   - Encrypt with AES-CBC
   - Add header and CRC
   - Send to device

2. **Write Single (P0x18):**
   - Build Modbus frame: `[slave][func][addr_hi][addr_lo][value_hi][value_lo]`
   - Encrypt with AES-CBC
   - Add header and CRC
   - Send to device

3. **Write Multiple (P0x19):**
   - Build Modbus frame: `[slave][func][addr_hi][addr_lo][count_hi][count_lo][byte_count][data...]`
   - Encrypt with AES-CBC
   - Add header and CRC
   - Send to device

## Device Compatibility

This protocol (ProtocolAddrV2) is used in the following Bluetti devices:

- EP2000
- EP600
- EP760
- AC180
- AC200P
- AC300
- AC500
- And other models with protocol version 2.0+

**Protocol version check:** `ProtocolVer.VER_2000` (value 2000) indicates V2 protocol.

## Notes

1. All communication is **encrypted** - both reads and writes
2. The protocol uses **big-endian** byte order for multi-byte values
3. Some registers are **bitmask** fields where each bit represents a boolean flag
4. Multi-register reads/writes are common for configuration and status data
5. The `ConnectManager` handles task queuing and execution
6. Responses are parsed by `ProtocolParserV2` and dispatched via `LiveEventBus`

## References

- Main protocol interface: `com.nky.protocal.unimodule.ProtocolModule`
- Protocol addresses: `net.poweroak.bluetticloud.ui.connectv2.tools.ProtocolAddrV2`
- Connection manager: `net.poweroak.bluetticloud.ui.connect.ConnectManager`
- Protocol parser: `net.poweroak.bluetticloud.ui.connectv2.tools.ProtocolParserV2`

---

**End of Document**