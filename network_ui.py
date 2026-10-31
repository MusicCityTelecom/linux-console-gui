"""Network console, file transfer browser, and persistent scratchpad widgets."""
import codecs
import ftplib
import json
import os
import posixpath
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from PyQt5.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit,
    QLabel, QPushButton, QCheckBox, QFileDialog, QMessageBox, QPlainTextEdit, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QInputDialog, QScrollArea, QGroupBox, QSpinBox)
from network_core import Session, Task, ssh_connect, CONFIG
from credential_vault import CredentialVault
from profile_ui import ProfileBar
from file_editing import RemoteTextStore,TextEditor,decode_text,ENCODINGS


def button(text, callback, layout):
    b = QPushButton(text); b.clicked.connect(callback); layout.addWidget(b); return b


def answer_credential(parent,worker,title,prompt,secret):
    if worker.isInterruptionRequested():worker.respond_credential(None);return
    dialog=QInputDialog(parent);dialog.setWindowTitle('SSH login — '+title[:120]);dialog.setLabelText(prompt[:2000]);dialog.setTextEchoMode(QLineEdit.Password if secret else QLineEdit.Normal)
    for label in dialog.findChildren(QLabel):label.setTextFormat(Qt.PlainText)
    accepted=dialog.exec_()==QInputDialog.Accepted;answer=dialog.textValue() if accepted else None;dialog.setTextValue('');dialog.deleteLater();worker.respond_credential(answer)


class Credentials(QWidget):
    def __init__(self, protocols):
        super().__init__();self.credential_ref=''; form=QFormLayout(self);self.form=form;form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.protocol=QComboBox();self.protocol.addItems(protocols);form.addRow('Connection type',self.protocol)
        self.host=QLineEdit();self.host.setPlaceholderText('Hostname or IP address');form.addRow('Remote host',self.host)
        self.port=QLineEdit();self.port.setMaximumWidth(100);form.addRow('Port',self.port)
        self.user=QLineEdit();form.addRow('Username',self.user)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.Password);form.addRow('Password',self.password)
        show=QCheckBox('Show password');show.toggled.connect(lambda visible:self.password.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password));form.addRow(show);self.show_password=show
        self.ssh_auth=QComboBox()
        for label,value in [('Automatic','auto'),('Password','password'),('Keyboard-interactive / device prompts','interactive'),('Keys / SSH agent','keys')]:self.ssh_auth.addItem(label,value)
        form.addRow('SSH login method',self.ssh_auth)
        self.ssh_legacy=QCheckBox('Legacy SSH algorithms (older devices)');self.ssh_legacy.setToolTip('Per-device compatibility: supported SHA-1 DH/RSA, CBC and older MACs. RSA authentication uses ssh-rsa for old servers. Host fingerprints are still verified. Unsupported SSHv1/removed algorithms are not added.');form.addRow(self.ssh_legacy)
        self.key_group=QGroupBox('SSH private key (optional)');self.key_group.setCheckable(True)
        key_layout=QVBoxLayout(self.key_group);self.key_body=QWidget();key_form=QFormLayout(self.key_body);key_form.setRowWrapPolicy(QFormLayout.WrapAllRows);key_layout.addWidget(self.key_body)
        self.key=QLineEdit();self.key.setPlaceholderText('SSH agent / default keys if blank');key_form.addRow('Private key path',self.key)
        self.key_data='';self.key.textEdited.connect(self.clear_stored_key)
        self.key_pick=QPushButton('Choose key file…');self.key_pick.clicked.connect(self.choose_key);key_form.addRow(self.key_pick)
        self.passphrase=QLineEdit();self.passphrase.setEchoMode(QLineEdit.Password);key_form.addRow('Key passphrase',self.passphrase)
        self.key_group.toggled.connect(self.key_body.setVisible);self.key_group.setChecked(False);form.addRow(self.key_group)
        self.ca=QLineEdit();self.ca.setPlaceholderText('Optional CA certificate file');form.addRow('TLS CA certificate',self.ca)
        self.unverified=QCheckBox('Allow unverified TLS certificate');form.addRow(self.unverified)
        self.legacy=QCheckBox('Legacy TLS 1.0 / older ciphers');form.addRow(self.legacy)
        self.open_files=QCheckBox('Open remote files with SSH');form.addRow(self.open_files)
        self.file_protocol=QComboBox();self.file_protocol.addItems(['SFTP','SCP','FTP','FTPS','TFTP']);form.addRow('Companion file service',self.file_protocol)
        self.file_port=QSpinBox();self.file_port.setRange(1,65535);self.file_port.setValue(22);form.addRow('File service port',self.file_port)
        self.remote_path=QLineEdit('.');self.remote_path.setPlaceholderText('Remote starting folder');form.addRow('Remote starting folder',self.remote_path)
        self.notice=QLabel();self.notice.setWordWrap(True);form.addRow(self.notice)
        self.protocol.currentTextChanged.connect(self.changed);self.file_protocol.currentTextChanged.connect(self.file_changed)
        for field in (self.user,self.password,self.passphrase,self.key):field.textEdited.connect(self.unlink_credential)
        self.port.textChanged.connect(self.sync_file_port);self.changed()
    def row_visible(self,widget,visible):
        widget.setVisible(visible)
        label=self.form.labelForField(widget)
        if label:label.setVisible(visible)
    def changed(self):
        from device_profiles import DEFAULT_PORTS
        p=self.protocol.currentText();previous=getattr(self,'previous_protocol',None)
        if previous and previous!=p and not ({previous,p}<={'SSH','SFTP','SCP'} or {previous,p}<={'FTP','FTPS'}):
            for field in (self.user,self.password,self.passphrase,self.key):field.clear()
            self.key_data='';self.credential_ref=''
        self.previous_protocol=p;self.port.setText(str(DEFAULT_PORTS.get(p,22)))
        local=p=='Local terminal';auth=p in ('SSH','SFTP','SCP','FTP','FTPS');ssh=p in ('SSH','SFTP','SCP');tls=p in ('Mitel TLS Telnet','FTPS')
        for w in (self.host,self.port):self.row_visible(w,not local)
        for w in (self.user,self.password,self.show_password):self.row_visible(w,auth)
        self.row_visible(self.key_group,ssh)
        for w in (self.ssh_auth,self.ssh_legacy):self.row_visible(w,ssh)
        for w in (self.ca,self.unverified):self.row_visible(w,tls)
        self.row_visible(self.legacy,p=='Mitel TLS Telnet')
        for w in (self.open_files,self.file_protocol,self.file_port):self.row_visible(w,p=='SSH')
        self.row_visible(self.remote_path,p in ('SSH','SFTP','SCP','FTP','FTPS'))
        self.notice.setText('FTP / Telnet traffic is unencrypted.' if p in ('FTP','Telnet') else 'Mitel TLS Telnet normally uses port 2000.' if p=='Mitel TLS Telnet' else 'TFTP uses exact filenames; it has no directory listing or login.' if p=='TFTP' else '')
        self.file_changed()
    def file_changed(self):
        from device_profiles import DEFAULT_PORTS
        p=self.file_protocol.currentText()
        self.file_port.setValue(int(self.port.text()) if p in ('SFTP','SCP') and self.port.text().isdigit() else DEFAULT_PORTS[p])
        self.file_port.setEnabled(p not in ('SFTP','SCP'));self.sync_file_port()
    def sync_file_port(self):
        if self.file_protocol.currentText() in ('SFTP','SCP') and self.port.text().isdigit():
            value=int(self.port.text())
            if 1<=value<=65535:self.file_port.setValue(value)
    def values(self):
        p=self.protocol.currentText()
        if p!='Local terminal' and not self.host.text().strip():raise ValueError('Enter a remote host.')
        port=int(self.port.text())
        if not 1<=port<=65535:raise ValueError('Port must be between 1 and 65535.')
        result=dict(protocol=p,host=self.host.text().strip(),port=port,user=self.user.text().strip(),password=self.password.text(),passphrase=self.passphrase.text(),key=self.key.text().strip(),key_data=self.key_data,credential_ref=self.credential_ref if p in ('SSH','SFTP','SCP','FTP','FTPS') else '',ca=self.ca.text().strip(),unverified=self.unverified.isChecked(),legacy=self.legacy.isChecked(),remote_path=self.remote_path.text().strip() or '.')
        result.update(ssh_auth=self.ssh_auth.currentData() if p in ('SSH','SFTP','SCP') else 'auto',ssh_legacy=self.ssh_legacy.isChecked() if p in ('SSH','SFTP','SCP') else False)
        if p not in ('SSH','SFTP','SCP','FTP','FTPS'):result.update(user='',password='')
        if p not in ('SSH','SFTP','SCP') or not self.key_group.isChecked():result.update(passphrase='',key='',key_data='')
        if p not in ('Mitel TLS Telnet','FTPS'):result.update(ca='',unverified=False)
        if p=='SSH':result.update(open_files=self.open_files.isChecked(),file_protocol=self.file_protocol.currentText(),file_port=self.file_port.value())
        return result
    def clear_stored_key(self,*args):self.key_data=''
    def unlink_credential(self,*args):self.credential_ref=''
    def choose_key(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose SSH private key',str(Path.home()/'.ssh'))
        if path:self.key.setText(path);self.key_data='';self.credential_ref='';self.key_group.setChecked(True)
    def apply(self,s):
        self.protocol.setCurrentText(s.get('protocol','SSH'))
        for name in ('host','port','user','key','ca','password','passphrase'):getattr(self,name).setText(str(s.get(name,'')))
        self.key_data=s.get('key_data','');self.key_group.setChecked(bool(self.key.text() or self.key_data))
        for name in ('unverified','legacy','open_files','ssh_legacy'):getattr(self,name).setChecked(bool(s.get(name,False)))
        self.ssh_auth.setCurrentIndex(max(0,self.ssh_auth.findData(s.get('ssh_auth','auto'))))
        self.file_protocol.setCurrentText(s.get('file_protocol','SFTP'));self.file_port.setValue(int(s.get('file_port',s.get('port',22))))
        self.remote_path.setText(s.get('remote_path','.'));self.show_password.setChecked(False)
        self.credential_ref=s.get('credential_ref','')
    def confirm_tls(self,s):
        if s['protocol'] in ('SSH','SFTP','SCP') and s.get('ssh_legacy'):
            return QMessageBox.question(self,'Legacy SSH compatibility','This device will permit the supported older SSH choices (including SHA-1 key exchange/RSA, CBC ciphers and older MACs) for this connection. Host-key verification remains enabled. SSHv1 and unavailable removed algorithms are not enabled. Continue?')==QMessageBox.Yes
        if s['protocol'] not in ('Mitel TLS Telnet','FTPS') or not (s['unverified'] or s['legacy']):return True
        return QMessageBox.question(self,'TLS compatibility','This connection uses reduced TLS verification or legacy cryptography. Continue for this device?')==QMessageBox.Yes


class NetworkConsole(QWidget):
    browse_requested=pyqtSignal(object,str)
    title_changed=pyqtSignal(str)
    duplicate_requested=pyqtSignal(object)
    companion_requested=pyqtSignal(object,str)
    def __init__(self, terminal_class, local=False, vault=None):
        super().__init__(); self.vault=vault or CredentialVault(); self.last_settings=None; self.worker = None; self.log = None; self.rx = self.tx = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        layout = QVBoxLayout(self)
        split = QSplitter(); layout.addWidget(split, 1)
        sidebar = QWidget(); left = QVBoxLayout(sidebar)
        self.credentials = Credentials(['Local terminal'] if local else ['SSH','Telnet','Mitel TLS Telnet'])
        self.sidebar_area=None
        left.addWidget(self.credentials)
        self.connect_button = button('Start terminal' if local else 'Connect', self.toggle, left)
        self.profile_bar=ProfileBar(self.credentials,self.vault,self.toggle); left.insertWidget(0,self.profile_bar)
        left.addStretch()
        area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(sidebar); area.setMinimumWidth(320); area.setMaximumWidth(350)
        self.sidebar_area=area
        split.addWidget(area)
        right = QWidget(); main = QVBoxLayout(right); split.addWidget(right); split.setStretchFactor(1, 1)
        row = QHBoxLayout(); main.addLayout(row)
        self.state = QLabel('Disconnected'); row.addWidget(self.state); row.addStretch()
        left.removeWidget(self.connect_button);row.addWidget(self.connect_button)
        self.log_button = button('Log…', self.toggle_log, row); button('Clear', lambda: self.terminal.reset(), row)
        self.terminal = terminal_class(); self.terminal.outgoing.connect(self.send); main.addWidget(self.terminal, 1)
        row = QHBoxLayout(); main.addLayout(row)
        self.input = QLineEdit(); self.input.setPlaceholderText('Command'); row.addWidget(self.input, 1)
        self.input.returnPressed.connect(self.send_line)
        button('Send', self.send_line, row); button('Ctrl+C', lambda: self.send(b'\x03'), row)
        password_row=QHBoxLayout();main.addLayout(password_row)
        self.password_button=button('Paste saved password',self.paste_saved_password,password_row);self.password_button.setEnabled(False)
        self.password_button.setToolTip('Send the active SSH profile password without Enter. Use at a password prompt. Outgoing password bytes are redacted from logs; a remote echo or shell history can still reveal them at the wrong prompt.')
        self.password_hint=QLabel('Saved device login • no Enter is sent');self.password_hint.setWordWrap(True);password_row.addWidget(self.password_hint,1)
        tools=QHBoxLayout(); main.addLayout(tools)
        self.sftp_button=button('SFTP ↗',lambda:self.browse('SFTP'),tools)
        self.scp_button=button('SCP ↗',lambda:self.browse('SCP'),tools)
        button('Duplicate tab',lambda:self.duplicate_requested.emit(self),tools)
        self.sftp_button.setEnabled(False); self.scp_button.setEnabled(False)
        self.search=QLineEdit(); self.search.setPlaceholderText('Find in console…'); self.search.returnPressed.connect(self.find_text)
        tools.addWidget(self.search,1)
        self.counts = QLabel('RX 0 • TX 0'); main.addWidget(self.counts)
        self.focus_hint=QLabel('Disconnected • connect before typing');self.focus_hint.setWordWrap(True);main.addWidget(self.focus_hint)
        self.terminal.focus_changed.connect(self.update_focus_hint)
        if local:
            from local_browser import LocalBrowser
            self.local_browser=LocalBrowser(self);area.hide();split.insertWidget(0,self.local_browser);split.setSizes([290,0,850])
            self.local_browser.command_requested.connect(self.insert_local_command)
            self.sftp_button.hide(); self.scp_button.hide()
            self.password_button.hide();self.password_hint.hide()
    def saved_password(self):
        if self.vault.locked or self.credentials.protocol.currentText()!='SSH':return ''
        name=self.profile_bar.combo.currentText();profile=self.vault.profiles.get(name)
        if not profile:return ''
        from device_profiles import for_context
        try:settings=self.vault.resolve_login(for_context(profile,['SSH']))
        except ValueError:return ''
        active=self.last_settings or {}
        if settings.get('host')!=active.get('host') or int(settings.get('port',22))!=int(active.get('port',22)) or settings.get('user','')!=active.get('user',''):return ''
        return settings.get('password','')
    def update_password_button(self):self.password_button.setEnabled(bool(self.worker and self.worker.isRunning() and self.state.text()=='Connected' and self.saved_password()))
    def paste_saved_password(self):
        password=self.saved_password()
        if not password or not self.worker or not self.worker.isRunning() or self.state.text()!='Connected':return
        if any(ord(char)<32 or ord(char)==127 for char in password):QMessageBox.warning(self,'Saved password','This saved password contains control characters and cannot be pasted safely into a terminal.');return
        try:self.worker.send_secret(password.encode('utf-8'));self.terminal.reveal_live();self.terminal.setFocus()
        except Exception as error:QMessageBox.warning(self,'Password was not sent',str(error))
    def record_secret(self,length):
        self.tx+=length;self.counts.setText(f'RX {self.rx:,} B • TX {self.tx:,} B')
        if self.log:
            try:self.log.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(),direction='TX',text='[saved password redacted]',hex='',redacted=True))+'\n');self.log.flush()
            except OSError as error:self.log.close();self.log=None;self.log_button.setText('Log…');QMessageBox.warning(self,'Logging stopped',str(error))
    def insert_local_command(self,command):
        if self.input.text() and QMessageBox.question(self,'Replace command','Replace the existing command-bar text?')!=QMessageBox.Yes:return
        self.input.setText(command);self.input.setFocus()
    def find_text(self):
        if not self.terminal.find(self.search.text()):
            self.terminal.moveCursor(__import__('PyQt5.QtGui',fromlist=['QTextCursor']).QTextCursor.Start)
            self.terminal.find(self.search.text())
    def browse(self,protocol):
        if self.worker and self.worker.isRunning() and self.worker.ssh_handle:
            self.browse_requested.emit(self,protocol)
    def set_state(self,text):
        self.state.setText(text);self.terminal.set_session_active(text=='Connected');self.update_focus_hint()
        self.update_password_button()
        skip=getattr(self,'skip_companion_once',False)
        if text=='Connected':self.skip_companion_once=False
        if text=='Connected' and self.credentials.protocol.currentText()=='SSH' and (self.last_settings or {}).get('open_files'):
            if not skip:self.companion_requested.emit(self,self.last_settings.get('file_protocol','SFTP'))
        active=text=='Connected' and self.credentials.protocol.currentText()=='SSH'
        self.sftp_button.setEnabled(active); self.scp_button.setEnabled(active)
    def update_focus_hint(self,*args):
        connected=bool(self.worker and self.worker.isRunning() and self.state.text()=='Connected')
        self.focus_hint.setText(('Console keyboard active • Ctrl+Shift+V pastes' if self.terminal.hasFocus() else 'Click console to type • command bar sends a full line') if connected else 'Disconnected • connect before typing')
    def toggle(self):
        if self.worker and self.worker.isRunning(): self.worker.stop(); self.connect_button.setEnabled(False); return
        try: s = self.credentials.values()
        except ValueError as e: self.connection_error(str(e)); return
        s=self.vault.resolve_login(s)
        if hasattr(self,'local_browser'):s['local_cwd']=self.local_browser.terminal_directory
        if not self.credentials.confirm_tls(s): return
        self.last_settings={k:v for k,v in s.items() if k not in ('password','passphrase','key_data')}
        self.title_changed.emit(s.get('host') or 'Local terminal')
        self.worker = Session(s, self)
        if hasattr(self.worker,'credential_requested'):
            worker=self.worker;worker.credential_requested.connect(lambda title,prompt,secret:answer_credential(self,worker,title+' — '+s.get('host',''),prompt,secret))
        if hasattr(self,'local_browser'):self.worker.directory_changed.connect(self.local_browser.terminal_changed)
        self.worker.received.connect(self.receive); self.worker.sent.connect(lambda data: self.record('TX',data))
        if hasattr(self.worker,'secret_sent'):self.worker.secret_sent.connect(self.record_secret)
        self.worker.state.connect(self.set_state)
        self.worker.failed.connect(self.connection_error)
        self.worker.question.connect(self.trust)
        self.worker.finished.connect(self.finished)
        self.credentials.setEnabled(False); self.profile_bar.setEnabled(False); self.connect_button.setText('Disconnect'); self.state.setText('Connecting…')
        self.decoder.reset(); self.worker.start(); self.terminal.setFocus(); self.credentials.password.clear(); self.credentials.passphrase.clear(); self.credentials.key_data=''
    def connection_error(self,message):
        if getattr(self,'quiet_errors',False):self.terminal.feed('\r\n[Connection failed] '+message+'\r\n');self.focus_hint.setText(message)
        else:QMessageBox.warning(self,'Connection',message)
    def trust(self,title,text): self.worker.respond(QMessageBox.question(self,title,text) == QMessageBox.Yes)
    def finished(self):
        self.credentials.setEnabled(True); self.profile_bar.setEnabled(True); self.connect_button.setEnabled(True); self.connect_button.setText('Connect')
    def send(self,data):
        if self.worker and self.worker.isRunning():
            try: self.worker.send(data)
            except Exception as e: QMessageBox.warning(self,'Send failed',str(e))
    def send_line(self): self.terminal.reveal_live();self.send(self.input.text().encode() + b'\r')
    def receive(self,data): self.record('RX',data); self.terminal.feed(self.decoder.decode(data))
    def record(self,direction,data):
        if direction == 'RX': self.rx += len(data)
        else: self.tx += len(data)
        self.counts.setText(f'RX {self.rx:,} B • TX {self.tx:,} B')
        if self.log:
            try:
                self.log.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(), direction=direction,hex=data.hex(' '),text=data.decode(errors='replace'))) + '\n'); self.log.flush()
            except OSError as e:
                self.log.close(); self.log = None; self.log_button.setText('Log…'); QMessageBox.warning(self,'Logging stopped',str(e))
    def toggle_log(self):
        if self.log: self.log.close(); self.log = None; self.log_button.setText('Log…'); return
        path,_ = QFileDialog.getSaveFileName(self,'Session log',str(Path.home() / 'session.jsonl'),'JSONL (*.jsonl)')
        if path:
            try: self.log = open(path,'a'); self.log_button.setText('Stop log')
            except OSError as e: QMessageBox.warning(self,'Log',str(e))
    def prepare_close(self):return self.local_browser.prepare_close() if hasattr(self,'local_browser') else True
    def shutdown(self):
        if self.worker and self.worker.isRunning(): self.worker.stop(); self.worker.wait(30000)
        if hasattr(self,'local_browser'):self.local_browser.shutdown()
        if self.log: self.log.close(); self.log = None


class UploadTable(QTableWidget):
    files_dropped=pyqtSignal(list)
    def __init__(self,*args):
        super().__init__(*args); self.can_drop=lambda:False
        self.empty_label=QLabel(self.viewport());self.empty_label.setWordWrap(True);self.empty_label.setAlignment(Qt.AlignCenter);self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents);self.empty_label.setStyleSheet('background: transparent; color: #8aa1bb; padding: 20px');self.empty_label.hide()
        self.setAcceptDrops(True); self.setDragDropMode(QTableWidget.DropOnly)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'empty_label'):self.empty_label.setGeometry(self.viewport().rect())
    def dragEnterEvent(self,event):
        if self.can_drop() and event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()): event.acceptProposedAction()
        else: event.ignore()
    def dragMoveEvent(self,event): self.dragEnterEvent(event)
    def dropEvent(self,event):
        if self.can_drop() and event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            self.files_dropped.emit([url.toLocalFile() for url in event.mimeData().urls()]); event.acceptProposedAction()
        else: event.ignore()


class FileBrowser(QWidget):
    title_changed=pyqtSignal(str)
    def __init__(self,vault=None):
        super().__init__(); self.vault=vault or CredentialVault(); self.shared=None; self.owns_client=True; self.pending_transfers=0; self.cancel_requested=False; self.task = None; self.client = self.remote = None; self.protocol = None;self.editors=[]
        layout = QVBoxLayout(self); split = QSplitter(); layout.addWidget(split)
        side = QWidget(); left = QVBoxLayout(side)
        self.credentials = Credentials(['SFTP','SCP','FTP','FTPS']); left.addWidget(self.credentials)
        self.connect_button = button('Connect', self.connect_remote, left)
        self.profile_bar=ProfileBar(self.credentials,self.vault,self.connect_remote); left.insertWidget(0,self.profile_bar); left.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(side); scroll.setMinimumWidth(320); scroll.setMaximumWidth(350); split.addWidget(scroll)
        right = QWidget(); pane = QVBoxLayout(right); split.addWidget(right); split.setStretchFactor(1,1)
        header=QHBoxLayout();pane.addLayout(header);self.heading=QLabel('Remote File Manager — SFTP / SCP / FTP / FTPS');self.heading.setWordWrap(True);header.addWidget(self.heading,1);left.removeWidget(self.connect_button);header.addWidget(self.connect_button)
        row = QHBoxLayout(); pane.addLayout(row)
        self.path = QLineEdit('.');self.path.setAccessibleName('Remote directory path'); self.path.returnPressed.connect(self.refresh); row.addWidget(self.path,1)
        self.go_button=button('Go', self.refresh, row); self.up_button=button('Up', self.up, row)
        self.filter_files=QLineEdit();self.filter_files.setPlaceholderText('Filter remote filenames…');self.filter_files.setAccessibleName('Filter remote filenames');self.filter_files.textChanged.connect(self.filter_listing);pane.addWidget(self.filter_files)
        self.table = UploadTable(0,3);self.table.setAccessibleName('Remote directory contents');
        self.table.can_drop=lambda:self.remote is not None and not self.pending_transfers and not (self.task and self.task.isRunning())
        self.table.files_dropped.connect(self.upload_paths)
        self.table.setHorizontalHeaderLabels(['Name','Size','Type'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.doubleClicked.connect(self.open_item); pane.addWidget(self.table,1)
        self.setAcceptDrops(True)
        row = QHBoxLayout(); pane.addLayout(row)
        self.actions = []
        for label, cb in [('Upload…',self.upload),('Download…',self.download),('New folder…',self.mkdir),('Rename…',self.rename)]: self.actions.append(button(label,cb,row))
        row=QHBoxLayout();pane.addLayout(row)
        for label,cb in [('Edit text…',lambda:self.edit_text()),('Text options…',self.text_options),('Copy path',self.copy_path)]:self.actions.append(button(label,cb,row))
        self.cancel_button=QPushButton('Cancel transfers');self.cancel_button.clicked.connect(self.cancel_transfers);pane.addWidget(self.cancel_button);self.cancel_button.setEnabled(False)
        self.progress = QProgressBar(); self.progress.setRange(0,100); pane.addWidget(self.progress)
        self.status = QLabel('Remote File Manager • select a shared device or enter a host, then Connect.'); self.status.setWordWrap(True); pane.addWidget(self.status)
        self.set_busy(False)
    def run(self, function, complete,failed=None):
        if self.task and self.task.isRunning(): return
        self.task = Task(function,self)
        task=self.task;task.credential_requested.connect(lambda title,prompt,secret:answer_credential(self,task,title+' — '+self.credentials.host.text(),prompt,secret))
        self.task.question.connect(lambda title,text: self.task.respond(QMessageBox.question(self,title,text) == QMessageBox.Yes))
        self.task.progress.connect(self.progress.setValue)
        self.task.completed.connect(lambda result,error: self.complete(result,error,complete,failed))
        self.set_busy(True); self.task.start()
        return self.task
    def set_busy(self,busy):
        busy=busy or bool(self.pending_transfers)
        self.cancel_button.setEnabled(bool(self.pending_transfers) and not self.cancel_requested)
        self.connect_button.setEnabled(not busy)
        connected=self.remote is not None
        for a in self.actions:a.setEnabled(not busy and connected)
        for w in (self.table,self.path,self.go_button,self.up_button):w.setEnabled(not busy and connected)
        self.update_empty(busy)
    def complete(self,result,error,callback,failed=None):
        if error:self.pending_transfers=0;self.cancel_requested=False
        self.set_busy(False)
        if error:
            self.status.setText(error)
            if failed:failed(error)
            elif 'cancelled' not in error.lower() and not getattr(self,'quiet_errors',False):QMessageBox.warning(self,'Remote file operation',error)
        else: callback(result)
    def connect_remote(self):
        if self.remote:
            if self.prepare_close():self.close_remote()
            return
        try: s = self.credentials.values()
        except ValueError as e:
            self.status.setText(str(e))
            if not getattr(self,'quiet_errors',False):QMessageBox.warning(self,'Settings',str(e))
            return
        s=self.vault.resolve_login(s)
        if not self.credentials.confirm_tls(s): return
        def connect(task):
            if s['protocol'] in ('SFTP','SCP'):
                c = ssh_connect(s,task.ask,task.request_credential)
                try: r = c.open_sftp(); r.get_channel().settimeout(20)
                except Exception: c.close(); raise
                return s['protocol'],c,r
            import ssl
            if s['protocol'] == 'FTPS':
                ctx = ssl.create_default_context(cafile=s.get('ca') or None)
                if s['unverified']: ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
                r = ftplib.FTP_TLS(context=ctx,timeout=15)
            else: r = ftplib.FTP(timeout=15)
            try:
                r.connect(s['host'],s['port']); r.login(s['user'],s['password'])
                if s['protocol'] == 'FTPS': r.prot_p()
                return s['protocol'],None,r
            except Exception: r.close(); raise
        def ready(result):
            for secret in ('password','passphrase','key_data'): s.pop(secret,None)
            self.connected(result)
        self.run(connect,ready); self.credentials.password.clear(); self.credentials.passphrase.clear(); self.credentials.key_data=''
    def attach_ssh(self,handle,protocol,settings):
        self.credentials.apply(dict(settings,protocol=protocol)); self.credentials.setEnabled(False)
        self.shared=handle.acquire(); self.owns_client=False
        def connect(task):
            remote=self.shared.client.open_sftp(); remote.get_channel().settimeout(20)
            return protocol,self.shared.client,remote
        def completed(result,error):
            if error:
                self.shared.release(); self.shared=None; self.owns_client=True; self.credentials.setEnabled(True)
        self.run(connect,self.connected)
        self.task.completed.connect(completed)
    def connected(self,result):
        self.protocol,self.client,self.remote = result
        self.title_changed.emit(f'{self.protocol} Files · {self.credentials.host.text()}')
        self.heading.setText(f'Remote File Manager — {self.protocol} · {self.credentials.host.text()}')
        self.credentials.setEnabled(False); self.profile_bar.setEnabled(False); self.connect_button.setText('Disconnect'); self.path.setText(self.credentials.remote_path.text() or '.')
        self.set_busy(False)
        QTimer.singleShot(0,self.refresh)
    def refresh(self):
        if not self.remote or (self.task and self.task.isRunning()):
            if self.remote: QTimer.singleShot(50,self.refresh)
            return
        path = self.path.text() or '.'
        def listing(task):
            if self.client:
                real = self.remote.normalize(path)
                return real,[(a.filename,a.st_size,stat.S_ISDIR(a.st_mode)) for a in self.remote.listdir_attr(real)]
            self.remote.cwd(path); real = self.remote.pwd()
            try: return real,[(n,int(f.get('size',0)),f.get('type') == 'dir') for n,f in self.remote.mlsd() if f.get('type') not in ('cdir','pdir')]
            except ftplib.error_perm:
                rows=[]
                for n in self.remote.nlst():
                    name=posixpath.basename(n.rstrip('/'))
                    if name in ('.','..',''): continue
                    directory=False
                    try: self.remote.cwd(n); directory=True; self.remote.cwd(real)
                    except ftplib.error_perm: pass
                    rows.append((name,0,directory))
                return real,rows
        self.run(listing,self.listed)
    def listed(self,result):
        path,rows=result; self.path.setText(path); self.table.setRowCount(0)
        for name,size,directory in sorted(rows,key=lambda r:(not r[2],r[0].lower())):
            row=self.table.rowCount(); self.table.insertRow(row)
            for col,text in enumerate([name,str(size),'Folder' if directory else 'File']): self.table.setItem(row,col,QTableWidgetItem(text))
        self.status.setText(f'{len(rows)} items • {self.protocol} • double-click a folder or text file');self.filter_listing(self.filter_files.text()); self.progress.setValue(0)
    def update_empty(self,busy=False):
        visible=any(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        message=('Opening remote connection…' if busy else 'Connect to a remote device to browse files.\nUse a shared device above, or enter a host and Connect.') if self.remote is None else 'No filenames match the filter.' if self.table.rowCount() else 'This remote folder is empty.\nUpload files or create a folder.'
        self.table.empty_label.setText(message);self.table.empty_label.setGeometry(self.table.viewport().rect());self.table.empty_label.setVisible(not visible)
    def filter_listing(self,text):
        self.table.clearSelection()
        for row in range(self.table.rowCount()):self.table.setRowHidden(row,text.casefold() not in self.table.item(row,0).text().casefold())
        self.update_empty()
        if self.remote is not None:
            total=self.table.rowCount();count=sum(not self.table.isRowHidden(row) for row in range(total))
            self.status.setText((f'{count} of {total}' if count!=total else str(total))+f' items • {self.protocol} • double-click folders or text files')
    def selection(self):
        row=self.table.currentRow()
        if row<0 or self.table.isRowHidden(row):raise ValueError('Select a visible file or folder.')
        return self.table.item(row,0).text(),self.table.item(row,2).text() == 'Folder'
    def up(self): self.path.setText(posixpath.dirname(self.path.text().rstrip('/')) or '/'); self.refresh()
    def open_item(self):
        try: name,directory=self.selection()
        except ValueError: return
        if directory: self.path.setText(posixpath.join(self.path.text(),name)); self.refresh()
        else: self.edit_text()
    def copy_path(self):
        from PyQt5.QtWidgets import QApplication
        try:name,_=self.selection();QApplication.clipboard().setText(self.remote_path(name))
        except ValueError:pass
    def text_options(self):
        from PyQt5.QtWidgets import QMenu
        menu=QMenu(self);menu.addAction('Edit selected text file…',lambda:self.edit_text());menu.addAction('Open with encoding…',self.edit_with_encoding);menu.addAction('New remote text file…',self.new_text);menu.exec_(self.mapToGlobal(self.rect().center()))
    def edit_with_encoding(self):
        encoding,ok=QInputDialog.getItem(self,'Text encoding','Open selected file as:',list(ENCODINGS),0,False)
        if ok:self.edit_text(encoding)
    def edit_text(self,encoding=None):
        if not self.remote or self.pending_transfers or (self.task and self.task.isRunning()):return
        try:
            name,directory=self.selection()
            if directory:raise ValueError('Select a text file, not a folder.')
            path=self.remote_path(name)
        except ValueError as error:QMessageBox.information(self,'Edit remote text',str(error));return
        for editor in self.editors:
            if editor.snapshot['path']==path:editor.show();editor.raise_();editor.activateWindow();return
        store=RemoteTextStore(self.remote,sftp=bool(self.client))
        def read(task):
            snapshot=store.read(path);decode_text(snapshot['data'],encoding);return snapshot
        def ready(snapshot):
            editor=TextEditor(snapshot,self,remote=True,encoding=encoding,save_handler=lambda e,data:self.save_editor(e,store,data),reload_handler=lambda e:self.reload_editor(e,store))
            self.editors.append(editor);editor.finished.connect(lambda *_:self.editors.remove(editor) if editor in self.editors else None);editor.show()
        def failed(message):
            if store.disconnected:self.close_remote()
            QMessageBox.warning(self,'Cannot open remote text',message)
        self.run(read,ready,failed)
    def save_editor(self,editor,store,data):
        if not self.remote or self.pending_transfers or (self.task and self.task.isRunning()):editor.status.setText('Finish the current remote operation, then try Save again.');return
        editor.set_busy(True)
        def done(snapshot):editor.saved(snapshot);self.status.setText('Remote text saved: '+snapshot['path']);QTimer.singleShot(0,self.refresh)
        def failed(message):
            if store.disconnected:self.close_remote()
            editor.failed(message)
        self.run(lambda task:store.save(editor.snapshot,data),done,failed)
    def reload_editor(self,editor,store):
        if not self.remote or self.pending_transfers or (self.task and self.task.isRunning()):editor.status.setText('Finish the current remote operation, then try Reload again.');return
        editor.set_busy(True)
        def ready(snapshot):
            editor.set_busy(False)
            try:editor.load_snapshot(snapshot,editor.encoding.currentText())
            except (ValueError,UnicodeError) as error:editor.failed(str(error))
        def failed(message):
            if store.disconnected:self.close_remote()
            editor.failed(message)
        self.run(lambda task:store.read(editor.snapshot['path']),ready,failed)
    def new_text(self):
        if not self.remote or (self.task and self.task.isRunning()):return
        if not self.client:QMessageBox.information(self,'New remote text','Create a local text file and upload it, or use SFTP/SCP for exclusive remote creation.');return
        name,ok=QInputDialog.getText(self,'New remote text file','Filename:')
        if not ok or not name:return
        if name in ('.','..') or any(char in name for char in '/\\\r\n\0'):QMessageBox.warning(self,'Filename','Enter a single filename.');return
        path=self.remote_path(name)
        def create(task):
            with self.remote.open(path,'wx'):pass
            self.remote.chmod(path,0o600)
        self.run(create,lambda result:QTimer.singleShot(0,self.refresh))
    def remote_path(self,name): return posixpath.join(self.path.text(),name)
    def cancel_transfers(self):
        self.cancel_requested=True
        if self.task:self.task.requestInterruption()
        self.cancel_button.setEnabled(False);self.status.setText('Cancelling transfers… An interrupted upload may leave a partial remote file.')
    def transfer(self,local,remote,upload,done=None):
        def job(task):
            import tempfile
            from tftp_transfer import TransferCancelled
            target=local;partial=None
            if not upload:
                fd,path=tempfile.mkstemp(prefix='.serial-desk-',suffix='.part',dir=Path(local).parent);os.close(fd);partial=Path(path);local_path=str(partial)
            else:local_path=local
            def check():
                if self.cancel_requested or task.isInterruptionRequested():raise TransferCancelled('Transfer cancelled. An interrupted upload may leave a partial remote file.')
            try:
                check()
                return perform(task,local_path,check)
            finally:
                if partial:
                    if not self.cancel_requested and not task.isInterruptionRequested() and getattr(task,'download_ok',False):
                        try:partial.replace(target)
                        finally:partial.unlink(missing_ok=True)
                    else:partial.unlink(missing_ok=True)
        def perform(task,local,check):
            def progress(done,total):check();task.progress.emit(int(done*100/max(1,total)))
            if self.protocol == 'SCP':
                from scp import SCPClient
                with SCPClient(self.client.get_transport(),socket_timeout=20,progress=lambda f,total,done: progress(done,total)) as scp:
                    if upload: scp.put(local,remote_path=remote)
                    else: scp.get(remote,local_path=local)
            elif self.client:
                if upload: self.remote.put(local,remote,callback=progress)
                else: self.remote.get(remote,local,callback=progress)
            elif upload:
                total=Path(local).stat().st_size; done=0
                def sent(chunk):
                    nonlocal done; done+=len(chunk); progress(done,total)
                with open(local,'rb') as f: self.remote.storbinary('STOR '+remote,f,callback=sent)
            else:
                with open(local,'wb') as f:
                    def write(chunk):check();f.write(chunk)
                    self.remote.retrbinary('RETR '+remote,write)
            check();task.download_ok=True
            return 'Transfer complete'
        self.run(job,lambda result:(self.status.setText(result),self.progress.setValue(100),done() if done else None))
    def dragEnterEvent(self,event):
        if self.remote and not self.pending_transfers and not (self.task and self.task.isRunning()) and event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()): event.acceptProposedAction()
    def dropEvent(self,event):
        paths=[url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.upload_paths(paths); event.acceptProposedAction()
    def run_transfers(self,jobs):
        if self.cancel_requested:
            self.pending_transfers=0;self.cancel_requested=False;self.set_busy(False);self.status.setText('Transfers cancelled.');return
        self.pending_transfers=len(jobs)
        if not jobs: self.set_busy(False); QTimer.singleShot(0,self.refresh); return
        if self.task and self.task.isRunning(): QTimer.singleShot(50,lambda:self.run_transfers(jobs)); return
        local,remote,upload=jobs[0]
        self.transfer(local,remote,upload,done=lambda:QTimer.singleShot(0,lambda:self.run_transfers(jobs[1:])))
    def upload_paths(self,paths):
        if not self.remote: return
        files=[path for path in paths if Path(path).is_file()]
        if len(files)!=len(paths): QMessageBox.warning(self,'Upload','Select files. Folder uploads are not supported.'); return
        if not files: return
        if QMessageBox.question(self,'Upload files',f'Upload {len(files)} file(s) to {self.path.text()}? Existing remote files with matching names will be replaced.') != QMessageBox.Yes: return
        self.run_transfers([(path,self.remote_path(Path(path).name),True) for path in files])
    def upload(self):
        if not self.remote: return
        files,_=QFileDialog.getOpenFileNames(self,'Upload files')
        self.upload_paths(files)
    def download(self):
        if not self.remote: return
        names=[self.table.item(index.row(),0).text() for index in self.table.selectionModel().selectedRows() if self.table.item(index.row(),2).text()=='File']
        if not names: QMessageBox.warning(self,'Download','Select one or more files.'); return
        if len(names)==1:
            local,_=QFileDialog.getSaveFileName(self,'Download file',str(Path.home()/posixpath.basename(names[0])))
            if local: self.run_transfers([(local,self.remote_path(names[0]),False)])
        else:
            folder=QFileDialog.getExistingDirectory(self,'Download files to folder')
            if not folder: return
            jobs=[(str(Path(folder)/posixpath.basename(name)),self.remote_path(name),False) for name in names]
            if any(Path(local).exists() for local,_,_ in jobs) and QMessageBox.question(self,'Replace local files','Some destination files already exist. Replace them?') != QMessageBox.Yes: return
            self.run_transfers(jobs)
    def mkdir(self):
        if not self.remote: return
        name,ok=QInputDialog.getText(self,'New folder','Folder name:')
        if ok and name and name not in ('.','..') and '/' not in name and '\0' not in name:
            path=self.remote_path(name)
            self.run(lambda task:self.remote.mkdir(path) if self.client else self.remote.mkd(path),lambda r:QTimer.singleShot(0,self.refresh))
    def rename(self):
        if not self.remote: return
        try: old,_=self.selection()
        except ValueError as e: QMessageBox.warning(self,'Rename',str(e)); return
        if any(e.snapshot['path']==self.remote_path(old) for e in self.editors):QMessageBox.information(self,'Rename','Close the text editor for this file before renaming it.');return
        name,ok=QInputDialog.getText(self,'Rename','New name:',text=old)
        if ok and name and name not in ('.','..') and '/' not in name and '\0' not in name:
            if QMessageBox.question(self,'Rename',f'Rename {old} to {name}?') == QMessageBox.Yes:
                self.run(lambda task:self.remote.rename(self.remote_path(old),self.remote_path(name)),lambda r:QTimer.singleShot(0,self.refresh))
    def close_remote(self):
        if self.remote:
            try: self.remote.close()
            except Exception: pass
        if self.shared:
            self.shared.release(); self.shared=None
        elif self.client and self.owns_client: self.client.close()
        self.owns_client=True
        self.remote=self.client=None; self.credentials.setEnabled(True); self.profile_bar.setEnabled(True); self.connect_button.setText('Connect'); self.table.setRowCount(0);self.set_busy(False);self.status.setText('Disconnected • choose a shared device or enter a host.');self.heading.setText('Remote File Manager — SFTP / SCP / FTP / FTPS')
    def shutdown(self):
        if self.task and self.task.isRunning():
            self.task.requestInterruption(); self.task.respond(False)
            self.close_remote(); self.task.wait(30000)
        self.close_remote()
        for editor in list(self.editors):editor.text.document().setModified(False);editor.close()
    def prepare_close(self):
        for editor in list(self.editors):
            if not editor.close():return False
        return True


class Notepad(QWidget):
    def __init__(self):
        super().__init__(); layout=QVBoxLayout(self); row=QHBoxLayout(); layout.addLayout(row)
        button('Open…',self.open_file,row); button('Save copy…',self.save_file,row); row.addStretch()
        label=QLabel('Scratchpad • saved locally as you type'); row.addWidget(label)
        self.text=QPlainTextEdit(); self.text.setReadOnly(False); self.text.setFont(QFont('Consolas' if os.name=='nt' else 'DejaVu Sans Mono',11)); layout.addWidget(self.text)
        self.path=CONFIG/'notes.txt'; self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.persist)
        try:
            if self.path.exists(): self.text.setPlainText(self.path.read_text(encoding='utf-8'))
        except OSError as e: label.setText(str(e))
        self.text.textChanged.connect(lambda:self.timer.start(700))
    def persist(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix('.tmp'); tmp.write_text(self.text.toPlainText(),encoding='utf-8'); tmp.chmod(0o600); tmp.replace(self.path)
        except OSError as e: QMessageBox.warning(self,'Notes could not be saved',str(e))
    def open_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Open notes')
        if path:
            if self.text.toPlainText() and QMessageBox.question(self,'Replace scratchpad','Replace current notes? Save a copy first if needed.') != QMessageBox.Yes: return
            try: self.text.setPlainText(Path(path).read_text(encoding='utf-8'))
            except (OSError,UnicodeError) as e: QMessageBox.warning(self,'Notes',str(e))
    def save_file(self):
        path,_=QFileDialog.getSaveFileName(self,'Save notes copy',str(Path.home()/'notes.txt'))
        if path:
            try: Path(path).write_text(self.text.toPlainText(),encoding='utf-8')
            except OSError as e: QMessageBox.warning(self,'Notes',str(e))
    def shutdown(self): self.timer.stop(); self.persist()
