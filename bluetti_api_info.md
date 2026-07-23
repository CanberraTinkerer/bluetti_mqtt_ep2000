# Bluetti Cloud API Reference (May 2026)

## 1. Overview
As of May 2026, the Bluetti Developer Platform provides a Cloud-to-Cloud API for monitoring and controlling Bluetti devices (including the EP2000). While a public Swagger/OpenAPI documentation site is not yet globally accessible, the API structure has been reverse-engineered from official integrations.

## 2. API Infrastructure
- **Base URL:** `https://api.bluettipower.com/`
- **Protocol:** REST over HTTPS / JSON
- **Authentication:** OAuth 2.0 (Bearertoken) + Request Signing

## 3. Core Endpoints
| Path | Method | Description |
| :--- | :--- | :--- |
| `/api/v1/auth/login` | POST | Authenticate with Bluetti credentials. Returns `access_token`. |
| `/api/v1/device/list` | GET | List all devices bound to your account. |
| `/api/v1/device/status` | GET | Get real-time telemetry (PV input, Battery %, AC/DC load). |
| `/api/v1/device/control` | POST | Send commands (Toggle AC/DC, Eco Mode, etc.). |

## 4. Request Signing (Security)
All requests must include security headers to prevent tampering. Without a valid `sign` header, the API returns `401 Unauthorized`.

### Required Headers
- `app-id`: Your unique Developer App ID.
- `timestamp`: Current time in milliseconds.
- `nonce`: A unique random string (UUID).
- `sign`: The calculated MD5 signature.

### Signature Calculation Algorithm
1. **Sort:** Collect all request parameters (Query string for GET, JSON body for POST) and sort them alphabetically by key.
2. **Concatenate:** Create a string in the format `key1=value1&key2=value2`.
3. **Secret Suffix:** Append your `app-secret` to the end: `key1=value1&key2=value2secret_here`.
4. **Hash:** Perform an **MD5** hash on the string.
5. **Format:** Convert the hash to **Uppercase** hexadecimal.

## 5. Local API & Future Roadmap
- **Current State:** Primarily Cloud-based.
- **Mid-2026 Target:** Bluetti R&D is rolling out **Local MQTT** support via LAN, allowing direct communication with devices (like the EP2000) on the local network (Port 1883/8883), bypassing the cloud entirely.

## 6. Official Resources
- **GitHub:** [bluetti-official/bluetti-home-assistant](https://github.com/bluetti-official/bluetti-home-assistant)
- **Community Hub:** [Bluetti Community Forum](https://community.bluettipower.com/)
