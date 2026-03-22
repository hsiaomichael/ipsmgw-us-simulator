#!/usr/bin/env python3
"""
IP-SM-GW UE Simulator
Simulates an IMS UE sending MO SMS and SIP REGISTER toward an IP-SM-GW.
Implements 3GPP TS 24.341 / TS 24.011 over SIP/UDP.
Standard library only – no third-party packages required.

Usage:
    python3 ipsmgw_ue_simulator.py                        # uses ipsmgw_ue_simulator.ini
    python3 ipsmgw_ue_simulator.py --config my_lab.ini    # custom config file
"""

import socket
import logging
import logging.handlers
import binascii
import random
import time
import threading
import queue
import sys
import argparse
import configparser
import os

# ============================================================
#  CONFIGURATION LOADER
# ============================================================

def _load_config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(path):
        print(f'[ERROR] Config file not found: {path}')
        print(f'        Create one by copying ipsmgw_ue_simulator.ini and editing the values.')
        sys.exit(1)
    cfg.read(path)
    return cfg

def _get(cfg: configparser.ConfigParser, section: str, key: str, fallback: str = '') -> str:
    return cfg.get(section, key, fallback=fallback).strip()

# ── Parse CLI args first so we know the config path ──────────
_ap = argparse.ArgumentParser(description='IP-SM-GW UE Simulator')
_ap.add_argument('--config', default='ipsmgw_ue_simulator.ini',
                 metavar='FILE',
                 help='Path to customer config file (default: ipsmgw_ue_simulator.ini)')
_args = _ap.parse_args()

_cfg = _load_config(_args.config)

# ── Network ──────────────────────────────────────────────────
LOCAL_IP    = _get(_cfg, 'network', 'local_ip',    '127.0.0.1')
LOCAL_PORT  = int(_get(_cfg, 'network', 'local_port',  '5060'))
REMOTE_HOST = _get(_cfg, 'network', 'remote_host', '127.0.0.1')
REMOTE_PORT = int(_get(_cfg, 'network', 'remote_port', '5060'))
ORIG_HOST   = _get(_cfg, 'network', 'orig_host',   LOCAL_IP)
LOCAL_URI   = f'{LOCAL_IP}:{LOCAL_PORT}'

# ── Subscriber ───────────────────────────────────────────────
DEFAULT_MSISDN = _get(_cfg, 'subscriber', 'default_msisdn', '619305004412')
DEFAULT_IMSI   = _get(_cfg, 'subscriber', 'default_imsi',   '466924253444139')
DEFAULT_TEL    = _get(_cfg, 'subscriber', 'default_tel',    '619375725469')

# ── SMSC / IP-SM-GW ──────────────────────────────────────────
SMSC_GT           = _get(_cfg, 'smsc', 'smsc_gt',           '619332489454')
SMSC_MSISDN       = _get(_cfg, 'smsc', 'smsc_msisdn',       '619332489464')
SMSC_DOMAIN       = _get(_cfg, 'smsc', 'smsc_domain',       'smsc123-traffic.test.com')
SMSC_DOMAIN_AU    = _get(_cfg, 'smsc', 'smsc_domain_au',    SMSC_DOMAIN)
REGISTER_URI      = _get(_cfg, 'smsc', 'register_uri',      'smsc123.lab.mcoipsm.mnc002.mcc505.3gppnetwork.org')
REGISTER_FROM     = _get(_cfg, 'smsc', 'register_from',     'ipsmg-test.com:5060')
# TP-DA used in SMS-SUBMIT TPDU inside MO RP-DATA
TP_DA_MSISDN      = _get(_cfg, 'smsc', 'tp_da_msisdn',      '619310310411')
TP_DA_TOA         = int(_get(_cfg, 'smsc', 'tp_da_toa',     '0x91'), 16)  # default 0x91 = international

# ── IMS network identifiers ───────────────────────────────────
IMS_DOMAIN        = _get(_cfg, 'ims', 'ims_domain',         'ims.mnc092.mcc466.3gppnetwork.org')
REGISTER_TO_DOMAIN= _get(_cfg, 'ims', 'register_to_domain', 'ims.mnc002.mcc505.3gppnetwork.org')
AUTH_DOMAIN       = _get(_cfg, 'ims', 'auth_domain',        'ims.mnc015.mcc234.3gppnetwork.org')

# ── Charging / access-net ─────────────────────────────────────
ICID_PREFIX       = _get(_cfg, 'charging', 'icid_prefix',       'tessbg1108.ims.mnc092.mcc466')
ICID_GENERATED_AT = _get(_cfg, 'charging', 'icid_generated_at', 'tessbg1108.ims.mnc092.mcc466.3gppnetwork.org')
ORIG_IOI          = _get(_cfg, 'charging', 'orig_ioi',           'ims.mnc092.mcc466.3gppnetwork.org')
SUBSCRIBE_ICID    = _get(_cfg, 'charging', 'subscribe_icid',     '')
SUBSCRIBE_PAN     = _get(_cfg, 'charging', 'subscribe_pan',      '')
ACCESS_NET_INFO   = _get(_cfg, 'charging', 'access_net_info',    'IEEE-802.11n;i-wlan-node-id=2034fbc343d2')

# ── Load test defaults ────────────────────────────────────────
LT_DEFAULT_DEST   = _get(_cfg, 'load_test', 'default_dest_msisdn', '619363540361')
LT_MO_COUNT       = int(_get(_cfg, 'load_test', 'mo_default_count',   '100'))
LT_MO_TPS         = float(_get(_cfg, 'load_test', 'mo_default_tps',   '10'))
LT_REG_COUNT      = int(_get(_cfg, 'load_test', 'reg_default_count',  '100'))
LT_REG_TPS        = float(_get(_cfg, 'load_test', 'reg_default_tps',  '10'))

# ── Tag / UA strings ──────────────────────────────────────────
DR_TAG_PREFIX     = _get(_cfg, 'tag', 'delivery_report_tag_prefix', 'ims.testcp.com-1111111111')
USER_AGENT        = _get(_cfg, 'tag', 'user_agent',                 'DTF_UA')

# Thread pool size for inbound message handling
RX_WORKERS    = 32

# ============================================================
#  LOGGING  –  rotating file (10 MB × 5), console=WARNING only
# ============================================================
log = logging.getLogger('ipsmgw')
log.setLevel(logging.DEBUG)

_fh = logging.handlers.RotatingFileHandler(
    'sip_server.log', maxBytes=10*1024*1024, backupCount=5)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
log.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
log.addHandler(_ch)

# ============================================================
#  COUNTERS
# ============================================================
_stats_lock = threading.Lock()
_stats = {
    'rx_total': 0, 'rx_request': 0, 'rx_response': 0, 'tx_total': 0,
    'rx_dropped': 0,
    'rx_OPTIONS': 0, 'rx_SUBSCRIBE': 0, 'rx_MESSAGE': 0,
    'rx_REGISTER': 0, 'rx_NOTIFY': 0,
    'tx_200OK': 0, 'tx_201OK': 0,
    'tx_REGISTER': 0, 'tx_MO': 0, 'tx_NOTIFY': 0, 'tx_DELIVERY': 0,
}

def _inc(key: str, n: int = 1):
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n

# ============================================================
#  CONSOLE PRETTY-PRINT  –  compact single-line format
#  Each SIP event = exactly ONE line on the console.
#  Full AVP detail always goes to the rotating log file.
# ============================================================
RESET  = '\033[0m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
CYAN   = '\033[96m'
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
BLUE   = '\033[94m'
MAGENTA = '\033[95m'

_print_lock = threading.Lock()

def _p(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)

# ── legacy block-style helpers (still used by menu/stats/load-test output) ──

def _banner(title: str, color: str = CYAN):
    bar = '─' * 58
    _p(f'\n{color}{BOLD}┌{bar}┐')
    _p(f'│  {title:<56}│')
    _p(f'└{bar}┘{RESET}')

def _field(label: str, value: str, color: str = RESET):
    _p(f'  {DIM}{label:<24}{RESET}{color}{value}{RESET}')

def _sep():
    _p(f'  {DIM}{"─"*54}{RESET}')

def _ok(msg: str):   _p(f'  {GREEN}✔  {msg}{RESET}')
def _err(msg: str):  _p(f'  {RED}✘  {msg}{RESET}')
def _info(msg: str): _p(f'  {YELLOW}ℹ  {msg}{RESET}')

# ── microsecond timestamp ────────────────────────────────────

def _ts() -> str:
    """Return current time as HH:MM:SS.ffffff  (6-digit microseconds)."""
    import datetime
    return datetime.datetime.now().strftime('%H:%M:%S.%f')

# ── compact 1-line console helpers ──────────────────────────

# Column widths for alignment
_COL_DIR  = 2    # ← / →
_COL_METH = 12   # METHOD or STATUS
_COL_ADDR = 21   # host:port
_COL_ID   = 32   # Call-ID (truncated)
_COL_CSEQ = 14   # CSeq

def _oneline_rx_req(method: str, addr: tuple, h: dict,
                    rp_info: dict = None, color: str = CYAN):
    """Print one console line for an inbound SIP request."""
    ts      = _ts()
    peer    = f'{addr[0]}:{addr[1]}'
    call_id = h.get('call-id', '')[:32]
    cseq    = h.get('cseq', '')
    from_v  = h.get('from', '')
    to_v    = h.get('to', '')
    # Extract MSISDNs for compact display
    from_n  = _extract_msisdn(from_v)
    to_n    = _extract_msisdn(to_v)
    num_part = ''
    if from_n:
        num_part += f' src=+{from_n}'
    if to_n:
        num_part += f' dst=+{to_n}'
    rp_part = ''
    if rp_info:
        mti = rp_info.get('RP-MTI', '')
        ref = rp_info.get('RP-Ref', '')
        oa  = rp_info.get('RP-OA', '')
        rp_part = f' [{mti} ref={ref} oa={oa}]' if (mti or ref or oa) else ''
    line = (f'{color}{BOLD}{ts}{RESET} {color}←{RESET} '
            f'{color}{BOLD}{method:<10}{RESET} '
            f'{DIM}{peer:<22}{RESET}'
            f'{DIM}cseq={cseq:<12}{RESET}'
            f'{DIM}id={call_id}{RESET}'
            f'{YELLOW}{num_part}{RESET}'
            f'{YELLOW}{rp_part}{RESET}')
    _p(line)

def _oneline_rx_resp(code: str, txt: str, addr: tuple, h: dict, color: str = GREEN):
    """Print one console line for an inbound SIP response."""
    ts      = _ts()
    peer    = f'{addr[0]}:{addr[1]}'
    call_id = h.get('call-id', '')[:32]
    cseq    = h.get('cseq', '')
    line = (f'{color}{BOLD}{ts}{RESET} {color}←{RESET} '
            f'{color}{BOLD}{code} {txt:<7}{RESET} '
            f'{DIM}{peer:<22}{RESET}'
            f'{DIM}cseq={cseq:<12}{RESET}'
            f'{DIM}id={call_id}{RESET}')
    _p(line)

def _oneline_tx(method: str, addr: tuple, call_id: str, cseq: str,
                extra: str = '', color: str = BLUE):
    """Print one console line for an outbound SIP message."""
    ts      = _ts()
    peer    = f'{addr[0]}:{addr[1]}'
    cid     = call_id[:32]
    line = (f'{color}{BOLD}{ts}{RESET} {color}→{RESET} '
            f'{color}{BOLD}{method:<10}{RESET} '
            f'{DIM}{peer:<22}{RESET}'
            f'{DIM}cseq={cseq:<12}{RESET}'
            f'{DIM}id={cid}{RESET}'
            f'{GREEN}{(" " + extra) if extra else ""}{RESET}')
    _p(line)

# ── rich AVP log to file ─────────────────────────────────────

def _log_rx_req(method: str, addr: tuple, h: dict,
                rp_info: dict = None, body: bytes = None):
    """Write full AVP detail for an inbound request to the log file."""
    ts   = _ts()
    peer = f'{addr[0]}:{addr[1]}'
    lines = [
        f'--- RX REQUEST  {ts}  {method}  from={peer} ---',
        f'  From            : {h.get("from","")}',
        f'  To              : {h.get("to","")}',
        f'  Call-ID         : {h.get("call-id","")}',
        f'  CSeq            : {h.get("cseq","")}',
        f'  Via             : {h.get("via","")}',
        f'  Contact         : {h.get("contact","")}',
        f'  Max-Forwards    : {h.get("max-forwards","")}',
        f'  Content-Type    : {h.get("content-type","")}',
        f'  Content-Length  : {h.get("content-length","")}',
        f'  Record-Route    : {h.get("record-route","")}',
        f'  Route           : {h.get("route","")}',
        f'  Allow           : {h.get("allow","")}',
        f'  Supported       : {h.get("supported","")}',
        f'  User-Agent      : {h.get("user-agent","")}',
        f'  P-Asserted-ID   : {h.get("p-asserted-identity","")}',
        f'  P-Access-Net    : {h.get("p-access-network-info","")}',
        f'  P-Charging-Vec  : {h.get("p-charging-vector","")}',
        f'  In-Reply-To     : {h.get("in-reply-to","")}',
        f'  Event           : {h.get("event","")}',
        f'  Subscription    : {h.get("subscription-state","")}',
        f'  Expires         : {h.get("expires","")}',
        f'  Authorization   : {h.get("authorization","")}',
        f'  WWW-Auth        : {h.get("www-authenticate","")}',
    ]
    # dump any extra headers not already listed
    known = {
        'from','to','call-id','cseq','via','contact','max-forwards',
        'content-type','content-length','record-route','route','allow',
        'supported','user-agent','p-asserted-identity','p-access-network-info',
        'p-charging-vector','in-reply-to','event','subscription-state',
        'expires','authorization','www-authenticate',
    }
    for k, v in h.items():
        if k not in known:
            lines.append(f'  {k:<16}: {v}')
    if rp_info:
        lines.append('  [3GPP 24.011 RP PDU]')
        for k, v in rp_info.items():
            lines.append(f'    {k:<16}: {v}')
    if body:
        lines.append(f'  Body (hex)      : {" ".join(f"{b:02x}" for b in body[:64])}'
                     f'{"..." if len(body) > 64 else ""}')
    log.debug('\n'.join(lines))

def _log_rx_resp(code: str, txt: str, addr: tuple, h: dict):
    """Write full AVP detail for an inbound response to the log file."""
    ts   = _ts()
    peer = f'{addr[0]}:{addr[1]}'
    lines = [
        f'--- RX RESPONSE  {ts}  {code} {txt}  from={peer} ---',
        f'  From            : {h.get("from","")}',
        f'  To              : {h.get("to","")}',
        f'  Call-ID         : {h.get("call-id","")}',
        f'  CSeq            : {h.get("cseq","")}',
        f'  Via             : {h.get("via","")}',
        f'  Contact         : {h.get("contact","")}',
        f'  Content-Type    : {h.get("content-type","")}',
        f'  Content-Length  : {h.get("content-length","")}',
        f'  Record-Route    : {h.get("record-route","")}',
        f'  Allow           : {h.get("allow","")}',
        f'  Supported       : {h.get("supported","")}',
        f'  Server          : {h.get("server","")}',
        f'  P-Asserted-ID   : {h.get("p-asserted-identity","")}',
        f'  P-Charging-Vec  : {h.get("p-charging-vector","")}',
        f'  Expires         : {h.get("expires","")}',
        f'  Reason          : {h.get("reason","")}',
        f'  Warning         : {h.get("warning","")}',
        f'  Retry-After     : {h.get("retry-after","")}',
    ]
    known_r = {
        'from','to','call-id','cseq','via','contact','content-type',
        'content-length','record-route','allow','supported','server',
        'p-asserted-identity','p-charging-vector','expires','reason',
        'warning','retry-after',
    }
    for k, v in h.items():
        if k not in known_r:
            lines.append(f'  {k:<16}: {v}')
    log.debug('\n'.join(lines))

def _log_tx(method: str, addr: tuple, call_id: str, cseq: str,
            msg_bytes: bytes, extra_avps: dict = None):
    """Write full AVP detail for an outbound message to the log file."""
    ts   = _ts()
    peer = f'{addr[0]}:{addr[1]}'
    # Parse the headers we're about to send for complete logging
    lines = [
        f'--- TX {method}  {ts}  to={peer} ---',
        f'  Call-ID         : {call_id}',
        f'  CSeq            : {cseq}',
    ]
    if extra_avps:
        for k, v in extra_avps.items():
            lines.append(f'  {k:<16}: {v}')
    # Hex-dump first 128 bytes of body if present
    sep_pos = msg_bytes.find(b'\r\n\r\n')
    if sep_pos >= 0:
        raw_hdrs = msg_bytes[:sep_pos].decode('utf-8', errors='replace')
        body     = msg_bytes[sep_pos+4:]
        # Extract key headers from raw text
        for line in raw_hdrs.split('\r\n')[1:]:  # skip request line
            if line:
                lines.append(f'  {line}')
        if body:
            lines.append(f'  Body (hex)      : {" ".join(f"{b:02x}" for b in body[:64])}'
                         f'{"..." if len(body) > 64 else ""}')
    log.debug('\n'.join(lines))

# ── MSISDN helpers ───────────────────────────────────────────

def _extract_msisdn(s: str) -> str:
    """
    Extract a valid E.164 MSISDN (6-15 digits) from a SIP/tel URI or header.
    Stops at URI delimiters (@, ;, >, :port) so tag= or port numbers
    are never included.  Returns '' if no plausible number found.
    """
    import re
    # Match digits immediately after sip:+, sip:, or tel:+
    # and stop at the first non-digit URI delimiter
    for pattern in (
        r'(?:sip|tel):\+(\d{6,15})(?=[@;:>\s]|$)',   # with leading +
        r'(?:sip|tel):(\d{6,15})(?=[@;:>\s]|$)',      # without leading +
    ):
        m = re.search(pattern, s, re.IGNORECASE)
        if m:
            return m.group(1)
    return ''

def _fmt_msisdn(raw: str) -> str:
    """Format an MSISDN string as +CCC-NXX-SUBSCRIBER."""
    n = _extract_msisdn(raw) if (raw.startswith('sip') or raw.startswith('tel')
                                  or raw.startswith('<')) else raw.strip('+')
    n = n or raw
    if len(n) >= 10:
        return f'+{n[:3]}-{n[3:6]}-{n[6:]}'
    return f'+{n}' if n else raw

# ============================================================
#  SOCKET
# ============================================================
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((LOCAL_IP, LOCAL_PORT))
sock.settimeout(1.0)
stop_event = threading.Event()

# Dedicated send socket for load test (avoids RX/TX contention on same fd)
_send_sock      = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_send_sock_lock = threading.Lock()

def _send(data: bytes, host: str = REMOTE_HOST, port: int = REMOTE_PORT,
          _load: bool = False):
    """
    Send a UDP datagram.  Retries up to 3 times on EAGAIN (send buffer full).
    Uses dedicated send socket during load test to keep RX path clear.
    """
    target = _send_sock if _load else sock
    lock   = _send_sock_lock if _load else _send_sock_lock
    for attempt in range(3):
        try:
            with lock:
                target.sendto(data, (host, port))
            _inc('tx_total')
            log.debug(f'TX {len(data)}B to {host}:{port}')
            return
        except BlockingIOError:
            if attempt < 2:
                time.sleep(0.002 * (attempt + 1))
            else:
                log.warning(f'_send: dropped {len(data)}B to {host}:{port} (EAGAIN x3)')
        except OSError as e:
            log.warning(f'_send: OSError {e}')
            return

def _send_text(msg: str, host: str = REMOTE_HOST, port: int = REMOTE_PORT,
               _load: bool = False):
    _send(msg.encode('utf-8'), host, port, _load)

def _uniq(n: int = 13) -> str:
    return ''.join(str(random.randint(0, 9)) for _ in range(n))

# ============================================================
#  SIP PARSER
# ============================================================

def parse_sip(data: bytes) -> dict:
    result = {'request_line': '', 'method': None, 'status': None,
              'status_text': '', 'headers': {}, 'body': b''}
    try:
        if b'\r\n\r\n' in data:
            head_b, body = data.split(b'\r\n\r\n', 1)
        else:
            head_b, body = data, b''

        head_str = head_b.decode('utf-8', errors='replace')
        lines    = head_str.split('\r\n')
        result['request_line'] = lines[0].strip()

        first = result['request_line']
        if first.startswith('SIP/2.0'):
            parts = first.split(' ', 2)
            result['status']      = parts[1] if len(parts) > 1 else ''
            result['status_text'] = parts[2] if len(parts) > 2 else ''
        else:
            result['method'] = first.split(' ', 1)[0]

        hdrs: dict = {}
        for line in lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                k_l, v_s = k.strip().lower(), v.strip()
                if k_l in hdrs:
                    all_key = k_l + '_all'
                    hdrs.setdefault(all_key, [hdrs[k_l]])
                    hdrs[all_key].append(v_s)
                else:
                    hdrs[k_l] = v_s
        result['headers'] = hdrs

        cl = int(hdrs.get('content-length', 0))
        result['body'] = data[-cl:] if cl > 0 else body
    except Exception as exc:
        log.error(f'parse_sip: {exc}')
    return result

# ============================================================
#  RESPONSE BUILDER
# ============================================================

def _resp(parsed: dict, status: str = '200 OK', extra: str = '') -> str:
    h = parsed['headers']
    return (f'SIP/2.0 {status}\r\n'
            f'Via: {h.get("via","")}\r\n'
            f'To: {h.get("to","")}\r\n'
            f'From: {h.get("from","")}\r\n'
            f'Call-ID: {h.get("call-id","")}\r\n'
            f'CSeq: {h.get("cseq","")}\r\n'
            f'{extra}'
            f'Content-Length: 0\r\n\r\n')

# ============================================================
#  GSM 24.011 CODEC
# ============================================================

_RP_MTI  = {0:'RP-DATA(MO)',1:'RP-DATA(MT)',2:'RP-ACK(MO)',3:'RP-ACK(MT)',
             4:'RP-ERROR(MO)',5:'RP-ERROR(MT)',6:'RP-SMMA'}
_TP_MTI  = {0:'SMS-DELIVER',1:'SMS-SUBMIT',2:'SMS-COMMAND'}

def _bcd_decode(raw: bytes, digit_count: int) -> str:
    out = ''
    for b in raw:
        out += str(b & 0x0F) + str((b >> 4) & 0x0F)
    return out[:digit_count]

def _addr_decode(raw: bytes) -> str:
    """BCD address: raw[0]=TON/NPI, rest=semi-octets."""
    if not raw:
        return '(empty)'
    digits = _bcd_decode(raw[1:], (len(raw)-1)*2)
    return digits.rstrip('F').rstrip('f')

def _encode_address(digits: str) -> bytes:
    if len(digits) % 2:
        digits += 'F'
    pairs = [digits[i:i+2] for i in range(0, len(digits), 2)]
    bcd   = bytes([int(p[1]+p[0], 16) for p in pairs])
    return bytes([0x91]) + bcd   # TON/NPI international

def decode_rp_data(body: bytes) -> tuple:
    """
    Decode a 3GPP 24.011 RP PDU.
    Returns (rp_ref:int, info:dict).
    Handles all MTI types gracefully – only RP-DATA (MTI 0/1) has OA/DA/UD fields.
    """
    info, rp_ref = {}, 0
    if not body:
        return rp_ref, info
    try:
        idx    = 0
        mti    = body[idx]; idx += 1
        rp_ref = body[idx]; idx += 1
        mti_name = _RP_MTI.get(mti, f'?{mti}')
        info['RP-MTI'] = f'{mti_name}  (0x{mti:02x})'
        info['RP-Ref'] = f'{rp_ref}  (0x{rp_ref:02x})'

        # ── RP-DATA MO (0) or MT (1) ─────────────────────────
        if mti in (0, 1):
            oa_len = body[idx]; idx += 1
            oa_raw = body[idx:idx+oa_len]; idx += oa_len
            info['RP-OA'] = _addr_decode(oa_raw) or '(none)'

            da_len = body[idx]; idx += 1
            da_raw = body[idx:idx+da_len]; idx += da_len
            info['RP-DA SMSC'] = f'+{_addr_decode(da_raw)}'

            ud_len = body[idx]; idx += 1
            ud_raw = body[idx:idx+ud_len]; idx += ud_len
            info['RP-UD len'] = str(ud_len)

            # SMS TPDU
            if ud_len:
                tpdu   = ud_raw
                tp_mti = tpdu[0] & 0x03
                info['TPDU type'] = f'{_TP_MTI.get(tp_mti, f"?{tp_mti}")}  (0x{tp_mti:02x})'
                flags = []
                if tpdu[0] & 0x04:  flags.append('MRD')
                if tpdu[0] & 0x20:  flags.append('SRI')
                if tpdu[0] & 0x40:  flags.append('UDHI')
                if tpdu[0] & 0x80:  flags.append('RP')
                info['TPDU flags'] = ' '.join(flags) if flags else 'none'
                t = 1
                if t < len(tpdu):
                    info['TP-MR'] = str(tpdu[t]); t += 1
                if t + 1 < len(tpdu):
                    da_dlen = tpdu[t]; t += 1
                    _tonpi  = tpdu[t]; t += 1
                    da_blen = (da_dlen + 1) // 2
                    da_bcd  = tpdu[t:t+da_blen]; t += da_blen
                    dest_num = _bcd_decode(da_bcd, da_dlen)
                    info['TP-DA (Dest)'] = _fmt_msisdn(dest_num)

        # ── RP-ACK MO (2) or MT (3) ──────────────────────────
        elif mti in (2, 3):
            # optional RP-User-Data element
            if idx < len(body):
                ei  = body[idx]; idx += 1
                eil = body[idx] if idx < len(body) else 0; idx += 1
                info['RP-ACK element'] = f'IE=0x{ei:02x} len={eil}'

        # ── RP-ERROR MO (4) or MT (5) ────────────────────────
        elif mti in (4, 5):
            if idx < len(body):
                cause_len = body[idx]; idx += 1
                cause_val = body[idx] if idx < len(body) else 0
                info['RP-Cause'] = f'{cause_val}  (0x{cause_val:02x})'

    except Exception as exc:
        log.debug(f'decode_rp_data note: {exc}')   # downgraded to DEBUG
    return rp_ref, info

def build_rp_ack(rp_ref: int) -> bytes:
    """
    RP-ACK MO (MTI=2) – success acknowledgment.
    Mirrors Lua: msg:set_param('24.011.mti', 2)
    Structure: MTI=0x02, RP-REF, IE=0x41 (RP-User-Data), len=0x00
    """
    pdu = bytes([0x02, rp_ref & 0xFF, 0x41, 0x00])
    log.debug(f'RP-ACK PDU: {" ".join(f"{b:02x}" for b in pdu)}')
    return pdu

def build_rp_error(rp_ref: int, cause: int = 0x29) -> bytes:
    """
    RP-ERROR MO (MTI=4) – failure report.
    Mirrors Lua: msg:set_param('24.011.mti', 4); cause=41 (0x29 temporary failure)
    Structure: MTI=0x04, RP-REF, cause-len=0x01, cause, pad=0x00
    """
    pdu = bytes([0x04, rp_ref & 0xFF, 0x01, cause & 0xFF, 0x00])
    log.debug(f'RP-ERROR PDU: {" ".join(f"{b:02x}" for b in pdu)}')
    return pdu

# ============================================================
#  OUTBOUND: DELIVERY REPORT  (mirrors Lua ack())
# ============================================================

def send_sip_delivery_report(parsed: dict):
    """
    Send an RP-ACK(MO) delivery report back to the IP-SM-GW.
    Called only when inbound body is RP-DATA(MO) MTI=0.

    SIP dialog rules (RFC 3261 §12):
      - Request-URI  : peer's Via address (their From URI, no tag)
      - From         : our address  = inbound To  (bare, no old tag) + new tag
      - To           : their address= inbound From (bare, no tag)
      - Call-ID      : unchanged
    """
    h       = parsed['headers']
    rp_ref, rp_info = decode_rp_data(parsed['body'])
    ack_pdu = build_rp_ack(rp_ref)   # RP-ACK MO MTI=2
    uniq    = _uniq(13)

    # ── inbound From (THEM) → our To, strip existing tag ──────────
    their_from  = h.get('from', '')
    their_addr  = their_from.split(';tag=')[0].rstrip()   # bare address, no tag
    # extract URI from angle brackets for Request-URI
    if '<' in their_addr:
        req_uri = their_addr.split('<')[1].split('>')[0]
    else:
        req_uri = their_addr

    # ── inbound To (US) → our From, strip any existing tag ────────
    our_to      = h.get('to', '')
    our_addr    = our_to.split(';tag=')[0].rstrip()       # bare address, no tag
    from_hdr    = f'{our_addr};tag={DR_TAG_PREFIX}-{uniq}'

    call_id     = h.get('call-id', '')
    in_reply_to = h.get('in-reply-to', '')

    # ── Via: extract peer host:port for routing ────────────────────
    via_hdr  = h.get('via', '')
    # e.g. SIP/2.0/UDP 10.158.42.75:5060;branch=...
    peer_host = REMOTE_HOST
    peer_port = REMOTE_PORT
    import re as _re
    vm = _re.search(r'SIP/2\.0/UDP\s+([\d.]+):(\d+)', via_hdr, _re.IGNORECASE)
    if vm:
        peer_host = vm.group(1)
        peer_port = int(vm.group(2))

    extra = f'In-Reply-To: {in_reply_to}\r\n' if in_reply_to else ''

    msg = (f'MESSAGE {req_uri} SIP/2.0\r\n'
           f'Via: SIP/2.0/UDP {LOCAL_URI};branch=z1hG4bKhjhs8ass877{uniq}\r\n'
           f'From: {from_hdr}\r\n'
           f'To: {their_addr}\r\n'
           f'CSeq: 120 MESSAGE\r\n'
           f'Call-ID: {call_id}\r\n'
           f'{extra}'
           f'Content-Type: application/vnd.3gpp.sms\r\n'
           f'Content-Length: {len(ack_pdu)}\r\n\r\n')

    _oneline_tx('MESSAGE', (peer_host, peer_port), call_id, '120 MESSAGE',
                extra=f'RP-ACK ref={rp_ref} [{" ".join(f"{b:02x}" for b in ack_pdu)}]',
                color=BLUE)
    _log_tx('MESSAGE', (peer_host, peer_port), call_id, '120 MESSAGE',
            msg.encode('utf-8') + ack_pdu,
            extra_avps={
                'Request-URI':  req_uri,
                'From':         from_hdr,
                'To':           their_addr,
                'RP-Ref':       str(rp_ref),
                'RP PDU type':  'RP-ACK (MO) MTI=2 – delivery success',
                'PDU (hex)':    ' '.join(f'{b:02x}' for b in ack_pdu),
            })

    _send(msg.encode('utf-8') + ack_pdu, peer_host, peer_port)
    _inc('tx_DELIVERY')

# ============================================================
#  OUTBOUND: NOTIFY  (mirrors Lua notify())
# ============================================================

def send_notify(parsed: dict):
    h   = parsed['headers']
    rl  = parsed.get('request_line', '')
    uri = rl.split(' ')[1] if ' ' in rl else ''

    if 'sip:+' in uri:
        msisdn = uri.split('sip:+')[1].split('@')[0][:11]
    elif 'sip:' in uri:
        msisdn = uri.split('sip:')[1].split('@')[0][:11]
    else:
        msisdn = SMSC_MSISDN

    uniq  = _uniq(5)
    data  = (f'<?xml version="1.0"?>\n'
             f'<reginfo xmlns="urn:ietf:params:xml:ns:reginfo" '
             f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             f'version="0" state="full">\n'
             f'  <registration aor="sip:+{msisdn}@{SMSC_DOMAIN}" '
             f'id="771982793" state="active">\n'
             f'    <contact id="2680011900" state="active" event="registered" '
             f'duration-registered="618533" expires="61" q="1" '
             f'callid="2427774398@{ORIG_HOST}" cseq="2">\n'
             f'      <unknown-param name="+g.3gpp.accesstype">"cellular"</unknown-param>\n'
             f'      <unknown-param name="+g.3gpp.icsi-ref">"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"</unknown-param>\n'
             f'    </contact>\n  </registration>\n'
             f'  <registration aor="tel:+{msisdn}" id="2625922963" state="active">\n'
             f'    <contact id="2680011900" state="active" event="registered" '
             f'duration-registered="618533" expires="61" q="1" '
             f'callid="2427774398@{ORIG_HOST}" cseq="2">\n'
             f'      <unknown-param name="+g.3gpp.accesstype">"cellular"</unknown-param>\n'
             f'      <unknown-param name="+g.3gpp.icsi-ref">"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"</unknown-param>\n'
             f'    </contact>\n  </registration>\n</reginfo>\n')
    data_b = data.encode('utf-8')

    msg = (f'NOTIFY sip:+{SMSC_MSISDN}@{SMSC_DOMAIN_AU};user=phone SIP/2.0\r\n'
           f'Via: SIP/2.0/UDP {LOCAL_URI};branch=z9hG4bKhjhs8ass877{uniq}-not-0001-{uniq}01\r\n'
           f'From: <sip:{msisdn}@{SMSC_DOMAIN}>;tag=h7g4Esbg_{uniq}-{msisdn}\r\n'
           f'To: <sip:+{SMSC_MSISDN}@{SMSC_DOMAIN_AU}>\r\n'
           f'CSeq: 1 NOTIFY\r\n'
           f'Call-ID: abcdef{msisdn}\r\n'
           f'User-Agent: CscfUacAgent\r\n'
           f'Event: reg\r\n'
           f'Max-Forwards: 70\r\n'
           f'Subscription-State: active;expires=60\r\n'
           f'Contact: sip:{LOCAL_URI}\r\n'
           f'Content-Type: Application/reginfo+xml\r\n'
           f'Content-Length: {len(data_b)}\r\n\r\n')

    _oneline_tx('NOTIFY', (REMOTE_HOST, REMOTE_PORT),
                f'abcdef{msisdn}', '1 NOTIFY',
                extra=f'reg-event msisdn=+{msisdn}', color=BLUE)
    _log_tx('NOTIFY', (REMOTE_HOST, REMOTE_PORT),
            f'abcdef{msisdn}', '1 NOTIFY',
            msg.encode('utf-8') + data_b)

    _send(msg.encode('utf-8') + data_b)
    _inc('tx_NOTIFY')

# ============================================================
#  OUTBOUND: MO SMS  (mirrors Lua sip_mo())
# ============================================================

def _build_sms_submit_tpdu(dest_digits: str) -> bytes:
    """
    Build a minimal SMS-SUBMIT TPDU addressed to dest_digits.

    Structure:
      11        – MTI=SMS-SUBMIT, VPF=relative, no TP-SRR
      2d        – TP-MR (message reference)
      [DA len]  – number of TP-DA digits
      [TOA]     – Type-of-Address from config  (TP_DA_TOA)
      [BCD]     – TP-DA semi-octet BCD
      00        – TP-PID  (implicit, no special protocol)
      00        – TP-DCS  (GSM7 default alphabet)
      10        – TP-VP   (relative, 24 h)
      0c        – TP-UDL  (12 septets → "hello world" in GSM7)
      d0f7bd2c4fbbcfa0b7d90c – TP-UD
    """
    digits = dest_digits.lstrip('+')
    # Pad to even length for semi-octet encoding
    raw = digits if len(digits) % 2 == 0 else digits + 'F'
    pairs = [raw[i:i+2] for i in range(0, len(raw), 2)]
    tp_da_bcd = bytes([int(p[1] + p[0], 16) for p in pairs])
    da_len    = len(digits)                          # digit count (not byte count)
    tp_da     = bytes([da_len, TP_DA_TOA]) + tp_da_bcd

    tp_ud  = bytes.fromhex('d0f7bd2c4fbbcfa0b7d90c')  # "hello world" GSM7
    tpdu   = (bytes([0x11, 0x2d])          # MTI + TP-MR
              + tp_da                       # TP-DA
              + bytes([0x00, 0x00, 0x10,    # TP-PID, TP-DCS, TP-VP
                       len(tp_ud)])         # TP-UDL
              + tp_ud)                      # TP-UD
    return tpdu


def send_sip_mo(orig_addr: str = None, tel_addr: str = None):
    d  = [random.randint(0, 9) for _ in range(5)]
    ds = ''.join(map(str, d))

    if not orig_addr:
        orig_addr = f'{DEFAULT_MSISDN[:7]}{ds}'
    if not tel_addr:
        tel_addr  = DEFAULT_TEL

    uniq      = _uniq(5)
    ud_bytes  = _build_sms_submit_tpdu(tel_addr)

    da_bcd   = _encode_address(SMSC_GT)
    rp_data  = bytes([0x00, 0x00, 0x00])          # MTI=0, ref=0, OA-len=0
    rp_data += bytes([len(da_bcd)]) + da_bcd       # RP-DA
    rp_data += bytes([len(ud_bytes)]) + ud_bytes   # RP-UD

    msg_hdr = (f'MESSAGE sip:+{SMSC_MSISDN}@{ORIG_HOST};user=phone SIP/2.0\r\n'
               f'Via: SIP/2.0/UDP {LOCAL_IP}:{LOCAL_PORT};branch=z9hG4bKhjhs8ass877{uniq}\r\n'
               f'From: <sip:+{orig_addr}@{ORIG_HOST}>;tag=h7g4Esbg_5446725095152f-5b1fb{orig_addr}\r\n'
               f'To: <sip:+{SMSC_MSISDN}@{SMSC_DOMAIN};user=phone>\r\n'
               f'CSeq: 63104 MESSAGE\r\n'
               f'Call-ID: abcdef{uniq}\r\n'
               f'Content-Type: application/vnd.3gpp.sms\r\n'
               f'Content-Transfer-Encoding: binary\r\n'
               f'P-Asserted-Identity: sip:+{orig_addr}@{IMS_DOMAIN}\r\n'
               f'P-Asserted-Identity: tel:+{tel_addr}\r\n'
               f'P-Access-Network-Info: {ACCESS_NET_INFO}\r\n'
               f'P-Charging-Vector: icid-value={ICID_PREFIX}.-1628-240749-677186-616;'
               f'icid-generated-at={ICID_GENERATED_AT};'
               f'orig-ioi={ORIG_IOI}\r\n'
               f'Max-Forwards: 70\r\n'
               f'Content-Length: {len(rp_data)}\r\n\r\n')

    _oneline_tx('MESSAGE', (REMOTE_HOST, REMOTE_PORT),
                f'abcdef{uniq}', '63104 MESSAGE',
                extra=(f'MO-SMS src=+{orig_addr} smsc=+{SMSC_GT} '
                       f'da=+{TP_DA_MSISDN} toa=0x{TP_DA_TOA:02x} rp={len(rp_data)}B'),
                color=BLUE)
    _log_tx('MESSAGE', (REMOTE_HOST, REMOTE_PORT),
            f'abcdef{uniq}', '63104 MESSAGE',
            msg_hdr.encode('utf-8') + rp_data,
            extra_avps={
                'Orig MSISDN':  _fmt_msisdn(orig_addr),
                'Tel / iWatch': _fmt_msisdn(tel_addr),
                'SMSC Address': f'+{SMSC_GT}',
                'TP-DA MSISDN': f'+{TP_DA_MSISDN}',
                'TP-DA TOA':    f'0x{TP_DA_TOA:02x}  (TON={(TP_DA_TOA>>4)&7} NPI={TP_DA_TOA&15})',
                'RP-DATA size': f'{len(rp_data)} bytes',
            })

    _send(msg_hdr.encode('utf-8') + rp_data)
    _inc('tx_MO')

# ============================================================
#  OUTBOUND: REGISTER  (mirrors Lua register())
# ============================================================

def send_register(msisdn: str = None, expires: int = 300):
    if not msisdn:
        msisdn = DEFAULT_MSISDN
    imsi = DEFAULT_IMSI
    uniq = _uniq(5)
    imei = f'15050{msisdn}'

    sip_data = (
        "--0499967417387699603\r\n"
        f'authorization: Digest username="{imsi}@{AUTH_DOMAIN}",'
        f'algorithm=AKAv1-MD5\r\n'
        f"    REGISTER sip:{REGISTER_URI} SIP/2.0\r\n"
        f"    via: SIP/2.0/ :;branch=z9hG4bK-ue-{uniq}\r\n"
        f"    from: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;"
        f"tag=ue-reg-{uniq}\r\n"
        f"    to: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>\r\n"
        "    cseq: 1 REGISTER\r\n"
        f"    call-id: reg-{imsi}@{LOCAL_IP}\r\n"
        "    max-forwards: 15\r\n"
        f"    user-agent: {USER_AGENT}\r\n\r\n"
        f"    p-charging-vector: icid-value=P-CSCF:{uniq};"
        f" icid-generated-at={LOCAL_IP}; orig-ioi={ORIG_IOI}\r\n"
        f'    contact: <sip:{imsi}@{LOCAL_IP}:{LOCAL_PORT};transport=udp>;'
        f'+sip.instance="<urn:gsma:imei:{imei}>";q=1.0;'
        f'+g.3gpp.icsi-ref="urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel";'
        f'+g.3gpp.accesstype="cellular"\r\n'
        "    expires: 60\r\n"
        "--0499967417387699603\r\n"
        "    SIP/2.0 200 OK\r\n"
        f"    from: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;"
        f"tag=ue-reg-{uniq}\r\n"
        f"    to: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;tag=1\r\n"
        f"    via: SIP/2.0/ :;branch=z9hG4bK-ue-{uniq}\r\n"
        "    cseq: 1 REGISTER\r\n"
        f"    call-id: reg-{imsi}@{LOCAL_IP}\r\n"
        "--0499967417387699603--\r\n"
    )
    sip_data_b = sip_data.encode('utf-8')

    msg = (f'REGISTER sip:{REGISTER_URI};'
           f'call=orig;lr;msisdn={SMSC_MSISDN} SIP/2.0\r\n'
           f'Via: SIP/2.0/UDP {LOCAL_URI};branch=z9hG4bKhjhs8ass877{uniq}\r\n'
           f'From: <sip:{REGISTER_FROM}>;tag=h7g4Esbg_5446725095152f-5b1fcd7b\r\n'
           f'To: <sip:+{msisdn}@{REGISTER_TO_DOMAIN}>\r\n'
           f'CSeq: 63104 REGISTER\r\n'
           f'Call-ID: abcdef{uniq}\r\n'
           f'Content-Type: application/3gpp-ims+xml\r\n'
           f'Expires: {expires}\r\n'
           f'Contact: sip:{LOCAL_IP}:{LOCAL_PORT}\r\n'
           f'Content-Length: {len(sip_data_b)}\r\n\r\n')

    _oneline_tx('REGISTER', (REMOTE_HOST, REMOTE_PORT),
                f'abcdef{uniq}', '63104 REGISTER',
                extra=f'msisdn=+{msisdn} expires={expires}s imsi={imsi}',
                color=BLUE)
    _log_tx('REGISTER', (REMOTE_HOST, REMOTE_PORT),
            f'abcdef{uniq}', '63104 REGISTER',
            msg.encode('utf-8') + sip_data_b,
            extra_avps={
                'MSISDN':   _fmt_msisdn(msisdn),
                'IMSI':     imsi,
                'IMEI':     imei,
                'Expires':  f'{expires} s',
            })

    _send(msg.encode('utf-8') + sip_data_b)
    _inc('tx_REGISTER')

# ============================================================
#  INBOUND HANDLER
# ============================================================

def handle_message(data: bytes, addr: tuple):
    _inc('rx_total')
    parsed = parse_sip(data)
    h      = parsed['headers']

    # ── Response ────────────────────────────────────────────
    if parsed['status']:
        _inc('rx_response')
        code  = parsed['status']
        txt   = parsed['status_text']

        # Load test latency tracking (always, even during normal use)
        call_id_hdr = h.get('call-id', '')
        _lt_record_response(call_id_hdr, code)

        with _lt_lock:
            lt_running = _lt_results['running']
        if lt_running:
            return

        color = GREEN if code.startswith('2') else (YELLOW if code.startswith('1') else RED)
        _oneline_rx_resp(code, txt, addr, h, color)
        _log_rx_resp(code, txt, addr, h)
        return

    # ── Request ─────────────────────────────────────────────
    _inc('rx_request')
    method = parsed.get('method') or 'UNKNOWN'
    _inc(f'rx_{method}')

    with _lt_lock:
        lt_running = _lt_results['running']

    # ── RP PDU decode for MESSAGE ──────────────────────────
    rp_mti  = -1
    rp_ref  = 0
    rp_info = {}
    if method == 'MESSAGE' and parsed['body']:
        rp_ref, rp_info = decode_rp_data(parsed['body'])
        rp_mti = parsed['body'][0] if parsed['body'] else -1

    # ── Single-line console output (always, even during load test for non-burst) ──
    if not lt_running:
        _oneline_rx_req(method, addr, h,
                        rp_info=(rp_info if rp_info else None),
                        color=CYAN)
        _log_rx_req(method, addr, h, rp_info=rp_info or None,
                    body=parsed['body'] if method == 'MESSAGE' else None)

    if method == 'OPTIONS':
        resp = _resp(parsed, '201 OK')
        _send_text(resp, addr[0], addr[1])
        _inc('tx_201OK')
        if not lt_running:
            _oneline_tx('201 OK', (addr[0], addr[1]),
                        h.get('call-id',''), h.get('cseq',''),
                        extra='OPTIONS reply', color=GREEN)
            _log_tx('201 OK', (addr[0], addr[1]),
                    h.get('call-id',''), h.get('cseq',''),
                    resp.encode('utf-8'))

    elif method == 'SUBSCRIBE':
        extra = (f'P-Charging-Vector: icid-value={SUBSCRIBE_ICID}\r\n'
                 f'P-Access-Network-Info: {SUBSCRIBE_PAN}\r\n')
        resp = _resp(parsed, '200 OK', extra)
        _send_text(resp)
        _inc('tx_200OK')
        if not lt_running:
            _oneline_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                        h.get('call-id',''), h.get('cseq',''),
                        extra='SUBSCRIBE reply + NOTIFY', color=GREEN)
            _log_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                    h.get('call-id',''), h.get('cseq',''),
                    resp.encode('utf-8'))
        send_notify(parsed)

    elif method == 'MESSAGE':
        resp = _resp(parsed, '200 OK')
        if rp_mti in (0, 1):
            label = 'MO-fwd' if rp_mti == 0 else 'MT-dlv'
            _send_text(resp)
            _inc('tx_200OK')
            if not lt_running:
                _oneline_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                            h.get('call-id',''), h.get('cseq',''),
                            extra=f'MESSAGE reply, RP-ACK in 1s [{label}]',
                            color=GREEN)
                _log_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                        h.get('call-id',''), h.get('cseq',''),
                        resp.encode('utf-8'))
            def _deferred():
                time.sleep(1)
                send_sip_delivery_report(parsed)
            threading.Thread(target=_deferred, daemon=True).start()
        else:
            mti_name = _RP_MTI.get(rp_mti, f'MTI={rp_mti}')
            _send_text(resp)
            _inc('tx_200OK')
            if not lt_running:
                _oneline_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                            h.get('call-id',''), h.get('cseq',''),
                            extra=f'MESSAGE reply [{mti_name}]', color=GREEN)
                _log_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                        h.get('call-id',''), h.get('cseq',''),
                        resp.encode('utf-8'))

    elif method in ('REGISTER', 'NOTIFY'):
        resp = _resp(parsed, '200 OK')
        _send_text(resp)
        _inc('tx_200OK')
        if not lt_running:
            _oneline_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                        h.get('call-id',''), h.get('cseq',''),
                        extra=f'{method} reply', color=GREEN)
            _log_tx('200 OK', (REMOTE_HOST, REMOTE_PORT),
                    h.get('call-id',''), h.get('cseq',''),
                    resp.encode('utf-8'))

    else:
        if not lt_running:
            _info(f'Unhandled SIP method: {method} – no response sent')

# ============================================================
#  LOAD TEST ENGINE
# ============================================================

# Pending map: call_id → send_timestamp  (used to measure round-trip latency)
_pending_lock = threading.Lock()
_pending: dict = {}          # call_id → float (time.monotonic at send)

# Active load test state (reset each run)
_lt_lock    = threading.Lock()
_lt_results = {
    'sent':      0,
    'success':   0,    # received 2xx response
    'error':     0,    # received 4xx/5xx or timeout
    'timeout':   0,
    'latencies': [],   # list of float (seconds)
    'start_ts':  0.0,
    'end_ts':    0.0,
    'running':   False,
    'target':    0,
    'test_type': '',   # 'MO' or 'REGISTER'
    'label':     '',   # short description stored in history
}

# Persistent history of completed runs (current session, newest first)
_lt_history: list = []

def _lt_reset(target: int, test_type: str = 'MO', label: str = ''):
    with _lt_lock:
        _lt_results.update({
            'sent': 0, 'success': 0, 'error': 0, 'timeout': 0,
            'latencies': [], 'start_ts': time.monotonic(),
            'end_ts': 0.0, 'running': True, 'target': target,
            'test_type': test_type, 'label': label,
        })
    with _pending_lock:
        _pending.clear()

def _lt_record_response(call_id: str, status_code: str):
    """Called from handle_message for any SIP response during a load test."""
    now = time.monotonic()
    with _pending_lock:
        sent_at = _pending.pop(call_id, None)
    if sent_at is None:
        return
    latency = now - sent_at
    success = status_code.startswith('2')
    with _lt_lock:
        if success:
            _lt_results['success'] += 1
        else:
            _lt_results['error']   += 1
        _lt_results['latencies'].append(latency)

def _lt_expire_timeouts(timeout_s: float = 5.0):
    """Sweep pending map and count expired entries as timeouts."""
    now = time.monotonic()
    expired = []
    with _pending_lock:
        for cid, ts in list(_pending.items()):
            if now - ts > timeout_s:
                expired.append(cid)
        for cid in expired:
            del _pending[cid]
    with _lt_lock:
        _lt_results['timeout'] += len(expired)
        _lt_results['error']   += len(expired)

def _send_mo_load(orig_addr: str, tel_addr: str, dest_msisdn: str, seq: int) -> str:
    """
    Send one MO SMS for load test. Returns the Call-ID used.
    dest_msisdn : B-party number (recipient of the SMS).
    Suppresses all console output – only increments counters.
    """
    uniq      = f'{seq:08d}{_uniq(5)}'
    da_bcd    = _encode_address(SMSC_GT)

    # Build SMS-SUBMIT TPDU using configured TP-DA MSISDN and TOA
    ud_bytes  = _build_sms_submit_tpdu(dest_msisdn)

    rp_data   = bytes([0x00, 0x00, 0x00]) + bytes([len(da_bcd)]) + da_bcd
    rp_data  += bytes([len(ud_bytes)]) + ud_bytes

    call_id   = f'lt-{seq:08d}-{uniq}'
    branch    = f'z9hG4bKlt{uniq}'

    msg_hdr = (f'MESSAGE sip:+{SMSC_MSISDN}@{ORIG_HOST};user=phone SIP/2.0\r\n'
               f'Via: SIP/2.0/UDP {LOCAL_IP}:{LOCAL_PORT};branch={branch}\r\n'
               f'From: <sip:+{orig_addr}@{ORIG_HOST}>;tag=lt-{uniq}\r\n'
               f'To: <sip:+{SMSC_MSISDN}@{SMSC_DOMAIN};user=phone>\r\n'
               f'CSeq: 1 MESSAGE\r\n'
               f'Call-ID: {call_id}\r\n'
               f'Content-Type: application/vnd.3gpp.sms\r\n'
               f'Content-Transfer-Encoding: binary\r\n'
               f'P-Asserted-Identity: sip:+{orig_addr}@{IMS_DOMAIN}\r\n'
               f'P-Asserted-Identity: tel:+{tel_addr}\r\n'
               f'P-Access-Network-Info: {ACCESS_NET_INFO}\r\n'
               f'P-Charging-Vector: icid-value=lt{uniq}.{ICID_PREFIX};'
               f'icid-generated-at=lt{uniq}.{ICID_GENERATED_AT};'
               f'orig-ioi={ORIG_IOI}\r\n'
               f'Max-Forwards: 70\r\n'
               f'Content-Length: {len(rp_data)}\r\n\r\n')

    with _pending_lock:
        # Cap pending dict to avoid unbounded growth if responses stop arriving
        if len(_pending) < 200_000:
            _pending[call_id] = time.monotonic()

    _send(msg_hdr.encode('utf-8') + rp_data, _load=True)
    _inc('tx_MO')
    with _lt_lock:
        _lt_results['sent'] += 1

    return call_id

def _lt_progress_bar(done: int, total: int, width: int = 30) -> str:
    pct   = done / total if total else 0
    filled = int(width * pct)
    bar   = '█' * filled + '░' * (width - filled)
    return f'[{bar}] {done}/{total} ({pct*100:.1f}%)'

def _lt_live_stats() -> str:
    with _lt_lock:
        s = dict(_lt_results)
    elapsed = time.monotonic() - s['start_ts']
    tps     = s['sent'] / elapsed if elapsed > 0 else 0
    lats    = s['latencies']
    avg_ms  = (sum(lats) / len(lats) * 1000) if lats else 0
    return (f"sent={s['sent']}  2xx={s['success']}  "
            f"err={s['error']}  tmo={s['timeout']}  "
            f"tps={tps:.1f}  avg={avg_ms:.0f}ms")

def _lt_snapshot() -> dict:
    """Return a deep-frozen copy of current _lt_results (call with lock held or after run)."""
    with _lt_lock:
        snap = dict(_lt_results)
        snap['latencies'] = list(snap['latencies'])
    return snap

def _lt_save_history(snap: dict):
    """Prepend snapshot to the in-memory history list (keep last 20 runs)."""
    _lt_history.insert(0, snap)
    if len(_lt_history) > 20:
        _lt_history.pop()

def _lt_final_report(snap: dict):
    """Print the load test result table from a frozen snapshot dict."""
    target  = snap['target']
    lats    = snap['latencies']
    elapsed = (snap['end_ts'] - snap['start_ts']) if snap['end_ts'] else 0
    tps     = snap['sent'] / elapsed if elapsed > 0 else 0
    test_type = snap.get('test_type', 'MO')
    label     = snap.get('label', '')

    def pct(n): return f'{n/target*100:.1f}%' if target else '0%'

    _banner(f'Load Test Results  [{test_type}]  {label}', YELLOW)
    _field('Target messages',  str(target))
    _field('Sent',             str(snap['sent']))
    _field('Success (2xx)',    f"{snap['success']}  ({pct(snap['success'])})")
    _field('Error (4xx/5xx)',  f"{snap['error'] - snap['timeout']}  ({pct(snap['error'] - snap['timeout'])})")
    _field('Timeout (>5s)',    f"{snap['timeout']}  ({pct(snap['timeout'])})")
    _sep()
    _field('Elapsed time',     f'{elapsed:.2f} s')
    _field('Throughput',       f'{tps:.2f} msg/s')
    _sep()
    if lats:
        lats_sorted = sorted(lats)
        n = len(lats_sorted)
        _field('Latency min',  f'{min(lats)*1000:.1f} ms')
        _field('Latency avg',  f'{sum(lats)/n*1000:.1f} ms')
        _field('Latency p50',  f'{lats_sorted[int(n*0.50)]*1000:.1f} ms')
        _field('Latency p90',  f'{lats_sorted[int(n*0.90)]*1000:.1f} ms')
        _field('Latency p99',  f'{lats_sorted[min(int(n*0.99), n-1)]*1000:.1f} ms')
        _field('Latency max',  f'{max(lats)*1000:.1f} ms')
    else:
        _info('No responses received – check connectivity')
    _p()

def run_load_test(count: int, tps: float, orig_base: str,
                  tel_addr: str, dest_base: str):
    """
    Send `count` MO SMS messages at `tps` messages/second.
    orig_base : A-party MSISDN prefix – last 5 digits rotate per message
    tel_addr  : P-Asserted-Identity tel URI (iWatch / secondary number)
    dest_base : B-party MSISDN prefix – last 5 digits rotate per message
    """
    label = f'{count} msgs @ {tps:.1f} TPS  orig={orig_base[:7]}XXXXX'
    _lt_reset(count, test_type='MO', label=label)
    interval   = 1.0 / tps if tps > 0 else 0
    timeout_s  = 5.0
    update_every = max(1, count // 20)

    _banner(f'Load Test  –  {count} msgs @ {tps:.1f} msg/s  [MO SMS]', YELLOW)
    _field('Orig MSISDN (A)',   f'{orig_base[:7]}XXXXX  (last 5 digits rotate)')
    _field('Dest MSISDN (B)',   f'{dest_base[:7]}XXXXX  (last 5 digits rotate)')
    _field('Tel MSISDN (PAI)',  _fmt_msisdn(tel_addr))
    _field('Target TPS',        f'{tps:.1f}')
    _field('Timeout / msg',     f'{timeout_s:.0f} s')
    _field('Progress update',   f'every {update_every} msg')
    _p()

    next_send = time.monotonic()
    for seq in range(1, count + 1):
        now = time.monotonic()
        if now < next_send:
            time.sleep(next_send - now)
        next_send += interval

        suffix = f'{seq % 100000:05d}'
        orig   = orig_base[:7]  + suffix if len(orig_base)  >= 7 else orig_base
        dest   = dest_base[:7]  + suffix if len(dest_base)  >= 7 else dest_base
        _send_mo_load(orig, tel_addr, dest, seq)

        if seq % update_every == 0 or seq == count:
            with _print_lock:
                print(f'\r  {_lt_progress_bar(seq, count)}  {_lt_live_stats()}',
                      end='', flush=True)

    _p()
    _info(f'All {count} messages sent – waiting up to {timeout_s:.0f}s for responses…')

    deadline = time.monotonic() + timeout_s + 1
    while time.monotonic() < deadline:
        with _pending_lock:
            remaining = len(_pending)
        if remaining == 0:
            break
        time.sleep(0.1)

    _lt_expire_timeouts(timeout_s)

    with _lt_lock:
        _lt_results['end_ts']  = time.monotonic()
        _lt_results['running'] = False

    snap = _lt_snapshot()
    _lt_save_history(snap)
    _lt_final_report(snap)


# ============================================================
#  REGISTER LOAD TEST ENGINE
# ============================================================

def _send_register_load(msisdn_base: str, seq: int) -> str:
    """
    Send one SIP REGISTER for load test. Returns the Call-ID used.
    msisdn_base : MSISDN prefix – last 5 digits rotate per sequence number.
    Suppresses all console output – only increments counters.
    """
    uniq    = f'{seq:08d}{_uniq(5)}'
    suffix  = f'{seq % 100000:05d}'
    msisdn  = (msisdn_base[:7] + suffix) if len(msisdn_base) >= 7 else msisdn_base
    imsi    = DEFAULT_IMSI
    imei    = f'15050{msisdn}'
    call_id = f'ltreg-{seq:08d}-{uniq}'

    sip_data = (
        "--0499967417387699603\r\n"
        f'authorization: Digest username="{imsi}@{AUTH_DOMAIN}",'
        f'algorithm=AKAv1-MD5\r\n'
        f"    REGISTER sip:{REGISTER_URI} SIP/2.0\r\n"
        "    via: SIP/2.0/ :;branch=z9hG4bK-lt-reg\r\n"
        f'    from: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;'
        f'tag=ltreg-{uniq}\r\n'
        f'    to: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>\r\n'
        "    cseq: 1 REGISTER\r\n"
        f'    call-id: {call_id}\r\n'
        "    max-forwards: 15\r\n"
        f"    user-agent: {USER_AGENT}\r\n\r\n"
        "    p-charging-vector: icid-value=P-CSCF:ltreg;icid-generated-at=192.0.6.8;"
        " orig-ioi=dtf.net\r\n"
        f'    contact: <sip:{imsi}@{LOCAL_IP}:{LOCAL_PORT};transport=udp>;'
        f'+sip.instance="<urn:gsma:imei:{imei}>";q=1.0\r\n'
        "    expires: 300\r\n"
        "--0499967417387699603\r\n"
        "    SIP/2.0 200 OK\r\n"
        f'    from: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;'
        f'tag=ltreg-{uniq}\r\n'
        f'    to: <sip:{msisdn}@{SMSC_DOMAIN};user=phone>;tag=1\r\n'
        "    via: SIP/2.0/ :;branch=z9hG4bK-lt-reg\r\n"
        "    cseq: 1 REGISTER\r\n"
        f'    call-id: {call_id}\r\n'
        "--0499967417387699603--\r\n"
    )
    sip_data_b = sip_data.encode('utf-8')

    msg = (f'REGISTER sip:{REGISTER_URI};'
           f'call=orig;lr;msisdn={msisdn} SIP/2.0\r\n'
           f'Via: SIP/2.0/UDP {LOCAL_URI};branch=z9hG4bKltreg{uniq}\r\n'
           f'From: <sip:{REGISTER_FROM}>;tag=ltreg-{uniq}\r\n'
           f'To: <sip:+{msisdn}@{REGISTER_TO_DOMAIN}>\r\n'
           f'CSeq: 1 REGISTER\r\n'
           f'Call-ID: {call_id}\r\n'
           f'Content-Type: application/3gpp-ims+xml\r\n'
           f'Expires: 300\r\n'
           f'Contact: sip:{LOCAL_IP}:{LOCAL_PORT}\r\n'
           f'Content-Length: {len(sip_data_b)}\r\n\r\n')

    with _pending_lock:
        if len(_pending) < 200_000:
            _pending[call_id] = time.monotonic()

    _send(msg.encode('utf-8') + sip_data_b, _load=True)
    _inc('tx_REGISTER')
    with _lt_lock:
        _lt_results['sent'] += 1

    return call_id


def run_register_load_test(count: int, tps: float, msisdn_base: str):
    """
    Send `count` SIP REGISTER messages at `tps` messages/second.
    msisdn_base : MSISDN prefix – last 5 digits rotate per message.
    """
    label = f'{count} REGISTERs @ {tps:.1f} TPS  msisdn={msisdn_base[:7]}XXXXX'
    _lt_reset(count, test_type='REGISTER', label=label)
    interval     = 1.0 / tps if tps > 0 else 0
    timeout_s    = 5.0
    update_every = max(1, count // 20)

    _banner(f'Load Test  –  {count} msgs @ {tps:.1f} msg/s  [REGISTER]', YELLOW)
    _field('MSISDN base',    f'{msisdn_base[:7]}XXXXX  (last 5 digits rotate)')
    _field('Target TPS',     f'{tps:.1f}')
    _field('Timeout / msg',  f'{timeout_s:.0f} s')
    _field('Progress update', f'every {update_every} msg')
    _p()

    next_send = time.monotonic()
    for seq in range(1, count + 1):
        now = time.monotonic()
        if now < next_send:
            time.sleep(next_send - now)
        next_send += interval

        _send_register_load(msisdn_base, seq)

        if seq % update_every == 0 or seq == count:
            with _print_lock:
                print(f'\r  {_lt_progress_bar(seq, count)}  {_lt_live_stats()}',
                      end='', flush=True)

    _p()
    _info(f'All {count} REGISTERs sent – waiting up to {timeout_s:.0f}s for responses…')

    deadline = time.monotonic() + timeout_s + 1
    while time.monotonic() < deadline:
        with _pending_lock:
            remaining = len(_pending)
        if remaining == 0:
            break
        time.sleep(0.1)

    _lt_expire_timeouts(timeout_s)

    with _lt_lock:
        _lt_results['end_ts']  = time.monotonic()
        _lt_results['running'] = False

    snap = _lt_snapshot()
    _lt_save_history(snap)
    _lt_final_report(snap)



# ============================================================
#  RECEIVE LOOP  –  fixed thread pool (no unbounded thread spawning)
# ============================================================

_rx_queue: queue.Queue = queue.Queue(maxsize=2000)  # backpressure limit

def _rx_worker():
    """One worker in the fixed RX thread pool."""
    while True:
        item = _rx_queue.get()
        if item is None:
            break
        data, addr = item
        try:
            handle_message(data, addr)
        except Exception as exc:
            log.error(f'rx_worker exception: {exc}')
        finally:
            _rx_queue.task_done()

def receive_loop():
    """
    Single receiver thread: reads UDP datagrams and enqueues them.
    Actual processing happens in the fixed RX worker pool.
    """
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            break
        log.debug(f'RX {len(data)}B from {addr}')
        try:
            _rx_queue.put_nowait((data, addr))
        except queue.Full:
            log.warning('RX queue full – packet dropped (backpressure)')
            _inc('rx_dropped')

# ============================================================
#  MENU UI
# ============================================================

def _show_conn_info():
    _banner('Connection Parameters', CYAN)
    _field('Local  IP',        LOCAL_IP)
    _field('Local  Port',      str(LOCAL_PORT))
    _field('Remote IP',        REMOTE_HOST)
    _field('Remote Port',      str(REMOTE_PORT))
    _field('Protocol',         'SIP / UDP')
    _sep()
    _field('Default MSISDN',   _fmt_msisdn(DEFAULT_MSISDN))
    _field('Default IMSI',     DEFAULT_IMSI)
    _field('Default Tel',      _fmt_msisdn(DEFAULT_TEL))
    _p()

def _show_stats():
    _banner('Message Statistics', YELLOW)
    with _stats_lock:
        s = dict(_stats)
    _field('RX Total',          str(s['rx_total']))
    _field('  Requests',        str(s['rx_request']))
    _field('  Responses',       str(s['rx_response']))
    _field('  Dropped (full)',   str(s.get('rx_dropped', 0)))
    _sep()
    for m in ('OPTIONS', 'SUBSCRIBE', 'MESSAGE', 'REGISTER', 'NOTIFY'):
        v = s.get(f'rx_{m}', 0)
        _field(f'  RX {m:<10}', str(v))
    _sep()
    _field('TX Total',          str(s['tx_total']))
    _field('  REGISTER sent',   str(s['tx_REGISTER']))
    _field('  MO SMS sent',     str(s['tx_MO']))
    _field('  NOTIFY sent',     str(s['tx_NOTIFY']))
    _field('  Delivery Rpt',    str(s['tx_DELIVERY']))
    _field('  200 OK sent',     str(s['tx_200OK']))
    _field('  201 OK sent',     str(s['tx_201OK']))
    _p()

def _menu_register():
    _banner('Send SIP REGISTER', BLUE)
    _field('Default MSISDN',  _fmt_msisdn(DEFAULT_MSISDN))
    _field('Default Expires', '300 s')
    _p()
    try:
        raw_msisdn = input(f'  MSISDN      [{DEFAULT_MSISDN}] : ').strip()
        raw_exp    = input(f'  Expires (s) [300]           : ').strip()
    except (EOFError, KeyboardInterrupt):
        return
    msisdn  = raw_msisdn  if raw_msisdn  else DEFAULT_MSISDN
    expires = int(raw_exp) if raw_exp.isdigit() else 300
    send_register(msisdn, expires)

def _menu_mo():
    _banner('Send SIP MO SMS', BLUE)
    _field('Default Orig MSISDN', _fmt_msisdn(DEFAULT_MSISDN))
    _field('Default Tel  MSISDN', _fmt_msisdn(DEFAULT_TEL))
    _p()
    try:
        raw_orig = input(f'  Orig MSISDN [{DEFAULT_MSISDN}] : ').strip()
        raw_tel  = input(f'  Tel  MSISDN [{DEFAULT_TEL}]  : ').strip()
    except (EOFError, KeyboardInterrupt):
        return
    orig = raw_orig if raw_orig else DEFAULT_MSISDN
    tel  = raw_tel  if raw_tel  else DEFAULT_TEL
    send_sip_mo(orig, tel)

def _menu_load_test_mo():
    _banner('Load Test – MO SMS Burst', YELLOW)
    _field('A-party (Orig)',    _fmt_msisdn(DEFAULT_MSISDN) + '  (last 5 digits rotate)')
    _field('B-party (Dest)',    _fmt_msisdn(LT_DEFAULT_DEST) + '  (last 5 digits rotate)')
    _field('Tel / PAI',        _fmt_msisdn(DEFAULT_TEL))
    _p()
    try:
        raw_orig  = input(f'  Orig MSISDN base [{DEFAULT_MSISDN}]  : ').strip()
        raw_dest  = input(f'  Dest MSISDN (B)  [{LT_DEFAULT_DEST}]  : ').strip()
        raw_tel   = input(f'  Tel  MSISDN PAI  [{DEFAULT_TEL}]  : ').strip()
        raw_count = input(f'  Number of msgs   [{LT_MO_COUNT}]              : ').strip()
        raw_tps   = input(f'  Target TPS       [{LT_MO_TPS}]               : ').strip()
    except (EOFError, KeyboardInterrupt):
        return

    orig  = raw_orig  if raw_orig  else DEFAULT_MSISDN
    dest  = raw_dest  if raw_dest  else LT_DEFAULT_DEST
    tel   = raw_tel   if raw_tel   else DEFAULT_TEL
    count = int(raw_count) if raw_count.isdigit() else LT_MO_COUNT
    try:
        tps = float(raw_tps) if raw_tps else LT_MO_TPS
    except ValueError:
        tps = LT_MO_TPS

    t = threading.Thread(target=run_load_test,
                         args=(count, tps, orig, tel, dest), daemon=True)
    t.start()
    t.join()


def _menu_load_test_register():
    _banner('Load Test – SIP REGISTER Burst', YELLOW)
    _field('MSISDN base',   _fmt_msisdn(DEFAULT_MSISDN) + '  (last 5 digits rotate)')
    _p()
    try:
        raw_msisdn = input(f'  MSISDN base      [{DEFAULT_MSISDN}]  : ').strip()
        raw_count  = input(f'  Number of msgs   [{LT_REG_COUNT}]              : ').strip()
        raw_tps    = input(f'  Target TPS       [{LT_REG_TPS}]               : ').strip()
    except (EOFError, KeyboardInterrupt):
        return

    msisdn = raw_msisdn if raw_msisdn else DEFAULT_MSISDN
    count  = int(raw_count) if raw_count.isdigit() else LT_REG_COUNT
    try:
        tps = float(raw_tps) if raw_tps else LT_REG_TPS
    except ValueError:
        tps = LT_REG_TPS

    t = threading.Thread(target=run_register_load_test,
                         args=(count, tps, msisdn), daemon=True)
    t.start()
    t.join()


def _menu_load_test_results():
    """Display load test history and let user pick a run to view in detail."""
    if not _lt_history:
        _banner('Load Test Results', YELLOW)
        _info('No load test runs recorded in this session yet.')
        _p()
        return

    while True:
        _banner(f'Load Test History  ({len(_lt_history)} run(s))', YELLOW)
        for i, snap in enumerate(_lt_history):
            idx      = i + 1
            tt       = snap.get('test_type', '??')
            lbl      = snap.get('label', '')
            elapsed  = (snap['end_ts'] - snap['start_ts']) if snap['end_ts'] else 0
            succ_pct = (snap['success'] / snap['target'] * 100) if snap['target'] else 0
            tps_val  = snap['sent'] / elapsed if elapsed > 0 else 0
            lat_lbl  = ''
            if snap['latencies']:
                avg_ms = sum(snap['latencies']) / len(snap['latencies']) * 1000
                lat_lbl = f'  avg={avg_ms:.0f}ms'
            _p(f"  {BOLD}{idx}{RESET}  [{tt}]  {lbl}")
            _p(f"      sent={snap['sent']}  2xx={snap['success']} ({succ_pct:.1f}%)"
               f"  tmo={snap['timeout']}  tps={tps_val:.1f}{lat_lbl}")
        _p()
        _p(f'  {BOLD}0{RESET}  Back to main menu')
        _p()
        try:
            sel = input(f'{BOLD}Select run # (or 0 to go back) > {RESET}').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if sel == '0' or sel == '':
            break
        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(_lt_history):
                _lt_final_report(_lt_history[idx - 1])
                try:
                    input('  Press Enter to continue…')
                except (EOFError, KeyboardInterrupt):
                    pass
            else:
                _err(f'Invalid selection: {sel}')
        else:
            _err(f'Invalid input: {sel!r}')


def _print_menu():
    _p(f'\n{BOLD}{CYAN}╔══════════════════════════════════════════╗')
    _p(f'║       IP-SM-GW SIP Server  v2.1          ║')
    _p(f'╚══════════════════════════════════════════╝{RESET}')
    _p(f'  {DIM}Local  {LOCAL_IP}:{LOCAL_PORT}  ↔  '
       f'Remote  {REMOTE_HOST}:{REMOTE_PORT}{RESET}')
    with _stats_lock:
        rx = _stats['rx_total']
        tx = _stats['tx_total']
    runs = len(_lt_history)
    _p(f'  {DIM}RX: {rx}   TX: {tx}   Load test runs: {runs}{RESET}\n')
    _p(f'  {BOLD}1{RESET}  Send REGISTER')
    _p(f'  {BOLD}2{RESET}  Send MO SMS')
    _p(f'  {BOLD}3{RESET}  Message statistics')
    _p(f'  {BOLD}4{RESET}  Connection info')
    _p(f'  {BOLD}5{RESET}  Load test  –  MO SMS burst')
    _p(f'  {BOLD}6{RESET}  Load test  –  SIP REGISTER burst')
    _p(f'  {BOLD}7{RESET}  Load test results  ({runs} run(s))')
    _p(f'  {BOLD}0{RESET}  Quit\n')


def run_menu():
    _show_conn_info()
    _p(f'{GREEN}{BOLD}Server started – listening on {LOCAL_IP}:{LOCAL_PORT} (UDP){RESET}\n')

    while not stop_event.is_set():
        _print_menu()
        try:
            choice = input(f'{BOLD}Choice > {RESET}').strip()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == '1':
            _menu_register()
        elif choice == '2':
            _menu_mo()
        elif choice == '3':
            _show_stats()
        elif choice == '4':
            _show_conn_info()
        elif choice == '5':
            _menu_load_test_mo()
        elif choice == '6':
            _menu_load_test_register()
        elif choice == '7':
            _menu_load_test_results()
        elif choice == '0':
            break
        else:
            _err(f'Unknown option: {choice!r}')

    stop_event.set()

# ============================================================
#  ENTRY POINT
# ============================================================
if __name__ == '__main__':
    # Start fixed RX worker pool
    _rx_workers = []
    for _ in range(RX_WORKERS):
        w = threading.Thread(target=_rx_worker, daemon=True)
        w.start()
        _rx_workers.append(w)

    rx_thread = threading.Thread(target=receive_loop, daemon=True)
    rx_thread.start()

    try:
        run_menu()
    finally:
        stop_event.set()
        # Signal all workers to exit
        for _ in _rx_workers:
            _rx_queue.put(None)
        sock.close()
        _send_sock.close()
        _p(f'\n{YELLOW}Server stopped.{RESET}')
        log.info('Server stopped.')
