# IP-SM-GW UE Simulator

A Python SIP/UDP client that simulates an **IMS UE (User Equipment)** sending
**MO SMS** and **SIP REGISTER** messages toward an **IP Short Message Gateway (IP-SM-GW)**.

Implements 3GPP TS 24.341 / TS 24.011 over plain SIP/UDP.  
**Standard library only — no third-party packages required.**

---

## What This Script Does

```
  ┌──────────────────────┐   SIP / UDP   ┌─────────────────────┐
  │  This script         │──────────────▶│  IP-SM-GW           │
  │  (IMS UE simulator)  │               │  (e.g. 192.168.0.101)│
  │                      │◀──────────────│                     │
  └──────────────────────┘               └─────────────────────┘

  Sends:                                 Receives & replies:
  ● REGISTER  (IMS registration)         ● 200 OK
  ● MESSAGE   (MO SMS / RP-DATA MO)      ● 202 Accepted
                                         ● MT MESSAGE (RP-DATA MT)
  Receives & handles:
  ● 202 Accepted  → latency tracked
  ● MT MESSAGE    → auto 200 OK + RP-ACK delivery report after 1 s
  ● SUBSCRIBE     → 200 OK + NOTIFY reginfo
  ● OPTIONS       → 201 OK
```

---

## Requirements

| Item | Version |
|------|---------|
| Python | 3.8 or newer |
| OS | Linux / macOS / Windows |
| Dependencies | **None** (stdlib only) |
| Network | UDP 5060 reachable to IP-SM-GW |

---

## Quick Start

```bash
git clone https://github.com/<your-user>/ipsmgw-ue-simulator.git
cd ipsmgw-ue-simulator

# 1. Copy the sample config and edit it for your environment
cp ipsmgw_ue_simulator.ini my_ipsmgw_ue_simulator.ini
nano my_ipsmgw_ue_simulator.ini        # set IPs, MSISDNs, domain names

# 2. Run
python3 ipsmgw_ue_simulator.py --config my_ipsmgw_ue_simulator.ini

# If no --config is given it loads ipsmgw_ue_simulator.ini from the current directory
python3 ipsmgw_ue_simulator.py
```

---

## Configuration File

All customer/network-specific values live in a `.cfg` file.  
**No code changes are needed to switch between customers or lab environments** —
just point to a different config file.

```bash
# Customer A lab
python3 ipsmgw_ue_simulator.py --config customerA.cfg

# Customer B production
python3 ipsmgw_ue_simulator.py --config customerB_prod.cfg
```

### Full reference — `ipsmgw_ue_simulator.ini`

```ini
# =============================================================
#  IP-SM-GW UE Simulator – Customer Configuration
# =============================================================

# -------------------------------------------------------------
# [network]  Local and remote SIP endpoints
# -------------------------------------------------------------
[network]
local_ip        = 192.168.0.100      # IP to bind the UDP socket (this machine)
local_port      = 5060              # UDP port to listen and send from
remote_host     = 192.168.0.101      # IP-SM-GW IP address
remote_port     = 5060              # IP-SM-GW SIP port
orig_host       = 192.168.0.100      # Host used in outbound SIP From/Via URIs

# -------------------------------------------------------------
# [subscriber]  Default UE identity for single-shot sends
# -------------------------------------------------------------
[subscriber]
default_msisdn  = 619305004412      # A-party MSISDN (originating subscriber)
default_imsi    = 505034253444139
default_tel     = 619375725469      # P-Asserted-Identity tel URI (secondary number)

# -------------------------------------------------------------
# [smsc]  SMSC / IP-SM-GW addressing
# -------------------------------------------------------------
[smsc]
smsc_gt         = 619332489001      # SMSC global title – used as RP-DA in MO RP-DATA
smsc_msisdn     = 619332489464      # SMSC MSISDN – used in SIP Request-URI and To header
smsc_domain     = smsc123-traffic.oper.com.tw   # SIP domain of the IP-SM-GW
smsc_domain_au  = smsc123-traffic.oper.com.au   # Alternate domain used in NOTIFY target
register_uri    = smsc123.lab.mcoipsm.mnc002.mcc505.3gppnetwork.org  # REGISTER Request-URI
register_from   = pxms412-oper.com.tw:5060       # From address used in REGISTER

# -------------------------------------------------------------
# [ims]  IMS network identifiers
# -------------------------------------------------------------
[ims]
ims_domain          = ims.mnc092.mcc466.3gppnetwork.org   # P-Asserted-Identity domain
register_to_domain  = ims.mnc002.mcc505.3gppnetwork.org   # REGISTER To header domain
auth_domain         = ims.mnc015.mcc234.3gppnetwork.org   # Authorization Digest domain

# -------------------------------------------------------------
# [charging]  P-Charging-Vector and P-Access-Network-Info
# -------------------------------------------------------------
[charging]
icid_prefix         = 3l1sbg1108.ims.mnc092.mcc466                      # icid-value prefix
icid_generated_at   = 3l1sbg1108.ims.mnc092.mcc466.3gppnetwork.org      # icid-generated-at
orig_ioi            = ims.mnc092.mcc466.3gppnetwork.org                  # orig-ioi
subscribe_icid      = kklji1sbg200-sgc017019.lab.ims.mnc002.mcc505.3gppnetwork.org-1579-672782-650343
subscribe_pan       = 3GPP-E-UTRAN-FDD;utran-cell-id-3gpp=50503045713ffe32
access_net_info     = IEEE-802.11n;i-wlan-node-id=2034fbc343d2           # P-Access-Network-Info in MO SMS

# -------------------------------------------------------------
# [load_test]  Default load test parameters
# -------------------------------------------------------------
[load_test]
default_dest_msisdn = 619363540361  # Default B-party MSISDN for MO SMS burst
mo_default_count    = 100           # Default message count for MO burst
mo_default_tps      = 10            # Default TPS for MO burst
reg_default_count   = 100           # Default message count for REGISTER burst
reg_default_tps     = 10            # Default TPS for REGISTER burst

# -------------------------------------------------------------
# [tag]  SIP tag / user-agent strings
# -------------------------------------------------------------
[tag]
delivery_report_tag_prefix  = ims.testcp.com-1111111111   # Prefix for From tag in delivery reports
user_agent                  = DTF_UA                       # User-Agent header value
```

### Config keys at a glance

| Section | Key | Used in |
|---------|-----|---------|
| `[network]` | `local_ip` / `local_port` | UDP bind, Via header |
| `[network]` | `remote_host` / `remote_port` | All outbound messages |
| `[network]` | `orig_host` | From/Via SIP URIs |
| `[subscriber]` | `default_msisdn` | MO SMS, REGISTER, load test defaults |
| `[subscriber]` | `default_imsi` | REGISTER Authorization, IMEI |
| `[subscriber]` | `default_tel` | P-Asserted-Identity tel URI in MO SMS |
| `[smsc]` | `smsc_gt` | RP-DA in MO RP-DATA PDU |
| `[smsc]` | `smsc_msisdn` | SIP Request-URI and To in MO MESSAGE |
| `[smsc]` | `smsc_domain` | To header, NOTIFY reginfo, REGISTER body |
| `[smsc]` | `smsc_domain_au` | NOTIFY Request-URI and To |
| `[smsc]` | `register_uri` | REGISTER Request-URI |
| `[smsc]` | `register_from` | REGISTER From header |
| `[ims]` | `ims_domain` | P-Asserted-Identity in MO MESSAGE |
| `[ims]` | `register_to_domain` | REGISTER To header |
| `[ims]` | `auth_domain` | Authorization Digest username |
| `[charging]` | `icid_prefix` | P-Charging-Vector icid-value in MO MESSAGE |
| `[charging]` | `icid_generated_at` | P-Charging-Vector icid-generated-at |
| `[charging]` | `orig_ioi` | P-Charging-Vector orig-ioi |
| `[charging]` | `subscribe_icid` | P-Charging-Vector in SUBSCRIBE 200 OK |
| `[charging]` | `subscribe_pan` | P-Access-Network-Info in SUBSCRIBE 200 OK |
| `[charging]` | `access_net_info` | P-Access-Network-Info in MO MESSAGE |
| `[load_test]` | `default_dest_msisdn` | Default B-party for MO burst |
| `[load_test]` | `mo_default_count` / `mo_default_tps` | MO burst menu defaults |
| `[load_test]` | `reg_default_count` / `reg_default_tps` | REGISTER burst menu defaults |
| `[tag]` | `delivery_report_tag_prefix` | From tag in RP-ACK delivery reports |
| `[tag]` | `user_agent` | User-Agent header in REGISTER |

---

## Menu

```
╔══════════════════════════════════════════╗
║       IP-SM-GW SIP Server  v2.1          ║
╚══════════════════════════════════════════╝
  Local 192.168.0.100:5060  ↔  Remote 192.168.0.101:5060

  1  Send REGISTER
  2  Send MO SMS
  3  Message statistics
  4  Connection info
  5  Load test  –  MO SMS burst
  6  Load test  –  SIP REGISTER burst
  7  Load test results  (N run(s))
  0  Quit
```

---

## Outbound Messages

### Option 1 — SIP REGISTER

Sends a `REGISTER` toward the IP-SM-GW with a 3GPP IMS XML body.

```
REGISTER sip:<register_uri> SIP/2.0
Via:     SIP/2.0/UDP <local_ip>:<local_port>;branch=z9hG4bK...
From:    <sip:<register_from>>;tag=...
To:      <sip:+<msisdn>@<register_to_domain>>
Expires: 300
Content-Type: application/3gpp-ims+xml
Authorization: Digest username="<imsi>@<auth_domain>",algorithm=AKAv1-MD5
```

### Option 2 — MO SMS

Sends a `MESSAGE` carrying an RP-DATA (MO, MTI=0x00) PDU with an SMS-SUBMIT TPDU.

```
MESSAGE sip:+<smsc_msisdn>@<orig_host>;user=phone SIP/2.0
From:    <sip:+<msisdn>@<orig_host>>;tag=...
To:      <sip:+<smsc_msisdn>@<smsc_domain>;user=phone>
P-Asserted-Identity: sip:+<msisdn>@<ims_domain>
P-Access-Network-Info: <access_net_info>
P-Charging-Vector: icid-value=<icid_prefix>...;icid-generated-at=<icid_generated_at>;orig-ioi=<orig_ioi>
Content-Type: application/vnd.3gpp.sms
[Body: RP-DATA MO with SMS-SUBMIT TPDU, RP-DA = <smsc_gt>]
```

---

## Inbound Messages Handled

| Inbound SIP | Response sent | Follow-up |
|-------------|--------------|-----------|
| `MESSAGE` RP-DATA MO (MTI=0x00) | `200 OK` | RP-ACK delivery report after 1 s |
| `MESSAGE` RP-DATA MT (MTI=0x01) | `200 OK` | RP-ACK delivery report after 1 s |
| `MESSAGE` RP-ACK / RP-ERROR | `200 OK` | None |
| `202 Accepted` (response to MO) | — | Latency recorded |
| `200 OK` (response to REGISTER) | — | Latency recorded |
| `SUBSCRIBE` | `200 OK` + `NOTIFY` (reginfo XML) | — |
| `OPTIONS` | `201 OK` | — |
| `NOTIFY` | `200 OK` | — |

---

## Console Log Format

Every SIP event prints as **one line** with microsecond precision:

```
HH:MM:SS.ffffff  ←/→  METHOD/STATUS  host:port  cseq=…  id=<call-id>  [extras]
```

### Example session

```
14:32:00.001234 → REGISTER   192.168.0.101:5060  cseq=63104 REGISTER  id=abcdef12345  msisdn=+619305004412 expires=300s
14:32:00.012500 ← 200 OK     192.168.0.101:5060  cseq=63104 REGISTER  id=abcdef12345
14:32:01.050000 → MESSAGE    192.168.0.101:5060  cseq=63104 MESSAGE   id=abcdefXXXXX  MO-SMS src=+619305004412 smsc=+619332489001 rp=32B
14:32:01.062300 ← 202 Accepted  192.168.0.101:5060  cseq=63104 MESSAGE  id=abcdefXXXXX
14:32:05.900000 ← MESSAGE    192.168.0.101:5060  cseq=1 MESSAGE   id=smsc123.oper-...  dst=+619336453012 [RP-DATA(MT) ref=169 oa=619332489001]
14:32:05.901100 → 200 OK     192.168.0.101:5060  cseq=1 MESSAGE   id=smsc123.oper-...  MESSAGE reply, RP-ACK in 1s [MT-dlv]
14:32:06.902400 → MESSAGE    192.168.0.101:5060  cseq=120 MESSAGE  id=smsc123.oper-...  RP-ACK ref=169 [02 a9 41 00]
14:32:06.914100 ← 202 Accepted  192.168.0.101:5060  cseq=120 MESSAGE  id=smsc123.oper-...
```

---

## File Log (`sip_server.log`)

Full AVP detail for every event — SIP headers, RP-PDU fields, body hex:

```
--- RX REQUEST  14:32:05.900000  MESSAGE  from=192.168.0.101:5060 ---
  From            : <sip:192.168.0.101:5060>;tag=smsc.test.cm-0000001
  To              : <sip:+619336453012@192.168.0.101;user=phone>
  Call-ID         : smsc.test.cm-00000000000656589574-1773911643
  CSeq            : 1 MESSAGE
  P-Asserted-ID   : <sip:192.168.0.101:5060>
  User-Agent      : MCO-CPM-Client/OMA2.0
  [3GPP 24.011 RP PDU]
    RP-MTI          : RP-DATA(MT)  (0x01)
    RP-Ref          : 169  (0xa9)
    RP-OA           : 619332489001
  Body (hex)      : 01 a9 07 91 68 49 23 84 ...
```

**Rotation:** 10 MB × 5 files (50 MB cap).

---

## Load Test

### MO SMS Burst — option 5

| Prompt | Default from config | Description |
|--------|---------------------|-------------|
| Orig MSISDN base | `default_msisdn` | A-party; last 5 digits rotate |
| Dest MSISDN (B) | `default_dest_msisdn` | B-party; last 5 digits rotate |
| Tel MSISDN PAI | `default_tel` | P-Asserted-Identity tel URI |
| Number of msgs | `mo_default_count` | Total to send |
| Target TPS | `mo_default_tps` | Transactions per second |

### SIP REGISTER Burst — option 6

| Prompt | Default from config | Description |
|--------|---------------------|-------------|
| MSISDN base | `default_msisdn` | Last 5 digits rotate |
| Number of msgs | `reg_default_count` | Total to send |
| Target TPS | `reg_default_tps` | Transactions per second |

### Results — option 7

Completed runs are stored in session history (up to 20). Select a run to view full latency report (p50/p90/p99).

---

## Stability at High Load (100 K / 100 TPS)

| Mechanism | Detail |
|-----------|--------|
| Fixed RX thread pool | 32 workers — no per-packet thread spawning |
| Bounded RX queue | `maxsize=2000`; overflow counted in `rx_dropped` |
| EAGAIN retry | `_send()` retries 3× with 2 ms / 4 ms backoff |
| Dedicated TX socket | Load-test sender uses separate socket from RX |
| Pending dict cap | Hard cap 200 000 entries; sweep after burst |
| Rotating log | 10 MB × 5 files — console silent during burst |

---

## Project Structure

```
ipsmgw-ue-simulator/
├── ipsmgw_ue_simulator.py   # Main script — stdlib only, no hardcoded customer values
├── ipsmgw_ue_simulator.ini             # Sample config — copy and edit per customer/environment
└── README.md
```

---

## Standards References

| Standard | Topic |
|----------|-------|
| 3GPP TS 24.341 | IP-SM-GW; UE-side SIP procedures |
| 3GPP TS 24.011 | SM-RL — RP-PDU formats |
| 3GPP TS 23.040 | SMS TPDU formats |
| RFC 3261 | SIP: Session Initiation Protocol |
| RFC 3680 | SIP Event Package for Registrations |


