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
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QInputDialog, QScrollArea, QGroupBox)
from network_core import Session, Task, ssh_connect, CONFIG
from credential_vault import CredentialVault
from profile_ui import ProfileBar


def button(text, callback, layout):
    b = QPushButton(text); b.clicked.connect(callback); layout.addWidget(b); return b


class Credentials(QWidget):
    def __init__(self, protocols):
        super().__init__(); form = QFormLayout(self); self.form = form; form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.protocol = QComboBox(); self.protocol.addItems(protocols); form.addRow('Connection', self.protocol)
        self.host = QLineEdit(); self.host.setPlaceholderText('Hostname or IP address'); form.addRow('Host', self.host)
        self.port = QLineEdit(); self.port.setMaximumWidth(100); form.addRow('Port', self.port)
        self.user = QLineEdit(); form.addRow('Username', self.user)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password); form.addRow('Password', self.password)
        self.passphrase=QLineEdit(); self.passphrase.setEchoMode(QLineEdit.Password); form.addRow('SSH key passphrase',self.passphrase)
        self.key = QLineEdit(); self.key.setPlaceholderText('Optional SSH private key path'); form.addRow('SSH key', self.key)
        self.key_data=''; self.key.textEdited.connect(lambda:self.clear_stored_key())
        pick=QPushButton('Choose key file…'); pick.clicked.connect(self.choose_key); form.addRow(pick); self.key_pick=pick
        self.ca = QLineEdit(); self.ca.setPlaceholderText('Optional CA certificate file'); form.addRow('TLS CA', self.ca)
        self.unverified = QCheckBox('Allow unverified TLS certificate'); form.addRow(self.unverified)
        self.legacy = QCheckBox('Enable legacy TLS 1.0 / older ciphers'); form.addRow(self.legacy)
        self.notice = QLabel(); self.notice.setWordWrap(True); form.addRow(self.notice)
        self.protocol.currentTextChanged.connect(self.changed); self.changed()
    def changed(self):
        p = self.protocol.currentText(); self.port.setText(str({'SSH':22,'SFTP':22,'SCP':22,'FTP':21,'FTPS':21,'Telnet':23,'Mitel TLS Telnet':2000}.get(p,22)))
        local = p == 'Local terminal'; tls = p in ('Mitel TLS Telnet','FTPS')
        for w in (self.host, self.port): w.setEnabled(not local)
        for w in (self.user, self.password): w.setEnabled(p in ('SSH','SFTP','SCP','FTP','FTPS'))
        for w in (self.key,self.passphrase,self.key_pick): w.setEnabled(p in ('SSH','SFTP','SCP'))
        for w in (self.ca, self.unverified):
            w.setVisible(tls)
            label = self.form.labelForField(w)
            if label: label.setVisible(tls)
        self.legacy.setVisible(p == 'Mitel TLS Telnet')
        self.notice.setText('Telnet and FTP send traffic in plain text.' if p in ('Telnet','FTP') else
            'Mitel SSL/TLS Telnet, default port 2000. Legacy compatibility depends on the controller and OpenSSL build.' if p == 'Mitel TLS Telnet' else '')
    def values(self):
        p = self.protocol.currentText()
        if p != 'Local terminal' and not self.host.text().strip(): raise ValueError('Enter a host.')
        port = int(self.port.text())
        if not 1 <= port <= 65535: raise ValueError('Port must be between 1 and 65535.')
        return dict(protocol=p, host=self.host.text().strip(), port=port, user=self.user.text().strip(),
                    password=self.password.text(), passphrase=self.passphrase.text(), key=self.key.text().strip(), key_data=self.key_data, ca=self.ca.text().strip(),
                    unverified=self.unverified.isChecked(), legacy=self.legacy.isChecked())
    def clear_stored_key(self): self.key_data=''
    def choose_key(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose SSH private key',str(Path.home()/'.ssh'))
        if path: self.key.setText(path); self.key_data=''
    def apply(self,s):
        self.protocol.setCurrentText(s.get('protocol','SSH'))
        for name in ('host','port','user','key','ca','password','passphrase'):
            getattr(self,name).setText(str(s.get(name,'')))
        self.key_data=s.get('key_data','')
        for name in ('unverified','legacy'): getattr(self,name).setChecked(bool(s.get(name,False)))

    def confirm_tls(self, s):
        if s['protocol'] not in ('Mitel TLS Telnet','FTPS') or not (s['unverified'] or s['legacy']): return True
        return QMessageBox.question(self, 'TLS compatibility', 'This connection uses reduced TLS verification or legacy cryptography. Continue for this device?') == QMessageBox.Yes


class NetworkConsole(QWidget):
    browse_requested=pyqtSignal(object,str)
    title_changed=pyqtSignal(str)
    duplicate_requested=pyqtSignal(object)
    def __init__(self, terminal_class, local=False, vault=None):
        super().__init__(); self.vault=vault or CredentialVault(); self.last_settings=None; self.worker = None; self.log = None; self.rx = self.tx = 0
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        layout = QVBoxLayout(self)
        split = QSplitter(); layout.addWidget(split, 1)
        sidebar = QWidget(); left = QVBoxLayout(sidebar)
        self.credentials = Credentials(['Local terminal'] if local else ['SSH','Telnet','Mitel TLS Telnet'])
        left.addWidget(self.credentials)
        self.connect_button = button('Start terminal' if local else 'Connect', self.toggle, left)
        self.profile_bar=ProfileBar(self.credentials,self.vault,self.toggle); left.insertWidget(0,self.profile_bar)
        left.addStretch()
        area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(sidebar); area.setMinimumWidth(230); area.setMaximumWidth(350)
        split.addWidget(area)
        right = QWidget(); main = QVBoxLayout(right); split.addWidget(right); split.setStretchFactor(1, 1)
        row = QHBoxLayout(); main.addLayout(row)
        self.state = QLabel('Disconnected'); row.addWidget(self.state); row.addStretch()
        self.log_button = button('Log…', self.toggle_log, row); button('Clear', lambda: self.terminal.reset(), row)
        self.terminal = terminal_class(); self.terminal.outgoing.connect(self.send); main.addWidget(self.terminal, 1)
        row = QHBoxLayout(); main.addLayout(row)
        self.input = QLineEdit(); self.input.setPlaceholderText('Command'); row.addWidget(self.input, 1)
        self.input.returnPressed.connect(self.send_line)
        button('Send', self.send_line, row); button('Ctrl+C', lambda: self.send(b'\x03'), row)
        tools=QHBoxLayout(); main.addLayout(tools)
        self.sftp_button=button('SFTP ↗',lambda:self.browse('SFTP'),tools)
        self.scp_button=button('SCP ↗',lambda:self.browse('SCP'),tools)
        button('Duplicate tab',lambda:self.duplicate_requested.emit(self),tools)
        self.sftp_button.setEnabled(False); self.scp_button.setEnabled(False)
        self.search=QLineEdit(); self.search.setPlaceholderText('Find in console…'); self.search.returnPressed.connect(self.find_text)
        tools.addWidget(self.search,1)
        self.counts = QLabel('RX 0 • TX 0'); main.addWidget(self.counts)
        if local:
            self.credentials.hide(); self.profile_bar.hide(); area.setMaximumWidth(170); area.setMinimumWidth(150)
            self.sftp_button.hide(); self.scp_button.hide()
    def find_text(self):
        if not self.terminal.find(self.search.text()):
            self.terminal.moveCursor(__import__('PyQt5.QtGui',fromlist=['QTextCursor']).QTextCursor.Start)
            self.terminal.find(self.search.text())
    def browse(self,protocol):
        if self.worker and self.worker.isRunning() and self.worker.ssh_handle:
            self.browse_requested.emit(self,protocol)
    def set_state(self,text):
        self.state.setText(text)
        active=text=='Connected' and self.credentials.protocol.currentText()=='SSH'
        self.sftp_button.setEnabled(active); self.scp_button.setEnabled(active)
    def toggle(self):
        if self.worker and self.worker.isRunning(): self.worker.stop(); self.connect_button.setEnabled(False); return
        try: s = self.credentials.values()
        except ValueError as e: QMessageBox.warning(self,'Settings',str(e)); return
        if not self.credentials.confirm_tls(s): return
        self.last_settings={k:v for k,v in s.items() if k not in ('password','passphrase','key_data')}
        self.title_changed.emit(s.get('host') or 'Local terminal')
        self.worker = Session(s, self)
        self.worker.received.connect(self.receive); self.worker.sent.connect(lambda data: self.record('TX',data))
        self.worker.state.connect(self.set_state)
        self.worker.failed.connect(lambda msg: QMessageBox.warning(self,'Connection',msg))
        self.worker.question.connect(self.trust)
        self.worker.finished.connect(self.finished)
        self.credentials.setEnabled(False); self.profile_bar.setEnabled(False); self.connect_button.setText('Disconnect'); self.state.setText('Connecting…')
        self.decoder.reset(); self.worker.start(); self.terminal.setFocus(); self.credentials.password.clear(); self.credentials.passphrase.clear(); self.credentials.key_data=''
    def trust(self,title,text): self.worker.respond(QMessageBox.question(self,title,text) == QMessageBox.Yes)
    def finished(self):
        self.credentials.setEnabled(True); self.profile_bar.setEnabled(True); self.connect_button.setEnabled(True); self.connect_button.setText('Connect')
    def send(self,data):
        if self.worker and self.worker.isRunning():
            try: self.worker.send(data)
            except Exception as e: QMessageBox.warning(self,'Send failed',str(e))
    def send_line(self): self.send(self.input.text().encode() + b'\r')
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
    def shutdown(self):
        if self.worker and self.worker.isRunning(): self.worker.stop(); self.worker.wait(30000)
        if self.log: self.log.close(); self.log = None


class UploadTable(QTableWidget):
    files_dropped=pyqtSignal(list)
    def __init__(self,*args):
        super().__init__(*args); self.can_drop=lambda:False
        self.setAcceptDrops(True); self.setDragDropMode(QTableWidget.DropOnly)
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
        super().__init__(); self.vault=vault or CredentialVault(); self.shared=None; self.owns_client=True; self.pending_transfers=0; self.task = None; self.client = self.remote = None; self.protocol = None
        layout = QVBoxLayout(self); split = QSplitter(); layout.addWidget(split)
        side = QWidget(); left = QVBoxLayout(side)
        self.credentials = Credentials(['SFTP','SCP','FTP','FTPS']); left.addWidget(self.credentials)
        self.connect_button = button('Connect', self.connect_remote, left)
        self.profile_bar=ProfileBar(self.credentials,self.vault,self.connect_remote); left.insertWidget(0,self.profile_bar); left.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(side); scroll.setMinimumWidth(230); scroll.setMaximumWidth(350); split.addWidget(scroll)
        right = QWidget(); pane = QVBoxLayout(right); split.addWidget(right); split.setStretchFactor(1,1)
        row = QHBoxLayout(); pane.addLayout(row)
        self.path = QLineEdit('.'); self.path.returnPressed.connect(self.refresh); row.addWidget(self.path,1)
        button('Go', self.refresh, row); button('Up', self.up, row)
        self.table = UploadTable(0,3);
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
        self.progress = QProgressBar(); self.progress.setRange(0,100); pane.addWidget(self.progress)
        self.status = QLabel('SCP mode browses through SFTP and transfers using the legacy SCP wire protocol.'); self.status.setWordWrap(True); pane.addWidget(self.status)
    def run(self, function, complete):
        if self.task and self.task.isRunning(): return
        self.task = Task(function,self)
        self.task.question.connect(lambda title,text: self.task.respond(QMessageBox.question(self,title,text) == QMessageBox.Yes))
        self.task.progress.connect(self.progress.setValue)
        self.task.completed.connect(lambda result,error: self.complete(result,error,complete))
        self.set_busy(True); self.task.start()
    def set_busy(self,busy):
        busy=busy or bool(self.pending_transfers)
        self.connect_button.setEnabled(not busy)
        for a in self.actions: a.setEnabled(not busy)
        self.table.setEnabled(not busy); self.path.setEnabled(not busy)
    def complete(self,result,error,callback):
        if error: self.pending_transfers=0
        self.set_busy(False)
        if error: self.status.setText(error); QMessageBox.warning(self,'File transfer',error)
        else: callback(result)
    def connect_remote(self):
        if self.remote:
            self.close_remote(); return
        try: s = self.credentials.values()
        except ValueError as e: QMessageBox.warning(self,'Settings',str(e)); return
        if not self.credentials.confirm_tls(s): return
        def connect(task):
            if s['protocol'] in ('SFTP','SCP'):
                c = ssh_connect(s,task.ask)
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
        self.title_changed.emit(f'{self.protocol} · {self.credentials.host.text()}')
        self.credentials.setEnabled(False); self.profile_bar.setEnabled(False); self.connect_button.setText('Disconnect'); self.path.setText('.')
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
        self.status.setText(f'{len(rows)} items • {self.protocol}'); self.progress.setValue(0)
    def selection(self):
        row=self.table.currentRow()
        if row < 0: raise ValueError('Select a file or folder.')
        return self.table.item(row,0).text(),self.table.item(row,2).text() == 'Folder'
    def up(self): self.path.setText(posixpath.dirname(self.path.text().rstrip('/')) or '/'); self.refresh()
    def open_item(self):
        try: name,directory=self.selection()
        except ValueError: return
        if directory: self.path.setText(posixpath.join(self.path.text(),name)); self.refresh()
        else: self.download()
    def remote_path(self,name): return posixpath.join(self.path.text(),name)
    def transfer(self,local,remote,upload,done=None):
        def job(task):
            def progress(done,total): task.progress.emit(int(done*100/max(1,total)))
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
                with open(local,'wb') as f: self.remote.retrbinary('RETR '+remote,f.write)
            return 'Transfer complete'
        self.run(job,lambda result:(self.status.setText(result),self.progress.setValue(100),done() if done else None))
    def dragEnterEvent(self,event):
        if self.remote and not self.pending_transfers and not (self.task and self.task.isRunning()) and event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()): event.acceptProposedAction()
    def dropEvent(self,event):
        paths=[url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.upload_paths(paths); event.acceptProposedAction()
    def run_transfers(self,jobs):
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
        if ok and name and '/' not in name:
            path=self.remote_path(name)
            self.run(lambda task:self.remote.mkdir(path) if self.client else self.remote.mkd(path),lambda r:QTimer.singleShot(0,self.refresh))
    def rename(self):
        if not self.remote: return
        try: old,_=self.selection()
        except ValueError as e: QMessageBox.warning(self,'Rename',str(e)); return
        name,ok=QInputDialog.getText(self,'Rename','New name:',text=old)
        if ok and name and '/' not in name:
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
        self.remote=self.client=None; self.credentials.setEnabled(True); self.profile_bar.setEnabled(True); self.connect_button.setText('Connect'); self.table.setRowCount(0)
    def shutdown(self):
        if self.task and self.task.isRunning():
            self.task.requestInterruption(); self.task.respond(False)
            self.close_remote(); self.task.wait(30000)
        self.close_remote()


class Notepad(QWidget):
    def __init__(self):
        super().__init__(); layout=QVBoxLayout(self); row=QHBoxLayout(); layout.addLayout(row)
        button('Open…',self.open_file,row); button('Save copy…',self.save_file,row); row.addStretch()
        label=QLabel('Scratchpad • saved locally as you type'); row.addWidget(label)
        self.text=QPlainTextEdit(); self.text.setReadOnly(False); self.text.setFont(QFont('DejaVu Sans Mono',11)); layout.addWidget(self.text)
        self.path=CONFIG/'notes.txt'; self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.persist)
        try:
            if self.path.exists(): self.text.setPlainText(self.path.read_text())
        except OSError as e: label.setText(str(e))
        self.text.textChanged.connect(lambda:self.timer.start(700))
    def persist(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix('.tmp'); tmp.write_text(self.text.toPlainText()); tmp.chmod(0o600); tmp.replace(self.path)
        except OSError as e: QMessageBox.warning(self,'Notes could not be saved',str(e))
    def open_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Open notes')
        if path:
            if self.text.toPlainText() and QMessageBox.question(self,'Replace scratchpad','Replace current notes? Save a copy first if needed.') != QMessageBox.Yes: return
            try: self.text.setPlainText(Path(path).read_text())
            except (OSError,UnicodeError) as e: QMessageBox.warning(self,'Notes',str(e))
    def save_file(self):
        path,_=QFileDialog.getSaveFileName(self,'Save notes copy',str(Path.home()/'notes.txt'))
        if path:
            try: Path(path).write_text(self.text.toPlainText())
            except OSError as e: QMessageBox.warning(self,'Notes',str(e))
    def shutdown(self): self.timer.stop(); self.persist()
