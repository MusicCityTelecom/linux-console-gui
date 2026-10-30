"""RFC 1350 octet-mode TFTP client and a filename-based transfer tab."""
import os
import socket
import struct
import tempfile
from pathlib import Path
from PyQt5.QtCore import pyqtSignal,QTimer
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QLabel,QPushButton,QFileDialog,QMessageBox,QSpinBox,QProgressBar,QScrollArea
from network_core import Task
from credential_vault import CredentialVault
from profile_ui import ProfileBar


class TransferCancelled(Exception):pass


def transfer(host,port,remote,local,upload,progress=lambda a,b:None,cancelled=lambda:False,timeout=3,retries=5):
    if not remote or '\0' in remote:raise ValueError('Enter a remote filename without null characters.')
    try:name=remote.encode('ascii')
    except UnicodeEncodeError:raise ValueError('Classic TFTP filenames must use ASCII characters.')
    if len(name)>255:raise ValueError('The remote filename is too long.')
    family,_,_,_,initial=socket.getaddrinfo(host,int(port),type=socket.SOCK_DGRAM)[0]
    local=Path(local);total=local.stat().st_size if upload else 0;temp_path=None
    if upload and total>=65535*512:raise ValueError('Classic TFTP transfers are limited to less than 32 MiB in this client.')
    peer=None;request=struct.pack('!H',2 if upload else 1)+name+b'\0octet\0'
    with socket.socket(family,socket.SOCK_DGRAM) as sock:
        sock.settimeout(min(.2,timeout))
        def exchange(packet,opcode,block):
            nonlocal peer
            import time
            destination=peer or initial
            for attempt in range(retries):
                
                if cancelled():raise TransferCancelled('Transfer cancelled.')
                sock.sendto(packet,destination);deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if cancelled():raise TransferCancelled('Transfer cancelled.')
                    try:data,address=sock.recvfrom(2048)
                    except socket.timeout:continue
                    if address[0]!=initial[0]:continue
                    if peer is not None and address!=peer:
                        sock.sendto(b'\0\5\0\5Unknown transfer ID\0',address);continue
                    if len(data)<4:continue
                    op,num=struct.unpack('!HH',data[:4])
                    if op==5:raise OSError('TFTP server error '+str(num)+': '+data[4:].rstrip(b'\0').decode('ascii',errors='replace'))
                    if op==opcode and num==block:
                        if opcode==3 and len(data)>516:raise OSError('Invalid TFTP data block size.')
                        peer=address;return data
                    # Re-acknowledge retransmitted DATA after an ACK was lost.
                    if opcode==3 and op==3 and num==((block-1)&0xffff):sock.sendto(struct.pack('!HH',4,num),address)
            raise TimeoutError('TFTP timed out after '+str(retries)+' attempts.')
        try:
            if upload:
                exchange(request,4,0);sent=0;block=1
                with local.open('rb') as f:
                    while True:
                        data=f.read(512);exchange(struct.pack('!HH',3,block)+data,4,block);sent+=len(data);progress(sent,total)
                        if len(data)<512:break
                        block+=1
            else:
                fd,name=tempfile.mkstemp(prefix='.serial-desk-',suffix='.part',dir=local.parent);temp_path=Path(name)
                packet=request;done=0;block=1
                with os.fdopen(fd,'wb') as f:
                    while True:
                        data=exchange(packet,3,block);payload=data[4:];f.write(payload);done+=len(payload);progress(done,0)
                        ack=struct.pack('!HH',4,block);sock.sendto(ack,peer)
                        if len(payload)<512:
                            # Dally briefly to answer a retransmitted final block.
                            import time
                            deadline=time.monotonic()+timeout
                            while time.monotonic()<deadline:
                                if cancelled():raise TransferCancelled('Transfer cancelled.')
                                try:again,address=sock.recvfrom(2048)
                                except socket.timeout:continue
                                if address==peer and again==data:sock.sendto(ack,peer)
                            break
                        block+=1
                        if block>65535:raise ValueError('Classic TFTP transfer exceeds the client size limit.')
                        packet=ack
                temp_path.replace(local);temp_path=None
        finally:
            if temp_path:temp_path.unlink(missing_ok=True)
    return 'Upload complete' if upload else 'Download complete'


class TFTPTransfer(QWidget):
    title_changed=pyqtSignal(str)
    def __init__(self,vault=None):
        super().__init__();self.task=None;self.pending_transfers=0;self.vault=vault or CredentialVault()
        from network_ui import Credentials
        outer=QVBoxLayout(self);body=QWidget();scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(body);outer.addWidget(scroll,1)
        layout=QVBoxLayout(body);header=QLabel('TFTP File Transfer');header.setStyleSheet('font-size: 20px; font-weight: bold');layout.addWidget(header)
        note=QLabel('Transfer a file by its exact server filename. TFTP has no directory browser, login, or encryption. The server must already permit the requested read/write.');note.setWordWrap(True);layout.addWidget(note)
        self.credentials=Credentials(['TFTP']);self.profile_bar=ProfileBar(self.credentials,self.vault,lambda:self.status.setText('Device loaded • choose a file and Upload or Download.'))
        layout.addWidget(self.profile_bar);layout.addWidget(self.credentials)
        form=QFormLayout();layout.addLayout(form)
        self.filename=QLineEdit();self.filename.setPlaceholderText('For example: startup-config.cfg');filename_form=QFormLayout();outer.addLayout(filename_form);filename_form.addRow('Remote filename',self.filename)
        self.timeout=QSpinBox();self.timeout.setRange(1,15);self.timeout.setValue(3);form.addRow('Timeout per attempt (seconds)',self.timeout)
        self.retries=QSpinBox();self.retries.setRange(1,10);self.retries.setValue(5);form.addRow('Attempts per block',self.retries)
        row=QHBoxLayout();outer.addLayout(row)
        self.upload=QPushButton('Upload file…');self.download=QPushButton('Download file…');self.cancel=QPushButton('Cancel');self.cancel.setEnabled(False)
        row.addWidget(self.upload);row.addWidget(self.download);row.addWidget(self.cancel)
        self.upload.clicked.connect(lambda:self.start(True));self.download.clicked.connect(lambda:self.start(False));self.cancel.clicked.connect(self.cancel_transfer)
        self.progress=QProgressBar();outer.addWidget(self.progress);self.status=QLabel('Ready • classic 512-byte octet-mode transfers, up to 32 MiB.');outer.addWidget(self.status);layout.addStretch()
    def start(self,upload):
        if self.task and self.task.isRunning():return
        try:s=self.credentials.values()
        except ValueError as e:QMessageBox.warning(self,'TFTP settings',str(e));return
        remote=self.filename.text().strip()
        if not remote:QMessageBox.warning(self,'Remote filename','Enter the exact remote filename.');return
        path,_=QFileDialog.getOpenFileName(self,'Upload file via TFTP') if upload else QFileDialog.getSaveFileName(self,'Download file via TFTP',str(Path.home()/Path(remote).name))
        if not path:return
        if upload and QMessageBox.question(self,'Upload via TFTP',f'Upload to {s["host"]}:{s["port"]} as {remote}? An existing remote file may be replaced.')!=QMessageBox.Yes:return
        timeout=self.timeout.value();retries=self.retries.value()
        self.task=Task(lambda task:transfer(s['host'],s['port'],remote,path,upload,lambda done,total:task.progress.emit(int(done*100/max(1,total))) if total else None,task.isInterruptionRequested,timeout,retries),self)
        self.task.progress.connect(self.progress.setValue);self.task.completed.connect(self.completed)
        self.pending_transfers=1;self.credentials.setEnabled(False);self.profile_bar.setEnabled(False);self.upload.setEnabled(False);self.download.setEnabled(False);self.cancel.setEnabled(True)
        self.progress.setRange(0,100 if upload else 0);self.status.setText('Uploading…' if upload else 'Downloading…');self.title_changed.emit('TFTP Transfer · '+s['host']);self.task.start()
    def completed(self,result,error):
        self.pending_transfers=0;self.credentials.setEnabled(True);self.profile_bar.setEnabled(True);self.upload.setEnabled(True);self.download.setEnabled(True);self.cancel.setEnabled(False)
        self.progress.setRange(0,100);self.progress.setValue(0 if error else 100);self.status.setText(error or result)
        if error and 'cancelled' not in error.lower():QMessageBox.warning(self,'TFTP transfer failed',error)
    def cancel_transfer(self):
        if self.task:self.task.requestInterruption();self.status.setText('Cancelling…');self.cancel.setEnabled(False)
    def shutdown(self):
        if self.task and self.task.isRunning():self.task.requestInterruption();self.task.wait(20000)
