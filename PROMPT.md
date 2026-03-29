# AI Design Specification & Master Prompt

This document contains the engineering prompt used to generate the **IP-SM-GW UE Simulator**. It is preserved here to ensure architectural consistency for future updates and to document the technical constraints of the project.

## The Design Prompt

> **Role:** Act as a Senior Telecommunications Software Engineer specializing in IMS (IP Multimedia Subsystem) and 3GPP protocols.
>
> **Task:** Develop a Python 3 "IP-SM-GW UE Simulator" script and a corresponding `.ini` configuration file.
>
> **Core Requirements:**
> 1. **Protocol Stack:** Implement SIP over UDP. The script must simulate an IMS UE and handle **3GPP TS 24.341** (SMS over IP) and **TS 24.011** (CP/RP layers).
> 2. **Zero Dependencies:** Use **only** Python standard libraries (`socket`, `threading`, `configparser`, `binascii`, etc.). Do not use Scapy, `pysctp`, or any external SIP libraries.
> 3. **Binary Logic:** Manually implement BCD (Binary Coded Decimal) encoding for MSISDNs/IMSIs and construct the binary RP-DATA PDU for MO (Mobile Originated) SMS.
> 4. **IMS Specifics:** Support specialized SIP headers: `P-Asserted-Identity`, `P-Charging-Vector` (with ICID and IOI), and `P-Access-Network-Info`.
> 5. **Architecture:** >    - Use a **multi-threaded** approach with a dedicated RX loop and a worker pool to process inbound SIP messages asynchronously.
>    - Implement an **interactive CLI menu** to trigger SIP REGISTER, single MO SMS, and high-volume Load Testing (configurable TPS).
> 6. **Strict Configuration:**
>    - Create a `Config` class that reads from the `.ini` file.
>    - **Mandatory Enforcement:** If any key or section is missing from the `.ini` file, the script must display a red error message and exit immediately. No hardcoded default values are allowed within the script logic.
> 7. **Logging:** Use `logging.handlers.RotatingFileHandler` for protocol debugging and color-coded console output for real-time status.
>
> **Configuration File Structure:** > Include sections for `[network]`, `[subscriber]`, `[smsc]`, `[ims]`, `[charging]`, and `[load_test]`.
