"""Serial transport, passive rate sampling, and atomic profile storage."""
import json
import os
import time
from pathlib import Path
import serial
from serial.tools import list_ports

BAUDS = [300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
DEFAULTS = dict(port='', baudrate=115200, bytesize=8, parity='N', stopbits=1.0,
                flow='None', dtr=True, rts=True, ending='CR', local_echo=False, hex_view=False)


def ports():
    return sorted(list_ports.comports(), key=lambda p: (not ('USB' in p.device or 'ACM' in p.device), p.device))


def open_port(settings):
    # Set control lines before opening, where the driver supports it.
    s = serial.serial_for_url(settings['port'], do_not_open=True)
    s.baudrate = int(settings['baudrate'])
    s.bytesize = int(settings['bytesize'])
    s.parity = settings['parity']
    s.stopbits = float(settings['stopbits'])
    s.xonxoff = settings['flow'] == 'XON/XOFF'
    s.rtscts = settings['flow'] == 'RTS/CTS'
    s.dsrdtr = settings['flow'] == 'DSR/DTR'
    s.timeout = 0
    s.write_timeout = 0.25
    if os.name == 'posix' and '://' not in settings['port']:
        s.exclusive = True
    s.dtr = settings.get('dtr', True)
    s.rts = settings.get('rts', True)
    try:
        s.open()
    except Exception:
        s.close()
        raise
    return s


def text_score(data):
    if not data:
        return 0.0
    # This is a readability heuristic, not electrical auto-baud detection.
    good = sum(32 <= b < 127 or b in (9, 10, 13, 27, 8) for b in data)
    return good / len(data)


def scan_rates(settings, cancelled, progress, seconds=1.4):
    results = []
    s = open_port(settings)
    try:
        for baud in BAUDS:
            if cancelled():
                break
            s.baudrate = baud
            s.reset_input_buffer()
            time.sleep(0.08)
            data = bytearray()
            end = time.monotonic() + seconds
            while time.monotonic() < end and not cancelled():
                data.extend(s.read(min(s.in_waiting, 8192)))
                time.sleep(0.02)
            result = dict(baud=baud, count=len(data), score=text_score(data),
                          preview=bytes(data[:100]).decode('ascii', errors='replace'))
            results.append(result)
            progress(result)
    finally:
        s.close()
    return sorted(results, key=lambda r: (r['score'] if r['count'] >= 16 else -1, min(r['count'], 256)), reverse=True)


def validate_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError('Profile settings must be an object')
    s = dict(DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS})
    if not isinstance(s['port'], str) or not isinstance(s['baudrate'], int) or not 0 < s['baudrate'] <= 4000000:
        raise ValueError('Invalid port or baud rate in profile')
    if s['bytesize'] not in (5, 6, 7, 8) or s['parity'] not in ('N', 'E', 'O', 'M', 'S'):
        raise ValueError('Invalid data bits or parity in profile')
    if s['stopbits'] not in (1, 1.5, 2) or s['flow'] not in ('None', 'RTS/CTS', 'XON/XOFF', 'DSR/DTR'):
        raise ValueError('Invalid stop bits or flow control in profile')
    if s['ending'] not in ('None', 'CR', 'LF', 'CRLF'):
        raise ValueError('Invalid line ending in profile')
    for key in ('dtr', 'rts', 'local_echo', 'hex_view'):
        if not isinstance(s[key], bool):
            raise ValueError('Invalid checkbox value in profile')
    return s


class ProfileStore:
    def __init__(self, path=None, load=True):
        self.path = Path(path) if path else Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'serial-desk' / 'profiles.json'
        self.profiles = {}
        self.last = dict(DEFAULTS)
        if load and self.path.exists():
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict) or not isinstance(raw.get('profiles', {}), dict):
                raise ValueError('Invalid profile file')
            self.profiles = {name: validate_settings(value) for name, value in raw.get('profiles', {}).items()}
            self.last = validate_settings(raw.get('last', {}))

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with open(temp, 'w') as f:
            json.dump(dict(profiles=self.profiles, last=self.last), f, indent=2)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
