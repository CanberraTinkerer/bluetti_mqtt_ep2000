# EP2000 System Architecture & Node Topology

## 1. System Overview
The Bluetti EP2000 is a modular, high-voltage energy storage system. Unlike simpler portable units, it operates as a networked "Mesh" of components, each addressed via Modbus over a central communication bus managed by the IoT module.

## 2. Node & Slave ID Mapping
The system uses specific Slave IDs to distinguish between functional blocks. In the "Node Directory" (Registers 21000+), these are often referred to as `p1`, `p2`, etc.

| Slave ID | Component Name | Model ID | Description |
| :--- | :--- | :--- | :--- |
| **0** | **EBOX / IoT** | **3004** | The "Brain." Handles Wi-Fi, Bluetooth, and global system logic. |
| **1** | **EP2000 (Inverter)**| **1004** | The power conversion unit (AC/DC). |
| **41** | **HV800 / B500H** | **4001** | High-Voltage Battery Expansion Pack 1. |
| **42** | **HV800 / B500H** | **4001** | High-Voltage Battery Expansion Pack 2 (if present). |

## 3. Communication Protocols


### Cloud API
- **Endpoint:** `https://api.bluettipower.com/`
- **Auth:** Bearer Token + MD5 Request Signing.
- **Signing Algorithm:** `MD5(Alphabetical_Sorted_Params + AppSecret)`.

## 4. Operational Modes (EMS)
Controlled via **Slave 0, Register 2241**.
- **Mode 0:** Standard UPS Mode.
- **Mode 1:** PV Priority (Solar first).
- **Mode 2:** Time of Use (TOU) / Scheduled.
- **Mode 3:** Customized / Full Manual.
