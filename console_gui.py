#!/usr/bin/env python3
"""Serial Desk — a local serial console."""
import codecs
import json
import sys
from datetime import datetime
from pathlib import Path
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSettings
from PyQt5.QtGui import QColor, QFont, QTextCursor, QKeySequence
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QComboBox, QPushButton, QLabel, QCheckBox, QPlainTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QInputDialog, QSplitter, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QShortcut, QScrollArea, QTabWidget, QSizePolicy, QToolButton, QTabBar)
from credential_vault import CredentialVault, VaultError
from profile_ui import SecurityMenu, unlock_vault
from version import VERSION
from network_ui import NetworkConsole, FileBrowser, Notepad
from updates import UpdateController
from serial_core import BAUDS, DEFAULTS, ProfileStore, open_port, ports, scan_rates, validate_settings


class Terminal(QPlainTextEdit):
    outgoing = pyqtSignal(bytes)

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont('Consolas' if sys.platform=='win32' else 'DejaVu Sans Mono', 11))
        self.setMaximumBlockCount(12000)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setPlaceholderText('Connect a device to begin.\n\nType directly here for an interactive console, or use the send bar below.\nCtrl+Shift+C copies selected text. Ctrl+Shift+V pastes to the device.')
        self.escape = ''
        self.escape_mode = False
        self.saved_position = 0

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy) and self.textCursor().hasSelection():
            self.copy()
            return
        if event.modifiers() & Qt.ControlModifier and event.modifiers() & Qt.ShiftModifier:
            if event.key() == Qt.Key_C:
                self.copy(); return
            if event.key() == Qt.Key_V:
                self.outgoing.emit(QApplication.clipboard().text().encode('utf-8')); return
        keys = {Qt.Key_Return: b'\r', Qt.Key_Enter: b'\r', Qt.Key_Backspace: b'\x7f',
                Qt.Key_Tab: b'\t', Qt.Key_Escape: b'\x1b', Qt.Key_Up: b'\x1b[A',
                Qt.Key_Down: b'\x1b[B', Qt.Key_Right: b'\x1b[C', Qt.Key_Left: b'\x1b[D',
                Qt.Key_Home: b'\x1b[H', Qt.Key_End: b'\x1b[F', Qt.Key_Delete: b'\x1b[3~'}
        if event.modifiers() & Qt.ControlModifier and Qt.Key_A <= event.key() <= Qt.Key_Z:
            self.outgoing.emit(bytes([event.key() - Qt.Key_A + 1]))
        elif event.key() in keys:
            self.outgoing.emit(keys[event.key()])
        elif event.text():
            self.outgoing.emit(event.text().encode('utf-8'))
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        self.outgoing.emit(source.text().encode('utf-8'))

    def reset(self):
        self.clear(); self.escape = ''; self.escape_mode = False; self.saved_position = 0

    def feed(self, text):
        follow = self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() - 3
        cursor = QTextCursor(self.document())
        cursor.setPosition(min(self.saved_position, self.document().characterCount() - 1))
        for ch in text:
            if self.escape_mode:
                self.escape += ch
                if self.escape.startswith(']'):
                    if ch == '\a' or self.escape.endswith('\x1b\\') or len(self.escape) > 4096:
                        self.escape_mode = False
                elif self.escape == '[':
                    continue
                elif self.escape.startswith('[') and '@' <= ch <= '~':
                    self.csi(cursor, self.escape[1:-1], ch)
                    self.escape_mode = False
                elif not self.escape.startswith('[') or len(self.escape) > 128:
                    self.escape_mode = False
                continue
            if ch == '\x1b':
                self.escape_mode = True; self.escape = ''
            elif ch == '\r':
                cursor.movePosition(QTextCursor.StartOfBlock)
            elif ch == '\n':
                cursor.movePosition(QTextCursor.EndOfBlock); cursor.insertText('\n')
            elif ch == '\b':
                cursor.movePosition(QTextCursor.PreviousCharacter)
            elif ch == '\t' or ord(ch) >= 32:
                if not cursor.atBlockEnd():
                    cursor.deleteChar()
                cursor.insertText(ch)
        self.saved_position = cursor.position()
        if follow:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def csi(self, c, args, command):
        # Lightweight console support; not a complete VT emulator.
        try:
            n = int(args or '1')
        except ValueError:
            n = 1
        n = max(1, min(n, 1000))
        if command == 'K':
            c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor); c.removeSelectedText()
        elif command == 'J' and args in ('2', '3'):
            c.select(QTextCursor.Document); c.removeSelectedText()
        elif command in 'ABCD':
            move = {'A': QTextCursor.Up, 'B': QTextCursor.Down, 'C': QTextCursor.Right, 'D': QTextCursor.Left}[command]
            c.movePosition(move, QTextCursor.MoveAnchor, n)
        elif command in ('H', 'f'):
            try:
                row, col = (int(x or '1') for x in (args + ';1').split(';')[:2])
                c.movePosition(QTextCursor.Start)
                c.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, max(0, min(row - 1, 1000)))
                c.movePosition(QTextCursor.StartOfBlock)
                c.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, max(0, min(col - 1, c.block().length() - 1)))
            except ValueError:
                pass


class Scanner(QThread):
    sample = pyqtSignal(dict)
    completed = pyqtSignal(list, str)

    def __init__(self, settings):
        super().__init__(); self.settings = settings

    def run(self):
        try:
            result = scan_rates(self.settings, self.isInterruptionRequested, self.sample.emit)
            self.completed.emit(result, '')
        except Exception as e:
            self.completed.emit([], str(e))


class DetectionDialog(QDialog):
    def __init__(self, settings, parent):
        super().__init__(parent)
        self.selected = None
        self.setWindowTitle('Find a likely baud rate')
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        label = QLabel('Passive scan • about 20 seconds\nKeep the device transmitting. No probe bytes are sent. Opening the port may toggle DTR/RTS.\nUses the selected parity, data bits, and stop bits. Readability is a hint, not proof.')
        label.setWordWrap(True); layout.addWidget(label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Baud', 'Bytes', 'Readable', 'Sample'])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        self.status = QLabel('Listening…'); layout.addWidget(self.status)
        row = QHBoxLayout(); layout.addLayout(row)
        self.use = QPushButton('Use selected rate'); self.use.setEnabled(False)
        self.stop = QPushButton('Cancel scan')
        row.addStretch(); row.addWidget(self.use); row.addWidget(self.stop)
        self.use.clicked.connect(self.choose); self.stop.clicked.connect(self.reject)
        self.worker = Scanner(settings)
        self.worker.sample.connect(self.add)
        self.worker.completed.connect(self.done_scan)
        self.worker.start()

    def add(self, r):
        i = self.table.rowCount(); self.table.insertRow(i)
        for col, val in enumerate([r['baud'], r['count'], f"{r['score']:.0%}", r['preview'].replace('\r', ' ').replace('\n', ' ')]):
            self.table.setItem(i, col, QTableWidgetItem(str(val)))
        self.status.setText(f"Sampled {r['baud']:,} baud…")

    def done_scan(self, results, error):
        self.stop.setText('Close')
        self.use.setEnabled(self.table.rowCount() > 0)
        if error:
            self.status.setText(error)
        elif results and results[0]['count'] >= 16 and results[0]['score'] >= .85:
            best = results[0]
            self.status.setText(f"Candidate: {best['baud']:,} baud. Verify the sample, select a row, then connect to confirm.")
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == str(best['baud']):
                    self.table.selectRow(row)
        else:
            self.status.setText('No reliable candidate. Device may be silent or sending binary data.')

    def choose(self):
        row = self.table.currentRow()
        if row >= 0:
            self.selected = self.table.item(row, 0).text(); self.accept()

    def done(self, result):
        self.worker.requestInterruption()
        self.worker.wait()
        super().done(result)


class SerialConsole(QWidget):
    title_changed=pyqtSignal(str)
    profiles_changed=pyqtSignal()
    def __init__(self,store=None):
        super().__init__()
        self.serial = None; self.log = None; self.rx = 0; self.tx = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        profile_error = None
        try:
            self.store = store or ProfileStore()
        except (ValueError, OSError, TypeError) as e:
            # Keep a damaged file intact until the user explicitly saves again.
            profile_error = str(e)
            self.store = ProfileStore(load=False)
            # Back up the unreadable file before any later settings save.
            backup = self.store.path.with_name(self.store.path.name + datetime.now().strftime('.broken-%Y%m%d-%H%M%S'))
            try:
                self.store.path.rename(backup)
                profile_error += f'\nOriginal file preserved at {backup}'
            except OSError:
                pass
        root = self
        self.message=QLabel()
        outer = QVBoxLayout(root); outer.setContentsMargins(24, 20, 24, 20); outer.setSpacing(16)
        header = QHBoxLayout(); outer.addLayout(header)
        title = QLabel('SERIAL DESK'); title.setObjectName('title'); header.addWidget(title)
        header.addStretch()
        self.state = QLabel('●  DISCONNECTED'); self.state.setObjectName('state'); header.addWidget(self.state)
        split = QSplitter(Qt.Horizontal); outer.addWidget(split, 1)
        left = QWidget(); left.setMinimumWidth(220)
        side = QVBoxLayout(left); side.setContentsMargins(0, 0, 16, 0); side.setSpacing(16)
        self.sidebar = QScrollArea(); self.sidebar.setWidgetResizable(True); self.sidebar.setWidget(left)
        self.sidebar.setMinimumWidth(245); self.sidebar.setMaximumWidth(360); split.addWidget(self.sidebar)
        group = QGroupBox('CONNECTION'); form = QFormLayout(group); form.setSpacing(8); form.setRowWrapPolicy(QFormLayout.WrapAllRows); form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow); side.addWidget(group)
        self.port = QComboBox(); self.port.setEditable(True); self.port.setMinimumContentsLength(18)
        form.addRow('Port', self.port)
        refresh = QPushButton('Refresh ports'); refresh.clicked.connect(self.refresh_ports); form.addRow(refresh)
        self.baud = self.combo([str(b) for b in BAUDS], '115200', True); form.addRow('Baud rate', self.baud)
        self.bits = self.combo(['5', '6', '7', '8'], '8'); form.addRow('Data bits', self.bits)
        self.parity = self.combo(['None', 'Even', 'Odd', 'Mark', 'Space'], 'None'); form.addRow('Parity', self.parity)
        self.stopbits = self.combo(['1', '1.5', '2'], '1'); form.addRow('Stop bits', self.stopbits)
        self.flow = self.combo(['None', 'RTS/CTS', 'XON/XOFF', 'DSR/DTR'], 'None'); form.addRow('Flow control', self.flow)
        lines = QHBoxLayout(); self.dtr = QCheckBox('DTR'); self.rts = QCheckBox('RTS')
        self.dtr.setChecked(True); self.rts.setChecked(True); lines.addWidget(self.dtr); lines.addWidget(self.rts); form.addRow(lines)
        self.connect_button = QPushButton('Connect'); self.connect_button.setObjectName('primary')
        self.connect_button.clicked.connect(self.toggle_connection); form.addRow(self.connect_button)
        self.detect = QPushButton('Find baud rate…'); self.detect.clicked.connect(self.detect_baud); form.addRow(self.detect)
        self.connection_fields = [self.port, refresh, self.baud, self.bits, self.parity, self.stopbits, self.flow, self.dtr, self.rts, self.detect]
        group = QGroupBox('PROFILES'); pf = QVBoxLayout(group); side.addWidget(group)
        self.profiles = QComboBox(); pf.addWidget(self.profiles)
        row = QHBoxLayout(); pf.addLayout(row)
        for name, callback in [('Load', self.load_profile), ('Save…', self.save_profile), ('Delete', self.delete_profile)]:
            b = QPushButton(name); b.clicked.connect(callback); row.addWidget(b)
        self.auto_profile=QCheckBox('Connect when profile selected'); pf.addWidget(self.auto_profile)
        self.profiles.activated[str].connect(self.selected_profile)
        side.addStretch()
        note = QLabel('Settings stay on this computer.\nDirect keyboard input • UTF-8 text\nHex view for binary traffic')
        note.setWordWrap(True); note.setObjectName('muted'); side.addWidget(note)
        right = QWidget(); right.setMinimumWidth(200); pane = QVBoxLayout(right); pane.setContentsMargins(0, 0, 0, 0); split.addWidget(right)
        toolbar_widget = QWidget(); toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(0,0,0,0)
        toolbar_scroll = QScrollArea(); toolbar_scroll.setWidgetResizable(True); toolbar_scroll.setWidget(toolbar_widget)
        toolbar_scroll.setFixedHeight(64); toolbar_scroll.setFrameShape(QScrollArea.NoFrame)
        pane.addWidget(toolbar_scroll)
        self.hex_view = QCheckBox('Hex view'); self.echo = QCheckBox('Local echo')
        toolbar.addWidget(self.hex_view); toolbar.addWidget(self.echo); toolbar.addStretch()
        self.log_button = QPushButton('Start logging…'); self.log_button.clicked.connect(self.toggle_log); toolbar.addWidget(self.log_button)
        export = QPushButton('Export view…'); export.clicked.connect(self.export_view); toolbar.addWidget(export)
        clear = QPushButton('Clear'); clear.clicked.connect(self.clear); toolbar.addWidget(clear)
        self.terminal = Terminal(); pane.addWidget(self.terminal, 1); self.terminal.outgoing.connect(self.send)
        self.hex_view.toggled.connect(lambda: self.decoder.reset())
        row = QHBoxLayout(); pane.addLayout(row)
        self.input = QLineEdit(); self.input.setPlaceholderText('Send text or hex bytes…'); self.input.returnPressed.connect(self.send_line); row.addWidget(self.input, 1)
        self.hex_send = QCheckBox('Hex'); row.addWidget(self.hex_send)
        self.ending = self.combo(['None', 'CR', 'LF', 'CRLF'], 'CR'); row.addWidget(self.ending)
        send = QPushButton('Send'); send.clicked.connect(self.send_line); row.addWidget(send)
        controls = QHBoxLayout(); pane.addLayout(controls)
        for name, callback in [('Ctrl+C', lambda: self.send(b'\x03')), ('Enter', lambda: self.send(b'\r')), ('Send break', self.send_break)]:
            b = QPushButton(name); b.clicked.connect(callback); controls.addWidget(b)
        controls.addStretch(); self.counts = QLabel('RX 0 B    TX 0 B'); controls.addWidget(self.counts)
        outer.addWidget(self.message)
        self.statusBar().showMessage('Ready • select a port and connect')
        self.refresh_ports(); self.refresh_profiles(); self.apply(self.store.last)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(30)
        self.port_timer = QTimer(self); self.port_timer.timeout.connect(self.auto_refresh); self.port_timer.start(3000)
        split.setStretchFactor(1,1)
        if profile_error: QTimer.singleShot(0,lambda:self.error('Profiles could not be read',profile_error))
    def statusBar(self): return self
    def showMessage(self,text): self.message.setText(text)
    def selected_profile(self,name):
        self.load_profile()
        if self.auto_profile.isChecked() and not self.serial: self.toggle_connection()
    @staticmethod
    def combo(items, selected, editable=False):
        w = QComboBox(); w.addItems(items); w.setEditable(editable); w.setCurrentText(selected); return w

    def error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def refresh_ports(self):
        current = self.port.currentText(); selected = self.port.currentData() if self.port.currentIndex() >= 0 and current == self.port.itemText(self.port.currentIndex()) else None
        self.port.clear()
        for p in ports():
            self.port.addItem(p.device, p.device)
            self.port.setItemData(self.port.count() - 1, f'{p.description}\n{p.hwid}', Qt.ToolTipRole)
        index = self.port.findData(selected) if selected else -1
        if index >= 0:
            self.port.setCurrentIndex(index)
        elif current:
            self.port.setCurrentText(current)
        self.port.setToolTip('Select a detected device, or enter a path / COM port. Use loop:// for a software loopback test.')

    def auto_refresh(self):
        if not self.serial and not self.port.view().isVisible() and not self.port.hasFocus():
            self.refresh_ports()

    def settings(self):
        port = self.port.currentData() if self.port.currentIndex() >= 0 and self.port.currentText() == self.port.itemText(self.port.currentIndex()) else self.port.currentText().strip()
        baud = int(self.baud.currentText())
        if baud <= 0 or not port:
            raise ValueError('Select a port and a positive baud rate.')
        return dict(port=port, baudrate=baud, bytesize=int(self.bits.currentText()),
                    parity={'None':'N','Even':'E','Odd':'O','Mark':'M','Space':'S'}[self.parity.currentText()],
                    stopbits=float(self.stopbits.currentText()), flow=self.flow.currentText(),
                    dtr=self.dtr.isChecked(), rts=self.rts.isChecked(), ending=self.ending.currentText(),
                    local_echo=self.echo.isChecked(), hex_view=self.hex_view.isChecked())

    def apply(self, settings):
        s = validate_settings(settings)
        if s['port']:
            idx = self.port.findData(s['port'])
            if idx >= 0: self.port.setCurrentIndex(idx)
            else: self.port.setCurrentText(s['port'])
        self.baud.setCurrentText(str(s['baudrate'])); self.bits.setCurrentText(str(s['bytesize']))
        self.parity.setCurrentText({'N':'None','E':'Even','O':'Odd','M':'Mark','S':'Space'}.get(s['parity'], 'None'))
        self.stopbits.setCurrentText(f"{float(s['stopbits']):g}"); self.flow.setCurrentText(s['flow'])
        self.dtr.setChecked(s['dtr']); self.rts.setChecked(s['rts']); self.ending.setCurrentText(s['ending'])
        self.echo.setChecked(s['local_echo']); self.hex_view.setChecked(s['hex_view'])

    def toggle_connection(self):
        if self.serial:
            self.disconnect(); return
        try:
            s = self.settings(); self.serial = open_port(s)
        except Exception as e:
            self.error('Could not connect', e); return
        self.decoder.reset(); self.rx = self.tx = 0; self.update_counts()
        for field in self.connection_fields: field.setEnabled(False)
        self.profiles.setEnabled(False)
        self.connect_button.setText('Disconnect'); self.state.setText('●  CONNECTED')
        self.state.setStyleSheet('color: #46c6ac')
        self.statusBar().showMessage(f"{s['port']} • {s['baudrate']:,} baud • {s['bytesize']}{s['parity']}{s['stopbits']:g}")
        self.terminal.setFocus()
        self.title_changed.emit(s['port'])
        self.store.last = s
        self.persist()

    def disconnect(self):
        if self.serial:
            self.serial.close(); self.serial = None
        for field in self.connection_fields: field.setEnabled(True)
        self.profiles.setEnabled(True)
        self.connect_button.setText('Connect'); self.state.setText('●  DISCONNECTED'); self.state.setStyleSheet('')
        self.statusBar().showMessage('Disconnected')

    def poll(self):
        if not self.serial: return
        try:
            n = self.serial.in_waiting
            if n:
                data = self.serial.read(min(n, 65536)); self.rx += len(data)
                self.log_data('RX', data); self.show_data(data); self.update_counts()
        except Exception as e:
            self.disconnect(); self.error('Connection lost', e)

    def show_data(self, data):
        if self.hex_view.isChecked():
            self.terminal.feed(' '.join(f'{b:02X}' for b in data) + '\r\n')
        else:
            self.terminal.feed(self.decoder.decode(data))

    def send(self, data):
        if not self.serial:
            self.statusBar().showMessage('Connect a device before sending.'); return
        try:
            count = self.serial.write(data)
            self.tx += count; self.log_data('TX', data[:count]); self.update_counts()
            if self.echo.isChecked(): self.show_data(data[:count])
            if count != len(data): raise IOError(f'Only {count} of {len(data)} bytes were sent.')
        except Exception as e:
            self.error('Send failed', e)

    def send_line(self):
        try:
            data = bytes.fromhex(self.input.text()) if self.hex_send.isChecked() else self.input.text().encode('utf-8')
            if not self.hex_send.isChecked():
                data += {'None':b'', 'CR':b'\r', 'LF':b'\n', 'CRLF':b'\r\n'}[self.ending.currentText()]
        except ValueError:
            self.error('Invalid hex', 'Use hexadecimal byte pairs, for example: 48 65 6C 6C 6F'); return
        self.send(data)

    def send_break(self):
        if self.serial:
            try: self.serial.send_break(0.25)
            except Exception as e: self.error('Break failed', e)

    def update_counts(self):
        self.counts.setText(f'RX {self.rx:,} B    TX {self.tx:,} B')

    def clear(self):
        self.terminal.reset(); self.decoder.reset()

    def toggle_log(self):
        if self.log:
            self.log.close(); self.log = None; self.log_button.setText('Start logging…'); self.log_button.setToolTip(''); return
        path, _ = QFileDialog.getSaveFileName(self, 'Save session log', str(Path.home() / f"serial-{datetime.now():%Y%m%d-%H%M%S}.jsonl"), 'Session log (*.jsonl)')
        if path:
            try:
                self.log = open(path, 'a', encoding='utf-8'); self.log_button.setText('● Stop logging'); self.log_button.setToolTip(path)
            except OSError as e: self.error('Could not start logging', e)

    def log_data(self, direction, data):
        if not self.log: return
        try:
            record = dict(time=datetime.now().astimezone().isoformat(timespec='milliseconds'), direction=direction,
                          hex=data.hex(' '), text=data.decode('utf-8', errors='replace'))
            self.log.write(json.dumps(record, ensure_ascii=True) + '\n'); self.log.flush()
        except OSError as e:
            self.log.close(); self.log = None; self.log_button.setText('Start logging…'); self.error('Logging stopped', e)

    def export_view(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export visible console', str(Path.home() / 'console.txt'), 'Text (*.txt)')
        if path:
            try: Path(path).write_text(self.terminal.toPlainText(), encoding='utf-8')
            except OSError as e: self.error('Export failed', e)

    def persist(self):
        try: self.store.write()
        except OSError as e: self.error('Could not save settings', e)

    def refresh_profiles(self):
        current = self.profiles.currentText(); self.profiles.clear(); self.profiles.addItems(sorted(self.store.profiles))
        if current in self.store.profiles: self.profiles.setCurrentText(current)

    def load_profile(self):
        if self.serial: return
        name = self.profiles.currentText()
        if name in self.store.profiles:
            try: self.apply(self.store.profiles[name])
            except (ValueError, TypeError, KeyError) as e: self.error('Invalid profile', e)

    def save_profile(self):
        try: settings = self.settings()
        except ValueError as e: self.error('Invalid settings', e); return
        name, ok = QInputDialog.getText(self, 'Save connection profile', 'Profile name:', text=self.profiles.currentText())
        if not ok or not name.strip(): return
        name = name.strip()
        if name in self.store.profiles and QMessageBox.question(self, 'Replace profile', f'Replace “{name}”?') != QMessageBox.Yes: return
        self.store.profiles[name] = settings; self.persist(); self.refresh_profiles(); self.profiles.setCurrentText(name); self.profiles_changed.emit()

    def delete_profile(self):
        name = self.profiles.currentText()
        if name in self.store.profiles and QMessageBox.question(self, 'Delete profile', f'Delete “{name}”?') == QMessageBox.Yes:
            del self.store.profiles[name]; self.persist(); self.refresh_profiles(); self.profiles_changed.emit()

    def detect_baud(self):
        try: settings = self.settings()
        except ValueError as e: self.error('Invalid settings', e); return
        dialog = DetectionDialog(settings, self)
        if dialog.exec_() == QDialog.Accepted and dialog.selected:
            self.baud.setCurrentText(dialog.selected)

    def shutdown(self):
        self.timer.stop(); self.port_timer.stop()
        try: self.store.last=self.settings()
        except ValueError: pass
        self.persist(); self.disconnect()
        if self.log: self.log.close(); self.log=None
    def closeEvent(self,event): self.shutdown(); event.accept()


class Window(QMainWindow):
    def __init__(self,vault=None,check_updates=True):
        super().__init__()
        self.vault=vault or CredentialVault()
        if self.vault.locked: raise VaultError('Unlock Serial Desk before opening the main window.')
        config=QSettings('MusicCityTelecom','SerialDesk')
        try: legacy=json.loads(config.value('network_profiles','{}'))
        except (ValueError,TypeError): legacy={}
        self.vault.migrate_legacy(legacy if isinstance(legacy,dict) else {})
        self.setWindowTitle(f'Serial Desk {VERSION}'); self.setMinimumSize(640,420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        screen=QApplication.primaryScreen().availableGeometry()
        self.resize(min(1280,screen.width()-40),min(850,screen.height()-60))
        self.tabs=QTabWidget(); self.tabs.setMovable(True); self.tabs.setTabsClosable(True); self.tabs.setUsesScrollButtons(True)
        self.setCentralWidget(self.tabs); self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self.rename_tab)
        self.serial_tab=self.new_serial(); self.network=self.new_network(); self.files=self.new_files()
        self.notes=Notepad(); self.add_tab(self.notes,'Notepad'); self.local=self.new_network(local=True)
        self.tabs.setCurrentIndex(0)
        sessions=self.menuBar().addMenu('Sessions')
        sessions.addAction('New SSH / Telnet tab',lambda:self.new_network(),'Ctrl+T')
        sessions.addAction('New serial tab',self.new_serial,'Ctrl+Shift+T')
        sessions.addAction('New file browser tab',self.new_files,'Ctrl+Shift+F')
        sessions.addAction('New local terminal tab',lambda:self.new_network(local=True),'Ctrl+Shift+L')
        sessions.addAction('Close current tab',lambda:self.close_tab(self.tabs.currentIndex()),'Ctrl+W')
        plus=QToolButton(); plus.setText('+'); plus.setToolTip('New session'); plus.setMenu(sessions); plus.setPopupMode(QToolButton.InstantPopup)
        self.tabs.setCornerWidget(plus,Qt.TopRightCorner)
        view=self.menuBar().addMenu('View')
        view.addAction('Maximize / restore',lambda:self.showNormal() if self.isMaximized() else self.showMaximized(),'F11')
        view.addAction('Show / hide serial settings',self.toggle_sidebar,'Ctrl+B')
        view.addAction('Larger console text',lambda:self.zoom_console(1),'Ctrl++')
        view.addAction('Smaller console text',lambda:self.zoom_console(-1),'Ctrl+-')
        self.security=SecurityMenu(self,self.vault); self.updates=UpdateController(self,startup=check_updates); self.apply_style()
    def widgets(self): return [self.tabs.widget(i) for i in range(self.tabs.count())]
    def add_tab(self,widget,title):
        index=self.tabs.addTab(widget,title); self.tabs.setCurrentIndex(index)
        if isinstance(widget,Notepad):
            self.tabs.tabBar().setTabButton(index,QTabBar.RightSide,None)
        else:
            close=QToolButton(); close.setText('×'); close.setToolTip('Close tab'); close.setAutoRaise(True)
            close.setStyleSheet('color: #dce6f2; background: transparent; padding: 1px 4px; font-size: 17px')
            close.clicked.connect(lambda:self.close_tab(self.tabs.indexOf(widget)))
            self.tabs.tabBar().setTabButton(index,QTabBar.RightSide,close)
        if hasattr(widget,'title_changed'): widget.title_changed.connect(lambda text:self.name_tab(widget,text))
        if hasattr(widget,'profile_bar'): widget.profile_bar.changed.connect(self.refresh_profiles)
        return widget
    def name_tab(self,widget,title):
        index=self.tabs.indexOf(widget)
        if index>=0: self.tabs.setTabText(index,title[:35]); self.tabs.setTabToolTip(index,title)
    def new_serial(self):
        store=getattr(self,'serial_store',None)
        widget=SerialConsole(store); self.serial_store=widget.store; widget.profiles_changed.connect(self.refresh_profiles)
        return self.add_tab(widget,'Serial')
    def new_network(self,local=False):
        widget=NetworkConsole(Terminal,local=local,vault=self.vault)
        widget.browse_requested.connect(self.browse_ssh); widget.duplicate_requested.connect(self.duplicate_session)
        return self.add_tab(widget,'Local terminal' if local else 'SSH / Telnet')
    def new_files(self): return self.add_tab(FileBrowser(self.vault),'Files')
    def browse_ssh(self,source,protocol):
        if not source.worker or not source.worker.ssh_handle: return
        widget=self.new_files()
        try: widget.attach_ssh(source.worker.ssh_handle,protocol,source.last_settings)
        except Exception as e: QMessageBox.warning(self,'File browser',str(e))
    def duplicate_session(self,source):
        local=source.credentials.protocol.currentText()=='Local terminal'
        widget=self.new_network(local=local)
        widget.credentials.apply(source.last_settings or source.credentials.values())
        # Copy selected saved profile on purpose; never auto-send credentials or commands on duplication.
        name=source.profile_bar.combo.currentText()
        if name:
            widget.profile_bar.combo.setCurrentText(name); widget.profile_bar.load()
    def rename_tab(self,index):
        if index<0: return
        title,ok=QInputDialog.getText(self,'Rename tab','Tab name:',text=self.tabs.tabText(index))
        if ok and title.strip(): self.tabs.setTabText(index,title.strip())
    def refresh_profiles(self):
        for widget in self.widgets():
            if hasattr(widget,'profile_bar'): widget.profile_bar.refresh()
            if isinstance(widget,SerialConsole): widget.refresh_profiles()
    def toggle_sidebar(self):
        widget=self.tabs.currentWidget()
        if isinstance(widget,SerialConsole): widget.sidebar.setVisible(not widget.sidebar.isVisible())
    def zoom_console(self,amount):
        widget=self.tabs.currentWidget()
        if hasattr(widget,'terminal'): widget.terminal.zoomIn(amount)
    def busy(self,widget):
        return isinstance(widget,FileBrowser) and ((widget.task and widget.task.isRunning()) or bool(getattr(widget,'pending_transfers',False)))
    def close_tab(self,index):
        widget=self.tabs.widget(index)
        if not widget or widget is self.notes: return
        if self.busy(widget): QMessageBox.information(self,'Transfer in progress','Let the current file operation finish before closing this tab.'); return
        active=(isinstance(widget,SerialConsole) and widget.serial is not None) or (isinstance(widget,NetworkConsole) and widget.worker and widget.worker.isRunning()) or (isinstance(widget,FileBrowser) and widget.remote)
        if active and QMessageBox.question(self,'Close session','Disconnect and close this tab?')!=QMessageBox.Yes: return
        widget.shutdown(); self.tabs.removeTab(index); widget.deleteLater()
    def closeEvent(self,event):
        if any(self.busy(w) for w in self.widgets()) or (self.updates.task and self.updates.task.isRunning()):
            QMessageBox.information(self,'Operation in progress','Please let file transfers and updates finish before closing.'); event.ignore(); return
        for widget in self.widgets(): widget.shutdown()
        self.updates.shutdown(); self.vault.lock(); event.accept()
    def apply_style(self):
        self.setStyleSheet('''
            QTabWidget::pane { border: 0; }
            QTabBar::tab { background: #243449; padding: 10px 20px; }
            QTabBar::tab:selected { background: #326e91; }
            QScrollArea { border: 0; }
            QWidget { background: #121a25; color: #dce6f2; font-size: 13px; }
            QGroupBox { border: 1px solid #2c3b4d; border-radius: 8px; margin-top: 12px; padding: 18px 12px 12px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #8aa1bb; }
            QComboBox, QLineEdit { background: #1c2939; border: 1px solid #34465e; border-radius: 5px; padding: 7px; }
            QComboBox QAbstractItemView { background: #1c2939; selection-background-color: #326e91; }
            QPushButton { background: #243449; border: 1px solid #354b65; border-radius: 5px; padding: 8px 12px; }
            QPushButton:hover { background: #304965; }
            QPushButton:disabled, QComboBox:disabled { color: #64748b; }
            QPushButton#primary { background: #46c6ac; color: #0c2722; font-weight: bold; padding: 11px; }
            QLabel#title { font-size: 23px; font-weight: bold; letter-spacing: 3px; color: #f2f7ff; }
            QLabel#state { color: #8aa1bb; } QLabel#muted { color: #8095ad; line-height: 1.5; }
            QPlainTextEdit { background: #090f18; color: #bce4da; border: 1px solid #2c3b4d; border-radius: 8px; padding: 12px; font-size: 14px; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #64748b; border-radius: 3px; background: #1c2939; }
            QCheckBox::indicator:checked { background: #46c6ac; border: 1px solid #46c6ac; }
            QStatusBar { color: #8aa1bb; } QSplitter::handle { background: #121a25; }
            QTableWidget { background: #101925; gridline-color: #34465e; }
            QHeaderView::section { background: #243449; padding: 7px; }
        ''')


def run_smoke_test(app,window,report):
    """Exercise the bundled GUI and platform terminal on a CI machine."""
    import time
    started=time.monotonic(); received=[]; finished=[False]
    timer=QTimer(window)
    def finish(error=''):
        if finished[0]: return
        finished[0]=True;timer.stop()
        report.write_text(json.dumps(dict(version=VERSION,tabs=window.tabs.count(),local_terminal=not bool(error),error=error)))
        window.close();app.quit()
    def begin():
        window.local.toggle()
        worker=window.local.worker
        worker.failed.disconnect();worker.failed.connect(finish);worker.received.connect(received.append)
        command=b'echo SERIAL_DESK_BUNDLE_O^K\r' if sys.platform=='win32' else b"printf 'SERIAL_DESK_BUNDLE_%s\\n' OK\r"
        worker.send(command);timer.start(50)
    def tick():
        if b'SERIAL_DESK_BUNDLE_OK' in b''.join(received): finish()
        elif time.monotonic()-started>20: finish('Local terminal did not produce the expected response: '+repr(b''.join(received)[-2000:]))
    timer.timeout.connect(tick);QTimer.singleShot(0,begin)


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv); app.setApplicationName('Serial Desk'); app.setOrganizationName('Local Tools')
    app.setStyle('Fusion')
    try:
        vault=CredentialVault()
        if not unlock_vault(vault): return
        window=Window(vault,check_updates='--smoke-test' not in sys.argv); window.show()
        if '--smoke-test' in sys.argv:
            run_smoke_test(app,window,Path(sys.argv[sys.argv.index('--smoke-test')+1]))
    except (VaultError,OSError) as e:
        QMessageBox.critical(None,'Serial Desk could not open',str(e)); return
    sys.exit(app.exec_())


if __name__ == '__main__': main()
