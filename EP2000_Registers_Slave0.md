# EP2000 Register Map: Slave 0 (EBOX/IoT)

## Register Range: 2200 - 2280 (Advanced Settings)
These registers control the global behavior of the entire battery stack and inverter coordination.

### 1. System Configuration
| Register | Field | Type | Description |
| :--- | :--- | :--- | :--- |
| **2200** | `AdvLoginPassword` | ASCII | 8-char password for advanced settings access. |
| **2209** | `InvVoltage` | uint8 | Nominal Output Voltage (V). |
| **2210** | `InvFreq` | uint8 | Output Frequency (50/60 Hz). |
| **2241** | `EmsCtrlMode` | uint4 | **The "Brain" Mode:** Determines power priority. |
| **2277** | `BatteryCapacity` | uint32 | Total System Capacity in **Wh**. |

### 2. Logic Bitmasks
These single registers contain multiple Boolean flags.

#### Register 2232 (System Flags)
| Bit | Name | Description |
| :--- | :--- | :--- |
| **0** | `AtsEnable` | Automatic Transfer Switch integration. |
| **8** | `GeneratorEnable` | External Generator support. |
| **10** | `MicroInvEnable` | AC-coupled solar support. |
| **14** | `GridConnEnable` | Allow grid parallel operation. |

#### Register 2242 (Performance Flags)
| Bit | Name | Description |
| :--- | :--- | :--- |
| **0** | `EvEnable` | EV Charging port logic. |
| **4** | `BalanceEnable` | Phase-to-phase load balancing. |
| **5** | `WinterMode` | Low-temp heater/protection logic. |

### 3. Power Limits
| Register | Field | Unit | Description |
| :--- | :--- | :--- | :--- |
| **2213** | `GridMaxPower` | W | Limit on grid draw. |
| **2215** | `FeedbackMaxPower`| W | Limit on grid export. |
| **2247** | `GenSocStart` | % | Auto-start generator at this level. |
| **2248** | `GenSocStop` | % | Auto-stop generator at this level. |

## Register Range: 21000 - 21032 (Node Map)
Used by the app to dynamically discover the system layout.
- **21001-21008:** Position 1 (usually Slave 0).
- **21009-21016:** Position 2 (usually Inverter Slave 1).
- **21017-21024:** Position 3 (usually Battery Slave 41).
