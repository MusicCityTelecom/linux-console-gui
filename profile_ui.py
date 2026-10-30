"""Shared saved-profile controls and optional application unlock UI."""
import json
from pathlib import Path
from PyQt5.QtCore import QTimer, QSettings, pyqtSignal, Qt
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QCheckBox,QPushButton,QInputDialog,QLineEdit,QMessageBox,QDialog
from credential_vault import VaultError
from device_profiles import compatible,for_context,merge_profile


def unlock_vault(vault,parent=None):
    while vault.locked:
        password,ok=QInputDialog.getText(parent,'Unlock Serial Desk','Master password (Cancel exits):',QLineEdit.Password)
        if not ok: return False
        try: vault.unlock(password)
        except (VaultError,OSError) as e: QMessageBox.warning(parent,'Unable to unlock',str(e))
    return True


class ProfileBar(QWidget):
    changed=pyqtSignal()
    def __init__(self,credentials,vault,connect_callback):
        super().__init__(); self.credentials=credentials; self.vault=vault; self.connect_callback=connect_callback
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.filter_group=QComboBox();self.filter_group.setToolTip('Filter shared devices by group');self.filter_group.setAccessibleName('Device group filter');layout.addWidget(self.filter_group)
        self.search=QLineEdit();self.search.setPlaceholderText('Find device or host…');self.search.setAccessibleName('Find saved device or host');layout.addWidget(self.search)
        self.combo=QComboBox(); self.combo.setPlaceholderText('Choose a shared device…');self.combo.setAccessibleName('Saved device'); layout.addWidget(self.combo)
        options=QWidget(); options_layout=QVBoxLayout(options); options_layout.setContentsMargins(0,0,0,0)
        self.group=QComboBox();self.group.setEditable(True);self.group.setAccessibleName('Device group name');self.group.setToolTip('Group for this device, for example Customers / Site A');self.group.setPlaceholderText('Device group (optional)');options_layout.addWidget(self.group)
        self.remember=QCheckBox('Remember password / passphrase'); options_layout.addWidget(self.remember)
        self.store_key=QCheckBox('Store a private-key copy'); options_layout.addWidget(self.store_key)
        self.auto=QCheckBox('Auto-connect on selection'); self.auto.setChecked(True); options_layout.addWidget(self.auto)
        row=QHBoxLayout(); layout.addLayout(row)
        for name,callback in [('Load',self.load),('Save…',self.save),('Delete',self.delete)]:
            b=QPushButton(name); b.clicked.connect(callback); row.addWidget(b)
        expand=QPushButton('Profile save options…'); expand.setCheckable(True); expand.toggled.connect(options.setVisible); layout.addWidget(expand)
        layout.addWidget(options); options.hide()
        self.combo.activated[str].connect(self.selected)
        self.filter_group.currentTextChanged.connect(lambda:self.refresh(update_groups=False))
        self.search.textChanged.connect(lambda:self.refresh(update_groups=False))
        for combo in (self.combo,self.group,self.filter_group):combo.setMinimumContentsLength(12);combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.refresh()
    def available(self):
        if self.vault.locked:return {}
        protocols=[self.credentials.protocol.itemText(i) for i in range(self.credentials.protocol.count())]
        return {name:value for name,value in self.vault.profiles.items() if compatible(value,protocols)}
    def refresh(self,update_groups=True):
        current=self.combo.currentText();profiles=self.available()
        if update_groups:
            selected=self.filter_group.currentText();group=self.group.currentText()
            groups=sorted({p.get('group','').strip() for p in self.vault.profiles.values() if p.get('group','').strip()})
            self.filter_group.blockSignals(True);self.filter_group.clear();self.filter_group.addItems(['All groups','Ungrouped']+groups)
            self.filter_group.setCurrentText(selected if selected in ['All groups','Ungrouped']+groups else 'All groups');self.filter_group.blockSignals(False)
            self.group.clear();self.group.addItems(groups);self.group.setEditText(group)
        filter_group=self.filter_group.currentText();query=self.search.text().strip().casefold()
        self.combo.blockSignals(True);self.combo.clear()
        for name,profile in sorted(profiles.items()):
            group=profile.get('group','').strip()
            if filter_group=='Ungrouped' and group:continue
            if filter_group not in ('','All groups','Ungrouped') and group!=filter_group:continue
            if query and query not in (name+' '+profile.get('host','')+' '+group).casefold():continue
            self.combo.addItem(name)
            self.combo.setItemData(self.combo.count()-1,profile.get('host','')+' • '+(group or 'Ungrouped'),Qt.ToolTipRole)
        self.combo.setCurrentIndex(self.combo.findText(current));self.combo.blockSignals(False)
    def selected(self,name): self.load(auto=True)
    def load(self,auto=False):
        s=self.available().get(self.combo.currentText())
        if not s or not self.credentials.isEnabled(): return
        protocols=[self.credentials.protocol.itemText(i) for i in range(self.credentials.protocol.count())]
        adapted=for_context(s,protocols,self.credentials.protocol.currentText())
        self.credentials.apply(adapted)
        self.group.setEditText(s.get('group',''))
        self.remember.setChecked(bool(adapted.get('remember',False))); self.store_key.setChecked(bool(adapted.get('key_data')))
        self.auto.setChecked(bool(adapted.get('auto_connect',False)))
        if auto and self.auto.isChecked(): QTimer.singleShot(0,self.connect_callback)
    def save(self):
        try:
            s=self.credentials.values(); name,ok=QInputDialog.getText(self,'Save profile','Profile name:',text=self.combo.currentText())
            if not ok or not name.strip(): return
            name=name.strip()
            if name in self.vault.profiles and QMessageBox.question(self,'Replace profile',f'Replace {name}?') != QMessageBox.Yes: return
            s.update(group=self.group.currentText().strip(),auto_connect=self.auto.isChecked(),remember=self.remember.isChecked())
            if not self.remember.isChecked():
                s['password']='';s['passphrase']=''
            if self.store_key.isChecked():
                if not s.get('key_data'):
                    if not s.get('key'): raise ValueError('Choose a private key file first.')
                    path=Path(s['key']).expanduser()
                    if path.stat().st_size>131072: raise ValueError('Private key file is unexpectedly large.')
                    s['key_data']=path.read_text()
                from network_core import private_key_from_text
                private_key_from_text(s['key_data'],self.credentials.passphrase.text() or self.credentials.password.text())
            else:s['key_data']=''
            s=merge_profile(self.vault.profiles.get(name,{}),s)
            self.vault.save_profile(name,s); self.filter_group.setCurrentText('All groups');self.search.clear();self.refresh(); self.combo.setCurrentText(name); self.changed.emit()
        except (ValueError,VaultError,OSError) as e: QMessageBox.warning(self,'Profile could not be saved',str(e))
    def delete(self):
        name=self.combo.currentText()
        if name in self.available() and QMessageBox.question(self,'Delete profile',f'Delete {name}?') == QMessageBox.Yes:
            try: self.vault.delete_profile(name); self.refresh(); self.changed.emit()
            except (VaultError,OSError) as e: QMessageBox.warning(self,'Profile',str(e))


class SecurityMenu:
    def __init__(self,window,vault):
        self.window=window; self.vault=vault
        menu=window.menuBar().addMenu('Security')
        menu.addAction('Set / change master password…',self.set_password)
        menu.addAction('Remove master password…',self.remove_password)
        menu.addAction('Credential protection…',self.explain)
    def current_password(self):
        if not self.vault.master_enabled: return ''
        password,ok=QInputDialog.getText(self.window,'Verify master password','Current master password:',QLineEdit.Password)
        if not ok: return None
        if not self.vault.verify(password): QMessageBox.warning(self.window,'Master password','Incorrect master password.'); return None
        return password
    def set_password(self):
        if self.current_password() is None: return
        password,ok=QInputDialog.getText(self.window,'Master password','New master password (at least 8 characters):',QLineEdit.Password)
        if not ok: return
        repeat,ok=QInputDialog.getText(self.window,'Confirm master password','Enter it again:',QLineEdit.Password)
        if not ok: return
        if repeat!=password: QMessageBox.warning(self.window,'Master password','The passwords do not match.'); return
        try:
            self.vault.set_master_password(password)
            QMessageBox.information(self.window,'Master password enabled','The password will be required when opening Serial Desk. There is no password recovery; keep a secure backup. Existing sessions can continue.')
        except (VaultError,OSError) as e: QMessageBox.warning(self.window,'Master password',str(e))
    def remove_password(self):
        if not self.vault.master_enabled: return
        password=self.current_password()
        if password is None: return
        if QMessageBox.question(self.window,'Remove master password','Use a local account-protected key instead? Anyone with access to your operating-system account can then unlock saved credentials.') != QMessageBox.Yes: return
        try: self.vault.remove_master_password(password)
        except (VaultError,OSError) as e: QMessageBox.warning(self.window,'Master password',str(e))
    def explain(self):
        mode='Master-password protection is enabled.' if self.vault.master_enabled else 'Local account protection is enabled. Set a master password for stronger protection.'
        QMessageBox.information(self.window,'Credential protection',mode+'\n\nSaved credentials and private-key copies are encrypted. Your original key files, notes, serial profiles, exports and session logs are separate files and are not encrypted by this setting. Logs may include passwords typed into consoles.')


class DeviceManager(QDialog):
    open_device=pyqtSignal(str,bool)
    changed=pyqtSignal()
    def __init__(self,vault,parent=None):
        from PyQt5.QtWidgets import QTableWidget,QHeaderView,QTableWidgetItem
        super().__init__(parent);self.vault=vault;self.setWindowTitle('Saved devices and groups');self.resize(820,460)
        layout=QVBoxLayout(self);self.search=QLineEdit();self.search.setPlaceholderText('Find a device, group, or host…');layout.addWidget(self.search)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Device','Group','Host','Console','Remote files']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows);self.table.setSelectionMode(QTableWidget.SingleSelection);self.table.setEditTriggers(QTableWidget.NoEditTriggers);layout.addWidget(self.table)
        row=QHBoxLayout();layout.addLayout(row)
        self.shell=QPushButton('Connect console');self.files=QPushButton('Open remote files');row.addWidget(self.shell);row.addWidget(self.files)
        for name,callback in [('Set group…',self.set_group),('Rename…',self.rename),('Close',self.accept)]:
            b=QPushButton(name);b.clicked.connect(callback);row.addWidget(b)
        self.shell.clicked.connect(lambda:self.launch(False));self.files.clicked.connect(lambda:self.launch(True))
        self.search.textChanged.connect(self.refresh);self.table.itemSelectionChanged.connect(self.update_buttons);self.refresh()
    def selected(self):
        row=self.table.currentRow();return self.table.item(row,0).text() if row>=0 else ''
    def refresh(self,*args):
        from PyQt5.QtWidgets import QTableWidgetItem
        current=self.selected();query=self.search.text().casefold();self.table.setRowCount(0)
        for name,p in sorted(self.vault.profiles.items()):
            if query and query not in (name+' '+p.get('group','')+' '+p.get('host','')).casefold():continue
            row=self.table.rowCount();self.table.insertRow(row)
            shell='SSH' if compatible(p,['SSH']) else p.get('protocol','') if p.get('protocol') in ('Telnet','Mitel TLS Telnet') else '—'
            files=p.get('file_protocol',p.get('protocol') if p.get('protocol') in ('SFTP','SCP','FTP','FTPS','TFTP') else 'SFTP' if p.get('protocol')=='SSH' else '—')
            for col,text in enumerate((name,p.get('group','') or 'Ungrouped',p.get('host',''),shell,files)):self.table.setItem(row,col,QTableWidgetItem(text))
            if name==current:self.table.selectRow(row)
        if not self.selected() and self.table.rowCount():self.table.selectRow(0)
        self.update_buttons()
    def update_buttons(self):
        p=self.vault.profiles.get(self.selected(),{});self.shell.setEnabled(bool(p) and compatible(p,['SSH','Telnet','Mitel TLS Telnet']));self.files.setEnabled(bool(p) and compatible(p,['SFTP','SCP','FTP','FTPS','TFTP']))
    def launch(self,files):
        name=self.selected()
        if name:self.open_device.emit(name,files);self.accept()
    def set_group(self):
        name=self.selected()
        if not name:return
        p=self.vault.profiles[name];groups=sorted({v.get('group','') for v in self.vault.profiles.values()})
        group,ok=QInputDialog.getItem(self,'Device group','Choose or enter a group (blank = Ungrouped):',groups,0,True)
        if ok:
            try:p['group']=group.strip();self.vault.save_profile(name,p);self.refresh();self.changed.emit()
            except (VaultError,OSError) as e:QMessageBox.warning(self,'Device group',str(e))
    def rename(self):
        old=self.selected()
        if not old:return
        name,ok=QInputDialog.getText(self,'Rename device','New device name:',text=old)
        if ok and name.strip() and name.strip()!=old:
            try:self.vault.rename_profile(old,name.strip());self.refresh();self.changed.emit()
            except (VaultError,OSError) as e:QMessageBox.warning(self,'Rename device',str(e))
