"""Shared saved-profile controls and optional application unlock UI."""
import json
from pathlib import Path
from PyQt5.QtCore import QTimer, QSettings, pyqtSignal
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QCheckBox,QPushButton,QInputDialog,QLineEdit,QMessageBox
from credential_vault import VaultError


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
        self.combo=QComboBox(); self.combo.setPlaceholderText('Choose a saved profile…'); layout.addWidget(self.combo)
        options=QWidget(); options_layout=QVBoxLayout(options); options_layout.setContentsMargins(0,0,0,0)
        self.remember=QCheckBox('Save password / key passphrase'); options_layout.addWidget(self.remember)
        self.store_key=QCheckBox('Store a private-key copy in vault'); options_layout.addWidget(self.store_key)
        self.auto=QCheckBox('Auto-connect when profile selected'); self.auto.setChecked(True); options_layout.addWidget(self.auto)
        row=QHBoxLayout(); layout.addLayout(row)
        for name,callback in [('Load',self.load),('Save…',self.save),('Delete',self.delete)]:
            b=QPushButton(name); b.clicked.connect(callback); row.addWidget(b)
        expand=QPushButton('Profile save options…'); expand.setCheckable(True); expand.toggled.connect(options.setVisible); layout.addWidget(expand)
        layout.addWidget(options); options.hide()
        self.combo.activated[str].connect(self.selected)
        self.refresh()
    def available(self):
        if self.vault.locked: return {}
        protocols=[self.credentials.protocol.itemText(i) for i in range(self.credentials.protocol.count())]
        return {name:value for name,value in self.vault.profiles.items() if value.get('protocol') in protocols}
    def refresh(self):
        current=self.combo.currentText(); self.combo.blockSignals(True); self.combo.clear(); self.combo.addItems(sorted(self.available()))
        self.combo.setCurrentIndex(self.combo.findText(current)); self.combo.blockSignals(False)
    def selected(self,name): self.load(auto=True)
    def load(self,auto=False):
        s=self.available().get(self.combo.currentText())
        if not s or not self.credentials.isEnabled(): return
        self.credentials.apply(s)
        self.remember.setChecked(bool(s.get('remember',False))); self.store_key.setChecked(bool(s.get('key_data')))
        self.auto.setChecked(bool(s.get('auto_connect',False)))
        if auto and self.auto.isChecked(): QTimer.singleShot(0,self.connect_callback)
    def save(self):
        try:
            s=self.credentials.values(); name,ok=QInputDialog.getText(self,'Save profile','Profile name:',text=self.combo.currentText())
            if not ok or not name.strip(): return
            name=name.strip()
            if name in self.vault.profiles and QMessageBox.question(self,'Replace profile',f'Replace {name}?') != QMessageBox.Yes: return
            s.update(auto_connect=self.auto.isChecked(),remember=self.remember.isChecked())
            if not self.remember.isChecked():
                s.pop('password',None); s.pop('passphrase',None)
            if self.store_key.isChecked():
                if not s.get('key_data'):
                    if not s.get('key'): raise ValueError('Choose a private key file first.')
                    path=Path(s['key']).expanduser()
                    if path.stat().st_size>131072: raise ValueError('Private key file is unexpectedly large.')
                    s['key_data']=path.read_text()
                from network_core import private_key_from_text
                private_key_from_text(s['key_data'],self.credentials.passphrase.text() or self.credentials.password.text())
            else: s.pop('key_data',None)
            self.vault.save_profile(name,s); self.refresh(); self.combo.setCurrentText(name); self.changed.emit()
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
        if QMessageBox.question(self.window,'Remove master password','Use a local account-protected key instead? Anyone with access to your Linux account can then unlock saved credentials.') != QMessageBox.Yes: return
        try: self.vault.remove_master_password(password)
        except (VaultError,OSError) as e: QMessageBox.warning(self.window,'Master password',str(e))
    def explain(self):
        mode='Master-password protection is enabled.' if self.vault.master_enabled else 'Local account protection is enabled. Set a master password for stronger protection.'
        QMessageBox.information(self.window,'Credential protection',mode+'\n\nSaved credentials and private-key copies are encrypted. Your original key files, notes, serial profiles, exports and session logs are separate files and are not encrypted by this setting. Logs may include passwords typed into consoles.')
