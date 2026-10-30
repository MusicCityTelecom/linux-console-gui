"""Optional user-maintained vault entries; no automatic browser access."""
import secrets
import string
from pathlib import Path
from urllib.parse import urlparse
from PyQt5.QtCore import QObject,QTimer,Qt
from PyQt5.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QLineEdit,QPlainTextEdit,QCheckBox,QPushButton,QFileDialog,QMessageBox,QTableWidget,
    QTableWidgetItem,QHeaderView,QScrollArea,QWidget)
from credential_vault import VaultError,display_url


def generate_password(length=24):
    groups=(string.ascii_lowercase,string.ascii_uppercase,string.digits,'!@#$%*-_=+?')
    chars=[secrets.choice(group) for group in groups]
    chars.extend(secrets.choice(''.join(groups)) for _ in range(max(4,length)-4))
    secrets.SystemRandom().shuffle(chars);return ''.join(chars)


class ClipboardGuard(QObject):
    def __init__(self,parent=None):
        super().__init__(parent);self.secret=None;self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.clear)
    def copy(self,text,timeout=30000):
        self.clear();self.secret=text;QApplication.clipboard().setText(text);self.timer.start(timeout)
    def clear(self):
        self.timer.stop()
        if self.secret is not None and QApplication.clipboard().text()==self.secret:QApplication.clipboard().clear()
        self.secret=None


class PasswordEditor(QDialog):
    def __init__(self,entry=None,parent=None):
        super().__init__(parent);entry=entry or {};self.key_data=entry.get('key_data','');self.setWindowTitle('Vault entry');self.resize(520,620)
        outer=QVBoxLayout(self);area=QScrollArea();area.setWidgetResizable(True);body=QWidget();form=QFormLayout(body);form.setRowWrapPolicy(QFormLayout.WrapAllRows);area.setWidget(body);outer.addWidget(area)
        self.fields={}
        for label,key in [('Name','name'),('Group','group'),('Website / host (optional)','url'),('Username','user'),('Password','password'),('Private key path (optional)','key'),('Key passphrase','passphrase')]:
            field=QLineEdit(entry.get(key,''));self.fields[key]=field;form.addRow(label,field)
            if key in ('password','passphrase'):field.setEchoMode(QLineEdit.Password)
        reveal=QCheckBox('Show password and passphrase');reveal.toggled.connect(lambda shown:[self.fields[k].setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password) for k in ('password','passphrase')]);form.addRow(reveal)
        generator=QPushButton('Generate a 24-character password');generator.clicked.connect(lambda:self.fields['password'].setText(generate_password()));form.addRow(generator)
        self.key_label=QLabel();form.addRow(self.key_label);self.update_key_label()
        row=QHBoxLayout();form.addRow(row)
        for title,callback in [('Choose key path…',self.choose_key),('Store key copy…',self.copy_key),('Remove key copy',self.remove_key)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        self.fields['key'].textEdited.connect(self.remove_key)
        self.notes=QPlainTextEdit(entry.get('notes',''));self.notes.setMaximumHeight(130);form.addRow('Notes (encrypted)',self.notes)
        hint=QLabel('Generated passwords are saved here only; they do not change any device or website account.');hint.setWordWrap(True);form.addRow(hint)
        row=QHBoxLayout();outer.addLayout(row)
        for title,callback in [('Cancel',self.reject),('Save entry',self.accept)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
    def values(self):return dict({k:v.text().strip() if k not in ('password','passphrase') else v.text() for k,v in self.fields.items()},notes=self.notes.toPlainText(),key_data=self.key_data)
    def accept(self):
        if not self.fields['name'].text().strip():QMessageBox.information(self,'Vault entry','Enter a name.');return
        super().accept()
    def update_key_label(self):self.key_label.setText('An encrypted private-key copy is stored in this entry.' if self.key_data else 'No private-key copy stored.')
    def remove_key(self,*args):self.key_data='';self.update_key_label()
    def choose_key(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose private key path',str(Path.home()/'.ssh'))
        if path:self.fields['key'].setText(path);self.remove_key()
    def copy_key(self):
        path,_=QFileDialog.getOpenFileName(self,'Store an encrypted private-key copy',str(Path.home()/'.ssh'))
        if not path:return
        try:
            source=Path(path)
            if source.stat().st_size>131072:raise ValueError('The key file is unexpectedly large.')
            text=source.read_text();from network_core import private_key_from_text
            private_key_from_text(text,self.fields['passphrase'].text() or self.fields['password'].text())
            self.key_data=text;self.fields['key'].setText(path);self.update_key_label()
        except Exception as error:QMessageBox.warning(self,'Private key',str(error)+'\nFor encrypted keys, enter the passphrase first. PuTTY PPK keys must be exported as OpenSSH keys using PuTTYgen.')


class PasswordManager(QDialog):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.vault=window.vault;self.setWindowTitle('Password manager')
        screen=self.screen().availableGeometry();self.resize(min(850,screen.width()-40),min(560,screen.height()-60))
        layout=QVBoxLayout(self);mode='Master-password protection is enabled.' if self.vault.master_enabled else 'Using local account protection. You can set a master password in Security.'
        hint=QLabel('Optional: add your own logins, or import a browser password CSV. '+mode);hint.setWordWrap(True);layout.addWidget(hint)
        self.search=QLineEdit();self.search.setPlaceholderText('Find by name, username, host or group…');layout.addWidget(self.search)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Name','Group','Website / host','Username','Secrets']);self.table.setSelectionBehavior(self.table.SelectRows);self.table.setSelectionMode(self.table.SingleSelection);self.table.setEditTriggers(self.table.NoEditTriggers);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);layout.addWidget(self.table)
        row=QHBoxLayout();layout.addLayout(row)
        for title,callback in [('Add…',lambda:self.edit()),('Edit…',lambda:self.edit(self.selected())),('Delete',self.delete),('Import…',self.import_entries)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        row=QHBoxLayout();layout.addLayout(row)
        for title,callback in [('Copy username',self.copy_user),('Copy password (30s)',self.copy_password)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        target=window.tabs.currentWidget();self.target=target if hasattr(target,'credentials') and target.credentials.protocol.currentText() in ('SSH','SFTP','SCP','FTP','FTPS') else None
        label='Fill current '+self.target.credentials.protocol.currentText()+' login' if self.target else 'Select an SSH / remote file tab to fill a login'
        if self.target:
            target_label=QLabel('Target: '+(self.target.credentials.host.text() or '(host not entered)'));target_label.setWordWrap(True);layout.addWidget(target_label)
        self.fill_button=QPushButton(label);self.fill_button.setEnabled(bool(self.target and self.target.credentials.isEnabled()));self.fill_button.clicked.connect(self.fill);layout.addWidget(self.fill_button)
        hint=QLabel('Filling links this entry to the connection form. Save that device profile to keep the link. Filling does not connect or type into the terminal. Clipboard history tools may retain copied passwords.');hint.setWordWrap(True);layout.addWidget(hint)
        close=QPushButton('Close');close.clicked.connect(self.accept);layout.addWidget(close)
        self.search.textChanged.connect(self.refresh);self.table.cellDoubleClicked.connect(lambda *_:self.edit(self.selected()));self.refresh()
    def selected(self):
        row=self.table.currentRow();item=self.table.item(row,0) if row>=0 else None
        return item.data(Qt.UserRole) if item else None
    def refresh(self,*args):
        selected=self.selected();self.table.setRowCount(0);query=self.search.text().casefold()
        for entry_id,entry in sorted(self.vault.passwords.items(),key=lambda pair:pair[1].get('name','').casefold()):
            if query and query not in ' '.join(str(entry.get(k,'')) for k in ('name','group','url','user')).casefold():continue
            row=self.table.rowCount();self.table.insertRow(row)
            secret=', '.join(label for k,label in [('password','password'),('key_data','key copy'),('passphrase','passphrase')] if entry.get(k))
            for col,value in enumerate((entry.get('name',''),entry.get('group',''),display_url(entry.get('url','')),entry.get('user',''),secret or 'No secret')):
                item=QTableWidgetItem(value);item.setData(Qt.UserRole,entry_id);self.table.setItem(row,col,item)
            if selected==entry_id:self.table.selectRow(row)
        if self.table.rowCount() and not self.selected():self.table.selectRow(0)
    def edit(self,entry_id=None):
        if entry_id is None:entry={}
        else:
            entry=self.vault.passwords.get(entry_id)
            if entry is None:return
        dialog=PasswordEditor(entry,self)
        if dialog.exec_()==QDialog.Accepted:
            try:self.vault.save_password(dialog.values(),entry_id);self.refresh()
            except (VaultError,OSError) as error:QMessageBox.warning(self,'Vault entry',str(error))
    def delete(self):
        entry_id=self.selected()
        if entry_id and QMessageBox.question(self,'Delete vault entry','Delete this entry? Linked profiles will need another login.')==QMessageBox.Yes:
            try:self.vault.delete_password(entry_id);self.refresh()
            except (VaultError,OSError) as error:QMessageBox.warning(self,'Vault entry',str(error))
    def copy_user(self):
        entry=self.vault.passwords.get(self.selected(),{});QApplication.clipboard().setText(entry.get('user',''))
    def copy_password(self):
        entry=self.vault.passwords.get(self.selected(),{})
        if entry.get('password'):self.window.clipboard_guard.copy(entry['password'])
    def import_entries(self):self.window.import_connections(passwords=True);self.refresh()
    def fill(self):
        entry_id=self.selected();entry=self.vault.passwords.get(entry_id)
        if not entry or not self.target or not self.target.credentials.isEnabled():return
        c=self.target.credentials;host=c.host.text().strip().casefold();url=entry.get('url','')
        try:source=(urlparse(url if '://' in url else '//'+url).hostname or '').casefold()
        except ValueError:source=''
        if source and host and source!=host:
            if QMessageBox.question(self,'Different login target',f'This entry is for {source}. Fill the connection form for {host}?')!=QMessageBox.Yes:return
        try:settings=c.values() if host else dict(protocol=c.protocol.currentText(),port=c.port.text())
        except ValueError as error:QMessageBox.warning(self,'Login target',str(error));return
        settings.update({k:entry.get(k,'') for k in ('user','password','key','key_data','passphrase')});settings['credential_ref']=entry_id
        c.apply(settings);self.accept();self.window.statusBar().showMessage('Vault login linked to this form. Save the device profile to keep the link.',10000)
