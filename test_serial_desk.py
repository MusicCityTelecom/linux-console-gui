import json
import os
import stat
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if os.name != 'nt':
    import pty
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from serial_core import DEFAULTS, ProfileStore, open_port, text_score, scan_rates
from console_gui import SerialConsole, Terminal

APP = QApplication.instance() or QApplication([])


class SerialDeskTests(unittest.TestCase):
    def test_loopback_settings(self):
        for baud in (9600, 115200):
            with open_port(dict(DEFAULTS, port='loop://', baudrate=baud)) as s:
                s.write(b'hello\r\n\x00\xff')
                self.assertEqual(s.read(9), b'hello\r\n\x00\xff')
                self.assertEqual(s.baudrate, baud)

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'profiles.json'
            store = ProfileStore(path)
            store.profiles['Router'] = dict(DEFAULTS, port='/dev/ttyUSB0', baudrate=9600, parity='E')
            store.last = store.profiles['Router']; store.write()
            loaded = ProfileStore(path)
            self.assertEqual(loaded.profiles, store.profiles)
            self.assertEqual(loaded.last['parity'], 'E')
            if os.name!='nt': self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'profiles.json'
            path.write_text(json.dumps({'last': {'stopbits': 'invalid'}}))
            with self.assertRaises(ValueError):
                ProfileStore(path)

    def test_passive_scan_sends_nothing_and_closes(self):
        class FakeSerial:
            in_waiting = 40
            baudrate = 115200
            closed = False
            def reset_input_buffer(self): pass
            def read(self, n): return b'router console ready\r\n' if self.baudrate == 9600 else b'\xff\x00\x80' * 8
            def close(self): self.closed = True
        fake = FakeSerial()
        with patch('serial_core.open_port', return_value=fake), patch('serial_core.BAUDS', [4800, 9600]):
            results = scan_rates(DEFAULTS, lambda: False, lambda r: None, seconds=.03)
        self.assertEqual(results[0]['baud'], 9600)
        self.assertTrue(fake.closed)
        self.assertEqual(text_score(b''), 0)

    def test_terminal_crlf_and_ansi(self):
        t = Terminal()
        t.feed('hello\r\nworld\rX')
        self.assertEqual(t.toPlainText(), 'hello\nXorld')
        t.feed('\x1b['); t.feed('K')
        self.assertEqual(t.toPlainText(), 'hello\nX')
        t.feed('\x1b[2Jdone')
        self.assertEqual(t.toPlainText(), 'done')
        sent = []; t.outgoing.connect(sent.append)
        QTest.keyClick(t, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(sent, [b'\x03'])

    def test_terminal_screen_positioning_never_rewrites_scrollback(self):
        t = Terminal()
        t.feed('installer@host:~$ sudo apt install btop\r\nInstalling: btop\r\nDone\r\n')
        transcript=t.toPlainText()
        # Package managers commonly use these while painting transient progress
        # lines.  The compact transcript must keep the following prompt at end.
        t.feed('\x1b[H\x1b[3Ainstaller@host:~$ ')
        self.assertEqual(t.toPlainText(),transcript+'installer@host:~$ ')

    def test_terminal_horizontal_correction_stays_on_live_line(self):
        t=Terminal();t.feed('older history\r\nlive text')
        t.feed('\x1b[999D!')
        self.assertEqual(t.toPlainText(),'older history\n!ive text')

    def test_terminal_reports_usable_dimensions(self):
        t=Terminal();reported=[];t.dimensions_changed.connect(lambda columns,rows:reported.append((columns,rows)))
        t.resize(800,400);t.show();APP.processEvents()
        columns,rows=t.terminal_dimensions()
        self.assertGreaterEqual(columns,20);self.assertGreaterEqual(rows,4);self.assertIn((columns,rows),reported);t.close()

    def test_smoke_test_creates_report_parent(self):
        from console_gui import run_smoke_test
        class Window(QWidget):
            class Tabs:
                def count(self):return 5
            tabs=Tabs()
        with tempfile.TemporaryDirectory() as d:
            report=Path(d)/'missing'/'smoke.json'
            # The parent is created before the asynchronous terminal check starts.
            window=Window()
            with patch('console_gui.QTimer.singleShot'):
                run_smoke_test(APP,window,report)
            self.assertTrue(report.parent.is_dir())
            window.close()

    @unittest.skipIf(os.name!='posix','POSIX mode bits')
    def test_session_logs_are_owner_only(self):
        from console_gui import private_append
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'session.jsonl';log=private_append(path);log.write('private\n');log.close()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    @unittest.skipIf(os.name=='nt','POSIX serial pseudo-terminal integration test')
    def test_real_pseudo_terminal_rx_tx_log_disconnect(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, XDG_CONFIG_HOME=d):
            master, slave = pty.openpty()
            w = SerialConsole()
            errors = []; w.error = lambda title, msg: errors.append((title, str(msg)))
            w.port.setCurrentText(os.ttyname(slave))
            w.toggle_connection()
            self.assertIsNotNone(w.serial)
            path = Path(d) / 'capture.jsonl'
            w.log = path.open('a')
            os.write(master, b'Router ready\r\n')
            time.sleep(.03); w.poll()
            self.assertIn('Router ready', w.terminal.toPlainText())
            w.input.setText('show version'); w.send_line()
            self.assertEqual(os.read(master, 128), b'show version\r')
            w.hex_send.setChecked(True); w.input.setText('00 FF'); w.send_line()
            self.assertEqual(os.read(master, 128), b'\x00\xff')
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['direction'] for r in records], ['RX', 'TX', 'TX'])
            self.assertEqual(records[-1]['hex'], '00 ff')
            self.assertFalse(errors)
            os.close(master); w.poll()
            self.assertIsNone(w.serial)
            self.assertEqual(errors[0][0], 'Connection lost')
            w.close(); os.close(slave)


if __name__ == '__main__': unittest.main(verbosity=2)
