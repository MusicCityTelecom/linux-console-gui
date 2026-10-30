"""Encrypted tab snapshots and opt-in reconnection, always after vault unlock."""
import json
import uuid
from PyQt5.QtCore import QObject,QTimer,QByteArray,Qt
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QComboBox,
    QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox)
from credential_vault import VaultError
from network_ui import NetworkConsole,FileBrowser,Notepad
from tftp_transfer import TFTPTransfer
from device_profiles import for_context

PUBLIC_FIELDS=('protocol','host','port','user','key','ca','unverified','legacy','remote_path',
               'open_files','file_protocol','file_port','credential_ref')
DEFAULT_OPTIONS=dict(save=True,policy='ask',reconnect=False,geometry=True)


class RestoreDialog(QDialog):
    def __init__(self,snapshot,reconnect=False,parent=None,replacing=False):
        super().__init__(parent);self.setWindowTitle('Restore previous workspace?');self.resize(730,440)
        layout=QVBoxLayout(self)
        text='Restore saved tabs? Connections remain disconnected unless you choose to reconnect.'
        if replacing:text+=' This replaces the current tabs and disconnects their sessions.'
        label=QLabel(text);label.setWordWrap(True);layout.addWidget(label)
        table=QTableWidget(0,3);table.setHorizontalHeaderLabels(['Tab','Type','Target']);table.setEditTriggers(table.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);layout.addWidget(table)
        for record in snapshot.get('tabs',[]):
            row=table.rowCount();table.insertRow(row);s=record.get('settings',{})
            target=s.get('host',s.get('port',''))
            if s.get('host') and s.get('port'):target=f'{target}:{s["port"]}'
            kind=record.get('kind','');label={'network':s.get('protocol','SSH / Telnet'),'files':s.get('protocol','Remote')+' files','local':'Local terminal','notes':'Notepad','serial':'Serial','tftp':'TFTP transfer'}.get(kind,kind)
            for col,value in enumerate((record.get('title',''),label,str(target))):table.setItem(row,col,QTableWidgetItem(value))
        self.reconnect=QCheckBox('Reconnect sessions that were connected (including local terminals)');self.reconnect.setChecked(reconnect);layout.addWidget(self.reconnect)
        hint=QLabel('Saved logins are available only after unlock. Commands and file transfers are never replayed.');hint.setWordWrap(True);layout.addWidget(hint)
        self.remember=QCheckBox('Use my restore / start-fresh choice at future startups');layout.addWidget(self.remember)
        row=QHBoxLayout();layout.addLayout(row)
        fresh=QPushButton('Start fresh');fresh.clicked.connect(self.reject);row.addWidget(fresh)
        restore=QPushButton('Restore tabs');restore.setDefault(True);restore.clicked.connect(self.accept);row.addWidget(restore)


class WorkspaceOptions(QDialog):
    def __init__(self,options,parent=None):
        super().__init__(parent);self.setWindowTitle('Workspace startup settings');self.resize(500,290)
        layout=QVBoxLayout(self)
        self.save=QCheckBox('Save open tabs automatically and on exit');self.save.setChecked(options['save']);layout.addWidget(self.save)
        layout.addWidget(QLabel('On opening Serial Desk, after the vault is unlocked:'))
        self.policy=QComboBox()
        for text,value in [('Ask before restoring (default)','ask'),('Always restore tabs','always'),('Never restore automatically','never')]:self.policy.addItem(text,value)
        self.policy.setCurrentIndex(max(0,self.policy.findData(options['policy'])));layout.addWidget(self.policy)
        self.reconnect=QCheckBox('Reconnect previously connected sessions when restoring');self.reconnect.setChecked(options['reconnect']);layout.addWidget(self.reconnect)
        self.geometry=QCheckBox('Restore window size and position');self.geometry.setChecked(options['geometry']);layout.addWidget(self.geometry)
        info=QLabel('Tab details are encrypted in the vault. Unsaved passwords, console contents, command input and transfer queues are excluded.');info.setWordWrap(True);layout.addWidget(info)
        row=QHBoxLayout();layout.addLayout(row)
        for name,callback in [('Cancel',self.reject),('Save settings',self.accept)]:
            button=QPushButton(name);button.clicked.connect(callback);row.addWidget(button)
    def values(self):return dict(save=self.save.isChecked(),policy=self.policy.currentData(),reconnect=self.reconnect.isChecked(),geometry=self.geometry.isChecked())


class WorkspaceController(QObject):
    def __init__(self,window,startup=True):
        super().__init__(window);self.window=window;self.vault=window.vault;self.stopped=False
        self.options=dict(DEFAULT_OPTIONS,**self.vault.section('workspace_options',{}))
        self.last=None;self.baseline=self.fingerprint(self.snapshot());self.preserve=True
        menu=window.menuBar().addMenu('Workspace')
        menu.addAction('Save workspace now',lambda:self.save(force=True),'Ctrl+Shift+S')
        menu.addAction('Restore saved workspace…',self.restore_saved)
        menu.addAction('Startup / restore settings…',self.configure)
        menu.addAction('Forget saved workspace…',self.forget)
        self.timer=QTimer(self);self.timer.setInterval(10000);self.timer.timeout.connect(self.save);self.timer.start()
        self.startup_timer=QTimer(self);self.startup_timer.setSingleShot(True);self.startup_timer.timeout.connect(self.startup)
        if startup:self.startup_timer.start(0)
    @staticmethod
    def fingerprint(snapshot):return json.dumps(snapshot,sort_keys=True)
    def snapshot(self):
        records=[]
        for widget in self.window.widgets():
            if not hasattr(widget,'workspace_id'):widget.workspace_id=uuid.uuid4().hex
            index=self.window.tabs.indexOf(widget)
            record=dict(id=widget.workspace_id,title=self.window.tabs.tabText(index),custom_title=getattr(widget,'custom_tab_title',False))
            if isinstance(widget,Notepad):record.update(kind='notes',settings={})
            elif hasattr(widget,'serial'):
                try:s=widget.settings(require_port=False)
                except ValueError:
                    # Empty port is a valid disconnected tab; retain the remaining controls.
                    s=dict(widget.store.last)
                    s.update(port=widget.port.currentText().strip(),baudrate=int(widget.baud.currentText()) if widget.baud.currentText().isdigit() else 9600)
                record.update(kind='serial',settings=s,connected=widget.serial is not None)
            else:
                c=widget.credentials
                try:s=c.values()
                except ValueError:
                    s={name:getattr(c,name).text() for name in ('host','port','user','key','ca')};s['protocol']=c.protocol.currentText()
                    s['credential_ref']=getattr(c,'credential_ref','')
                s={k:v for k,v in s.items() if k in PUBLIC_FIELDS}
                if isinstance(widget,NetworkConsole):
                    # Secrets are cleared from the form on connect; last_settings has only metadata.
                    if widget.worker and widget.worker.isRunning():s.update({k:v for k,v in (widget.last_settings or {}).items() if k in PUBLIC_FIELDS})
                    record.update(kind='local' if c.protocol.currentText()=='Local terminal' else 'network',connected=widget.state.text()=='Connected')
                elif isinstance(widget,FileBrowser):
                    record.update(kind='files',connected=widget.remote is not None,source=getattr(widget,'ssh_source_id',''))
                    s['remote_path']=widget.path.text() if widget.remote is not None else c.remote_path.text()
                elif isinstance(widget,TFTPTransfer):record.update(kind='tftp',connected=False)
                else:continue
                record.update(settings=s,profile=widget.profile_bar.combo.currentText())
            records.append(record)
        return dict(version=1,tabs=records,selected=self.window.tabs.currentIndex(),geometry=bytes(self.window.saveGeometry().toBase64()).decode('ascii'))
    def save(self,force=False,report=True):
        if self.stopped or self.vault.locked or (not force and not self.options['save']):return True
        snapshot=self.snapshot();fingerprint=self.fingerprint(snapshot)
        if not force and (fingerprint==self.last or (self.preserve and fingerprint==self.baseline)):return True
        try:self.vault.save_section('workspace',snapshot)
        except (VaultError,OSError) as error:
            if report:self.window.statusBar().showMessage('Workspace could not be saved: '+str(error))
            return False
        self.last=fingerprint;self.preserve=False
        if force and report:self.window.statusBar().showMessage('Workspace saved in the encrypted vault.',6000)
        return True
    def startup(self):
        if self.stopped or self.vault.locked:return
        snapshot=self.vault.section('workspace')
        if not snapshot or not snapshot.get('tabs'):return
        if self.options['policy']=='never':return
        try:
            if self.options['policy']=='always':self.restore(snapshot,self.options['reconnect'])
            else:self.ask_restore(snapshot)
        except (VaultError,OSError,ValueError,TypeError) as error:QMessageBox.warning(self.window,'Workspace could not be restored',str(error))
    def ask_restore(self,snapshot,replacing=False):
        dialog=RestoreDialog(snapshot,self.options['reconnect'],self.window,replacing)
        accepted=dialog.exec_()==QDialog.Accepted
        if dialog.remember.isChecked():
            self.options.update(policy='always' if accepted else 'never',reconnect=dialog.reconnect.isChecked())
            try:self.vault.save_section('workspace_options',self.options)
            except (VaultError,OSError) as error:QMessageBox.warning(self.window,'Startup preference was not saved',str(error))
        if accepted:self.restore(snapshot,dialog.reconnect.isChecked())
        else:self.baseline=self.fingerprint(self.snapshot());self.preserve=True
    def restore_saved(self):
        if any(self.window.busy(w) for w in self.window.widgets()):
            QMessageBox.information(self.window,'Workspace','Finish file operations before restoring a workspace.');return
        snapshot=self.vault.section('workspace')
        if snapshot and snapshot.get('tabs'):
            try:self.ask_restore(snapshot,replacing=True)
            except (VaultError,OSError,ValueError,TypeError) as error:QMessageBox.warning(self.window,'Workspace could not be restored',str(error))
        else:QMessageBox.information(self.window,'Workspace','There is no saved workspace yet.')
    def restore(self,snapshot,reconnect=False):
        if self.vault.locked:raise VaultError('Unlock the credential vault before restoring tabs.')
        if snapshot.get('version')!=1:raise VaultError('Unsupported workspace version.')
        if any(self.window.busy(w) for w in self.window.widgets()):raise VaultError('Finish file operations before restoring.')
        records=snapshot.get('tabs',[])
        if not isinstance(records,list) or len(records)>256:raise VaultError('Invalid or excessively large workspace.')
        if any(not isinstance(r,dict) or not isinstance(r.get('settings',{}),dict) for r in records):raise VaultError('Invalid workspace tab data.')
        window=self.window;notes=window.notes
        for widget in window.widgets():
            if widget is not notes:widget.shutdown()
        while window.tabs.count():
            old=window.tabs.widget(0);window.tabs.removeTab(0)
            if old is not notes:old.deleteLater()
        restored={};pending=[];problems=[];have_notes=False
        for record in records:
            kind=record.get('kind');widget=None
            try:
                if kind=='notes':
                    if have_notes:continue
                    widget=window.add_tab(notes,'Notepad');have_notes=True
                elif kind=='serial':widget=window.new_serial();widget.apply(record.get('settings',{}))
                elif kind in ('network','local'):widget=window.new_network(local=kind=='local')
                elif kind=='files':widget=window.new_files()
                elif kind=='tftp':widget=window.new_tftp()
                else:continue
                widget.workspace_id=record.get('id') or uuid.uuid4().hex
                if kind=='files':widget.ssh_source_id=record.get('source','')
                if kind not in ('notes','serial'):
                    fields={k:v for k,v in record.get('settings',{}).items() if k in PUBLIC_FIELDS}
                    profile=self.vault.profiles.get(record.get('profile',''))
                    if profile:
                        protocols=[widget.credentials.protocol.itemText(i) for i in range(widget.credentials.protocol.count())]
                        settings=for_context(profile,protocols);settings.update(fields)
                        widget.profile_bar.combo.setCurrentText(record['profile'])
                    else:
                        settings=fields
                        if record.get('profile'):problems.append('A saved profile is missing; re-enter its login.')
                    widget.credentials.apply(self.vault.resolve_login(settings))
                    widget.quiet_errors=True
                widget.custom_tab_title=record.get('custom_title',False)
                window.tabs.setTabText(window.tabs.indexOf(widget),record.get('title') or kind)
                restored[widget.workspace_id]=widget
                if reconnect and record.get('connected'):pending.append((widget,record))
            except (ValueError,TypeError,KeyError) as error:problems.append('A tab needs updated settings: '+str(error))
        if not have_notes:window.add_tab(notes,'Notepad')
        for attr,kind in [('serial_tab','serial'),('network','network'),('files','files'),('local','local')]:
            setattr(window,attr,next((restored.get(r.get('id')) for r in records if r.get('kind')==kind and restored.get(r.get('id'))),None))
        by_source={}
        for widget,record in pending:
            if record['kind']=='files' and record.get('source'):
                parent=restored.get(record['source'])
                if isinstance(parent,NetworkConsole) and any(w is parent for w,r in pending):by_source.setdefault(parent.workspace_id,[]).append((widget,record))
        for widget,record in pending:
            kind=record['kind']
            if kind=='serial':widget.quiet_errors=True;widget.toggle_connection()
            elif kind in ('network','local'):
                siblings=by_source.get(widget.workspace_id,[])
                for child,entry in siblings:child.status.setText('Waiting for the restored SSH connection…')
                widget.skip_companion_once=bool(siblings)
                widget.toggle()
                if widget.worker and siblings:
                    def ready(state,source=widget,children=siblings):
                        if state!='Connected' or not source.worker.ssh_handle:return
                        for child,entry in children:
                            if window.tabs.indexOf(child)<0 or child.remote is not None:continue
                            child.ssh_source_id=source.workspace_id
                            child.attach_ssh(source.worker.ssh_handle,child.credentials.protocol.currentText(),child.credentials.values())
                    widget.worker.state.connect(ready)
                    widget.worker.failed.connect(lambda message,children=siblings:[child.status.setText('SSH parent failed: '+message) for child,entry in children if window.tabs.indexOf(child)>=0])
            elif kind=='files' and not any(widget is child for children in by_source.values() for child,r in children):widget.connect_remote()
        window.tabs.setCurrentIndex(min(max(0,int(snapshot.get('selected',0))),window.tabs.count()-1))
        if self.options['geometry'] and snapshot.get('geometry'):window.restoreGeometry(QByteArray.fromBase64(snapshot['geometry'].encode('ascii')))
        self.preserve=False;self.last=None
        window.statusBar().showMessage('Workspace restored.'+(' '+problems[0] if problems else ''),12000)
    def configure(self):
        dialog=WorkspaceOptions(self.options,self.window)
        if dialog.exec_()==QDialog.Accepted:
            try:self.vault.save_section('workspace_options',dialog.values());self.options=dialog.values()
            except (VaultError,OSError) as error:QMessageBox.warning(self.window,'Workspace settings',str(error))
    def forget(self):
        if QMessageBox.question(self.window,'Forget workspace','Forget the saved tab snapshot? Current tabs stay open. Future changes are still saved if automatic saving is enabled.')==QMessageBox.Yes:
            try:self.vault.save_section('workspace',None);self.baseline=self.fingerprint(self.snapshot());self.last=None;self.preserve=True
            except (VaultError,OSError) as error:QMessageBox.warning(self.window,'Workspace was not forgotten',str(error))
    def shutdown(self):
        self.timer.stop();self.startup_timer.stop();self.stopped=True
