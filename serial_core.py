"""Serial transport, bounded prompt-assisted rate sampling, and profile storage."""
import json
import os
import time
import re
import math
from pathlib import Path
from platform_support import config_directory
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


def response_content(data):
    data=re.sub(rb'\x1b\][^\x07]*(?:\x07|\x1b\\)',b'',bytes(data))
    return re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',data)


def scan_rates(settings, cancelled, progress, seconds=1.4,probe_count=0,probe_interval=.35,probe=b'\r',status=None):
    """Listen first; optionally request a prompt with spaced line endings only."""
    if not isinstance(probe_count,int) or not 0<=probe_count<=5:raise ValueError('Use zero through five Enter probes.')
    if probe not in (b'\r',b'\n',b'\r\n'):raise ValueError('Only CR, LF or CRLF probes are supported.')
    if not math.isfinite(seconds) or not .01<=seconds<=10 or not math.isfinite(probe_interval) or not .01<=probe_interval<=3:raise ValueError('Invalid scan timing.')
    results = []
    s = open_port(settings)
    try:
        for baud in BAUDS:
            if cancelled():break
            if status:status(f'Testing {baud:,} baud • listening first…')
            if probe_count:s.reset_output_buffer()
            try:s.baudrate = baud
            except (ValueError,serial.SerialException) as error:
                result=dict(baud=baud,count=0,score=0,content_count=0,candidate=False,probes=0,preview='Unsupported rate: '+str(error));results.append(result);progress(result);continue
            s.reset_input_buffer()
            settle=time.monotonic()+.08
            while time.monotonic()<settle and not cancelled():time.sleep(.01)
            if cancelled():break
            data = bytearray()
            def collect(duration):
                end=time.monotonic()+duration
                while time.monotonic()<end and not cancelled():
                    chunk=s.read(min(s.in_waiting,8192));data.extend(chunk[:max(0,65536-len(data))]);time.sleep(.02)
            def readable():return sum(33<=b<127 for b in response_content(data))>=3 and text_score(data)>=.85
            sent=0
            if probe_count:
                collect(min(.25,seconds))
                for index in range(probe_count):
                    if cancelled() or readable():break
                    if status:status(f'Testing {baud:,} baud • Enter {index+1}/{probe_count}…')
                    try:written=s.write(probe)
                    except serial.SerialTimeoutException as error:raise OSError('Enter probe timed out. Check flow control/CTS or retry in listen-only mode.') from error
                    if written!=len(probe):raise OSError('The complete Enter probe could not be sent.')
                    sent+=1
                    if index+1<probe_count:collect(probe_interval)
            collect(seconds)
            content=response_content(data);count=sum(33<=b<127 for b in content)
            result = dict(baud=baud,count=len(data),score=text_score(data),content_count=count,candidate=readable(),probes=sent,
                          preview=content[:160].decode('ascii',errors='replace'))
            results.append(result)
            progress(result)
            if cancelled():break
    finally:
        s.close()
    return sorted(results,key=lambda r:(r['candidate'],r['score'] if r['content_count']>=3 else -1,min(r['content_count'],256)),reverse=True)


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
        self.path = Path(path) if path else config_directory() / 'profiles.json'
        self.profiles = {}
        self.last = dict(DEFAULTS)
        if load and self.path.exists():
            raw = json.loads(self.path.read_text(encoding='utf-8'))
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
