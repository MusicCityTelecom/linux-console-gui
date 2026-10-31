"""Bounded plain-text editing with conflict detection and staged saves."""
import errno
import hashlib
import io
import os
import posixpath
import stat
import tempfile
import uuid
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont,QTextCursor,QTextOption
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QLineEdit,
    QPushButton,QLabel,QComboBox,QCheckBox,QMessageBox,QFileDialog,QShortcut)
from PyQt5.QtGui import QKeySequence

MAX_TEXT=2*1024*1024
ENCODINGS={'UTF-8':('utf-8',b''),'UTF-8 BOM':('utf-8',b'\xef\xbb\xbf'),
           'UTF-16 LE':('utf-16-le',b'\xff\xfe'),'UTF-16 BE':('utf-16-be',b'\xfe\xff'),'Windows-1252':('cp1252',b'')}


class FileConflict(OSError):pass


def digest(data):return hashlib.sha256(data).hexdigest()


def decode_text(data,encoding=None):
    if len(data)>MAX_TEXT:raise ValueError('The built-in editor supports text files up to 2 MiB. Download/open larger files in a dedicated editor.')
    if encoding is None:
        encoding=next((name for name,(codec,bom) in ENCODINGS.items() if bom and data.startswith(bom)),'UTF-8')
    codec,bom=ENCODINGS[encoding];body=data[len(bom):] if bom and data.startswith(bom) else data
    try:text=body.decode(codec)
    except UnicodeError:raise ValueError('This is not valid '+encoding+' text. Try Open text with encoding, or use a dedicated editor for binary files.')
    if any(ord(char)<32 and char not in '\r\n\t\f' for char in text) or '\u2028' in text or '\u2029' in text:
        raise ValueError('This file contains binary/control characters or special line separators; use a dedicated editor.')
    endings=set()
    if '\r\n' in text:endings.add('CRLF')
    remainder=text.replace('\r\n','')
    if '\n' in remainder:endings.add('LF')
    if '\r' in remainder:endings.add('CR')
    newline='Mixed' if len(endings)>1 else next(iter(endings),'LF')
    return text.replace('\r\n','\n').replace('\r','\n'),encoding,newline


def encode_text(text,encoding,newline):
    if newline=='Mixed':raise ValueError('Choose LF, CRLF or CR before saving a file with mixed line endings.')
    codec,bom=ENCODINGS[encoding];data=bom+text.replace('\n',{'LF':'\n','CRLF':'\r\n','CR':'\r'}[newline]).encode(codec)
    if len(data)>MAX_TEXT:raise ValueError('The edited file exceeds the 2 MiB editor limit.')
    return data


def read_local(path):
    original=Path(path).absolute();target=original.resolve(strict=True)
    fd=os.open(target,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as source:
        attributes=os.fstat(source.fileno())
        if not stat.S_ISREG(attributes.st_mode):raise ValueError('Only regular text files can be edited.')
        if attributes.st_size>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
        data=source.read(MAX_TEXT+1)
    if len(data)>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
    return dict(path=str(original),target=str(target),data=data,hash=digest(data),identity=(attributes.st_dev,attributes.st_ino),attributes=attributes)


def save_local(snapshot,data):
    if len(data)>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
    current=read_local(snapshot['path'])
    if any(current[key]!=snapshot[key] for key in ('target','hash','identity')):raise FileConflict('The file changed outside this editor. Reload it, or save a separate copy; your edits are still here.')
    if current['attributes'].st_nlink>1:raise OSError('This file has multiple hard links. Save a copy to avoid breaking its links.')
    target=Path(current['target']);fd,name=tempfile.mkstemp(prefix='.serial-desk-edit-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            attrs=current['attributes'];created=os.fstat(output.fileno())
            if hasattr(os,'fchown') and (created.st_uid,created.st_gid)!=(attrs.st_uid,attrs.st_gid):os.fchown(output.fileno(),attrs.st_uid,attrs.st_gid)
            output.write(data);output.flush();os.fsync(output.fileno())
            os.chmod(name,stat.S_IMODE(attrs.st_mode))
            if hasattr(os,'listxattr'):
                for key in os.listxattr(target):os.setxattr(name,key,os.getxattr(target,key))
        latest=read_local(snapshot['path'])
        if any(latest[key]!=current[key] for key in ('target','hash','identity')) or (latest['attributes'].st_mode,latest['attributes'].st_uid,latest['attributes'].st_gid)!=(attrs.st_mode,attrs.st_uid,attrs.st_gid):raise FileConflict('The file or its permissions changed while saving. Your edits have not replaced it.')
        os.replace(name,target)
    finally:Path(name).unlink(missing_ok=True)
    return read_local(snapshot['path'])


class RemoteTextStore:
    """Uses the browser's serialized SFTP/FTP connection, never shell commands."""
    def __init__(self,remote,sftp=True):self.remote=remote;self.sftp=sftp;self.disconnected=False
    def metadata(self,path):
        if self.sftp:
            info=self.remote.stat(path)
            if not stat.S_ISREG(info.st_mode):raise ValueError('Only regular text files can be edited.')
            return dict(mode=stat.S_IMODE(info.st_mode),uid=info.st_uid,gid=info.st_gid,size=info.st_size)
        try:
            reply=self.remote.sendcmd('MLST '+path)
            facts=next(line.strip().split(' ',1)[0] for line in reply.splitlines() if 'type=' in line.lower())
            values=dict(part.split('=',1) for part in facts.split(';') if '=' in part)
            return dict(mode=int(values['unix.mode'],8) if 'unix.mode' in values else None,uid=values.get('unix.uid'),gid=values.get('unix.gid'),size=int(values.get('size',0)),type=values.get('type','').lower())
        except Exception:return dict(mode=None,uid=None,gid=None,size=0,type='unknown')
    def read(self,path):
        if any(char in path for char in '\r\n\0'):raise ValueError('Unsupported control characters in the filename.')
        target=self.remote.normalize(path) if self.sftp else path;attributes=self.metadata(target)
        if attributes['size']>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
        if self.sftp:
            with self.remote.open(target,'rb') as source:data=source.read(MAX_TEXT+1)
        else:
            output=io.BytesIO()
            def receive(chunk):
                if output.tell()+len(chunk)>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
                output.write(chunk)
            try:self.remote.retrbinary('RETR '+target,receive)
            except Exception:
                self.remote.close();self.disconnected=True
                raise OSError('Remote text read failed. Reconnect before using this FTP session again.')
            data=output.getvalue()
        if len(data)>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
        reason='' if self.sftp or (attributes['mode'] is not None and attributes.get('type')=='file') else 'This FTP server does not expose regular-file permission metadata. Save a local copy and upload it, or use SFTP for direct saving.'
        return dict(path=path,target=target,data=data,hash=digest(data),attributes=attributes,readonly_reason=reason)
    def save(self,snapshot,data):
        if len(data)>MAX_TEXT:raise ValueError('File exceeds the 2 MiB editor limit.')
        if snapshot.get('readonly_reason'):raise OSError(snapshot['readonly_reason'])
        current=self.read(snapshot['path'])
        if current['hash']!=snapshot['hash'] or current['target']!=snapshot['target']:raise FileConflict('The remote file changed outside this editor. Reload or save a local copy; your edits are still here.')
        target=current['target'];folder=posixpath.dirname(target);tag=uuid.uuid4().hex
        stage=posixpath.join(folder,'.serial-desk-edit-'+tag+'.tmp');backup=posixpath.join(folder,'.serial-desk-backup-'+tag)
        staged=False;backed_up=False;promoted=False;warning=''
        try:
            if self.sftp:
                with self.remote.open(stage,'wx') as output:
                    staged=True;self.remote.chmod(stage,0o600);output.write(data);output.flush()
                attrs=current['attributes'];new=self.remote.stat(stage)
                if (attrs['uid'],attrs['gid'])!=(new.st_uid,new.st_gid):self.remote.chown(stage,attrs['uid'],attrs['gid'])
                self.remote.chmod(stage,attrs['mode'])
            else:
                # Create an empty private staging file before sending any edited contents.
                self.remote.storbinary('STOR '+stage,io.BytesIO());staged=True
                self.remote.sendcmd(f'SITE CHMOD 600 {stage}')
                self.remote.storbinary('STOR '+stage,io.BytesIO(data))
                self.remote.sendcmd(f'SITE CHMOD {current["attributes"]["mode"]:o} {stage}')
                attributes=self.metadata(stage)
                for key in ('uid','gid'):
                    if current['attributes'][key] is not None and current['attributes'][key]!=attributes[key]:raise OSError('The FTP server cannot preserve this file owner/group. Save a local copy instead.')
            latest=self.read(snapshot['path'])
            if latest['hash']!=current['hash'] or latest['target']!=current['target'] or latest['attributes']!=current['attributes']:raise FileConflict('The remote file or its permissions changed during upload. It has not been replaced.')
            if self.sftp:
                try:self.remote.posix_rename(stage,target);promoted=True;staged=False
                except OSError as error:
                    if error.errno not in (errno.ENOSYS,errno.EOPNOTSUPP) and not any(s in str(error).lower() for s in ('unsupported','not supported')):raise
            if not promoted:
                self.remote.rename(target,backup);backed_up=True
                self.remote.rename(stage,target);staged=False;promoted=True
                try:self.remote.remove(backup) if self.sftp else self.remote.delete(backup);backed_up=False
                except Exception:warning='Saved. Previous version remains at '+backup
            result=self.read(snapshot['path']);result['warning']=warning;return result
        except Exception as error:
            if backed_up and not promoted:
                try:
                    if self.sftp:
                        try:self.remote.stat(target)
                        except OSError as missing:
                            if missing.errno==errno.ENOENT:self.remote.rename(backup,target);backed_up=False
                    else:
                        names=self.remote.nlst(folder or '.')
                        if posixpath.basename(target) not in [posixpath.basename(n) for n in names]:self.remote.rename(backup,target);backed_up=False
                except Exception:pass
            if backed_up and not promoted:raise OSError(str(error)+' Original file retained at '+backup) from error
            raise
        finally:
            if staged:
                try:self.remote.remove(stage) if self.sftp else self.remote.delete(stage)
                except Exception:pass


class TextEditor(QDialog):
    def __init__(self,snapshot,parent=None,remote=False,save_handler=None,reload_handler=None,encoding=None):
        super().__init__(parent);self.snapshot=snapshot;self.remote=remote;self.save_handler=save_handler;self.reload_handler=reload_handler;self.busy=False;self.closed=False;self.close_after_save=False
        self.setAttribute(Qt.WA_DeleteOnClose);self.resize(min(940,self.screen().availableGeometry().width()-40),min(700,self.screen().availableGeometry().height()-70))
        layout=QVBoxLayout(self);self.location=QLabel(snapshot['path']);self.location.setTextFormat(Qt.PlainText);self.location.setWordWrap(True);layout.addWidget(self.location)
        row=QHBoxLayout();layout.addLayout(row)
        self.save_button=QPushButton('Save remote' if remote else 'Save');self.save_button.clicked.connect(self.save);row.addWidget(self.save_button)
        self.copy_button=QPushButton('Save local copy…');self.copy_button.clicked.connect(self.save_copy);row.addWidget(self.copy_button)
        self.reload_button=QPushButton('Reload');self.reload_button.clicked.connect(self.reload);row.addWidget(self.reload_button)
        row=QHBoxLayout();layout.addLayout(row)
        self.encoding=QComboBox();self.encoding.addItems(ENCODINGS);row.addWidget(self.encoding)
        self.newline=QComboBox();self.newline.addItems(['LF','CRLF','CR','Mixed']);row.addWidget(self.newline)
        wrap=QCheckBox('Wrap');wrap.setChecked(True);row.addWidget(wrap)
        self.text=QPlainTextEdit();self.text.setFont(QFont('Consolas' if os.name=='nt' else 'DejaVu Sans Mono',11));self.text.setTabStopDistance(self.text.fontMetrics().horizontalAdvance(' ')*4);layout.addWidget(self.text,1)
        wrap.toggled.connect(lambda value:self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth if value else QPlainTextEdit.NoWrap))
        row=QHBoxLayout();layout.addLayout(row);self.find=QLineEdit();self.find.setPlaceholderText('Find text…');row.addWidget(self.find,1)
        self.find.returnPressed.connect(self.find_next)
        for title,callback in [('Find next',self.find_next),('Replace…',self.replace_text)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);layout.addWidget(self.status)
        self.position=QLabel();layout.addWidget(self.position);self.text.cursorPositionChanged.connect(self.update_position)
        self.text.document().modificationChanged.connect(self.update_title)
        for key,callback in [('Ctrl+S',self.save),('Ctrl+Shift+S',self.save_copy),('Ctrl+F',lambda:self.find.setFocus()),('Ctrl+W',self.close)]:
            shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.WidgetWithChildrenShortcut);shortcut.activated.connect(callback)
        self.load_snapshot(snapshot,encoding)
        self.encoding.currentIndexChanged.connect(self.option_changed);self.newline.currentIndexChanged.connect(self.option_changed)
    def load_snapshot(self,snapshot,encoding=None):
        text,encoding,newline=decode_text(snapshot['data'],encoding);self.snapshot=snapshot;self.encoding.blockSignals(True);self.newline.blockSignals(True);self.text.setPlainText(text);self.encoding.setCurrentText(encoding);self.newline.setCurrentText(newline);self.text.document().setModified(False);self.encoding.blockSignals(False);self.newline.blockSignals(False)
        self.status.setText(snapshot.get('readonly_reason') or ('Mixed line endings: choose a format before saving.' if newline=='Mixed' else 'Changes are saved only when you choose Save.'))
        self.save_button.setEnabled(not snapshot.get('readonly_reason'));self.update_title();self.update_position()
    def option_changed(self,*args):self.text.document().setModified(True)
    def update_title(self,*args):self.setWindowTitle(('* ' if self.text.document().isModified() else '')+Path(self.snapshot['path']).name+' — '+('Remote' if self.remote else 'Local')+' text editor')
    def update_position(self):
        cursor=self.text.textCursor();self.position.setText(f'Line {cursor.blockNumber()+1}, column {cursor.positionInBlock()+1}')
    def payload(self):return encode_text(self.text.toPlainText(),self.encoding.currentText(),self.newline.currentText())
    def set_busy(self,value):
        self.busy=value;self.text.setReadOnly(value)
        for widget in (self.save_button,self.copy_button,self.reload_button,self.encoding,self.newline):widget.setEnabled(not value)
        if not value and self.snapshot.get('readonly_reason'):self.save_button.setEnabled(False)
        if value:self.status.setText('Working… Keep this window open until the operation finishes.')
    def save(self):
        if self.busy:return
        try:
            if not self.text.document().isModified():self.status.setText('No unsaved changes.');return
            if self.snapshot.get('readonly_reason'):raise OSError(self.snapshot['readonly_reason'])
            data=self.payload()
            if self.remote:self.save_handler(self,data)
            else:self.saved(save_local(self.snapshot,data))
        except (OSError,ValueError,UnicodeError) as error:self.failed(str(error))
    def saved(self,snapshot):
        self.snapshot=snapshot;self.set_busy(False);self.text.document().setModified(False);self.status.setText(snapshot.get('warning') or 'Saved.');self.update_title()
        if self.close_after_save:self.close()
    def failed(self,message):self.set_busy(False);self.close_after_save=False;self.status.setText(message);QMessageBox.warning(self,'File was not saved',message)
    def reload(self):
        if self.busy:return
        if self.text.document().isModified() and QMessageBox.question(self,'Reload file','Discard unsaved edits and reload the current file?')!=QMessageBox.Yes:return
        try:
            if self.remote:self.reload_handler(self)
            else:self.load_snapshot(read_local(self.snapshot['path']),self.encoding.currentText())
        except (OSError,ValueError,UnicodeError) as error:self.failed(str(error))
    def save_copy(self):
        if self.busy:return
        path,_=QFileDialog.getSaveFileName(self,'Save local text copy',str(Path.home()/posixpath.basename(self.snapshot['path'])))
        if not path:return
        try:
            data=self.payload();target=Path(path)
            if target.exists():save_local(read_local(target),data)
            else:
                fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
                except Exception:target.unlink(missing_ok=True);raise
            self.status.setText('Local copy saved: '+path+' • original edits remain unsaved.')
        except (OSError,ValueError,UnicodeError) as error:self.failed(str(error))
    def find_next(self):
        if not self.find.text():return
        if not self.text.find(self.find.text()):self.text.moveCursor(QTextCursor.Start);self.text.find(self.find.text())
    def replace_text(self):
        if self.busy:return
        from PyQt5.QtWidgets import QInputDialog
        term=self.find.text()
        if not term:self.find.setFocus();return
        replacement,ok=QInputDialog.getText(self,'Replace all','Replace all exact occurrences of '+term+' with:')
        if not ok:return
        content=self.text.toPlainText();count=content.count(term)
        if count and QMessageBox.question(self,'Replace all',f'Replace {count} occurrence(s)?')==QMessageBox.Yes:
            cursor=self.text.textCursor();cursor.beginEditBlock();cursor.select(QTextCursor.Document);cursor.insertText(content.replace(term,replacement));cursor.endEditBlock()
    def closeEvent(self,event):
        if self.busy:event.ignore();return
        if self.text.document().isModified():
            choice=QMessageBox.warning(self,'Unsaved text edits','Save changes to '+self.snapshot['path']+'?',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
            if choice==QMessageBox.Cancel:event.ignore();return
            if choice==QMessageBox.Save:
                self.close_after_save=True;self.save()
                if self.busy or self.text.document().isModified():event.ignore();return
        self.closed=True;super().closeEvent(event)
