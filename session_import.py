"""Read user-selected exports as data, preview them, then commit one vault batch.

Never execute registry files, SSH ProxyCommand/LocalCommand, terminal macros,
or exported startup commands. No browser databases or client keychains are read.
"""
import configparser
import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit,unquote
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox,QPlainTextEdit,QCheckBox)
from credential_vault import VaultError,display_url
from device_profiles import DEFAULT_PORTS

FORMATS=[('PuTTY registry export (.reg)','putty'),('Tera Term settings (.ini)','teraterm'),
         ('MobaXterm sessions (.mxtsessions / .ini)','moba'),('Termius / OpenSSH config','sshconfig'),
         ('Session CSV / Termius CSV','sessions'),('Browser / password CSV','passwords'),('Session JSON','json')]
LIMIT=10*1024*1024
PROTOCOLS={p.casefold():p for p in DEFAULT_PORTS}
PROTOCOLS.update(ssh2='SSH',ssl='Mitel TLS Telnet',mitel='Mitel TLS Telnet',serial='Serial')


def normalized(row):return {re.sub(r'[^a-z0-9]','',str(k).lower()):v for k,v in row.items() if k is not None}
def first(row,*names,default=''):
    for name in names:
        value=row.get(name)
        if value is not None and value!='':return str(value)
    return default


def session_item(raw,name='',group=''):
    row=normalized(raw);notes=[]
    protocol=PROTOCOLS.get(first(row,'protocol','connectiontype','servicetype','type',default='telnet' if row.get('telnetport') and not row.get('sshport') else 'ssh').casefold())
    if not protocol:raise ValueError('Unsupported connection type.')
    name=first(row,'name','label','title','alias',default=name)
    if protocol=='Serial':
        from serial_core import validate_settings
        port=first(row,'serialline','device','host','port')
        if not port:raise ValueError('Serial device is missing.')
        settings=validate_settings(dict(port=port,baudrate=int(first(row,'baudrate','serialspeed','baud',default='9600')),
            bytesize=int(first(row,'bytesize','serialdatabits',default='8')),parity=first(row,'parity',default='N').upper(),
            stopbits=float(first(row,'stopbits',default='1')),flow=first(row,'flow',default='None')))
        return dict(kind='profile',name=name or port,data=dict(protocol='Serial',host=port,serial_settings=settings,group=first(row,'group','grouppath','folder',default=group)),notes='Serial device; open from Devices → Saved devices and groups.')
    host=first(row,'host','hostname','address','ip','ipaddress')
    if not host:raise ValueError('Host is missing.')
    port=first(row,'port','portnumber','tcpport','sshport' if protocol in ('SSH','SFTP','SCP') else 'telnetport' if protocol=='Telnet' else 'ftpport',default=str(DEFAULT_PORTS[protocol]))
    user=first(row,'user','username','login');password=first(row,'password','passwd');key=first(row,'key','keyfile','identityfile','publickeyfile','privatekeypath')
    if '://' in host:
        parsed=urlsplit(host)
        if parsed.scheme.casefold() not in PROTOCOLS:raise ValueError('Unsupported host URL scheme.')
        url_protocol=PROTOCOLS[parsed.scheme.casefold()]
        if first(row,'protocol','connectiontype','servicetype','type') and protocol!=url_protocol:raise ValueError('Protocol does not match the host URL scheme.')
        protocol=url_protocol
        if not first(row,'port','portnumber','tcpport','sshport','telnetport','ftpport'):port=str(DEFAULT_PORTS[protocol])
        host=parsed.hostname or '';port=str(parsed.port or int(port));user=user or unquote(parsed.username or '');password=password or unquote(parsed.password or '')
    elif '@' in host and not user:user,host=host.rsplit('@',1)
    if host.startswith('['):
        match=re.fullmatch(r'\[([^\]]+)\](?::(\d+))?',host)
        if not match:raise ValueError('Invalid bracketed IPv6 address.')
        host=match.group(1);port=match.group(2) or port
    elif host.count(':')==1 and host.rsplit(':',1)[1].isdigit():host,port=host.rsplit(':',1)
    if not host or any(c.isspace() or ord(c)<32 for c in host) or any(c in host for c in '/\\?#'):raise ValueError('Invalid host name or address.')
    port=int(port)
    if not 1<=port<=65535:raise ValueError('Port must be between 1 and 65535.')
    key_data=first(row,'keydata','privatekey')
    if key_data and not key_data.startswith('-----BEGIN '):key=key or key_data;key_data=''
    if key_data and ('PRIVATE KEY-----' not in key_data.splitlines()[0] or len(key_data)>131072):raise ValueError('Unsupported private-key material.')
    if key:
        notes.append('Key path retained; check its location on this computer.')
        if key.lower().endswith('.ppk'):notes.append('Convert PuTTY PPK to OpenSSH with PuTTYgen before connecting.')
    if any(row.get(k) for k in ('proxycommand','proxyjump','remotecommand','localcommand','startupcommand','macro','gateway')):notes.append('Proxy, gateway, macro or startup command omitted.')
    if first(row,'encryptedpassword','passwordencrypted','passwordciphertext'):notes.append('Encrypted source password was not imported; re-enter it or use a plaintext export.')
    settings=dict(protocol=protocol,host=host,port=port,user=user,password=password,key=key,key_data=key_data,
                  passphrase=first(row,'passphrase','keypassphrase'),group=first(row,'group','grouppath','folder',default=group),
                  remote_path=first(row,'remotepath','directory',default='.'),auto_connect=False,remember=bool(password or row.get('passphrase')))
    if protocol not in ('SSH','SFTP','SCP','FTP','FTPS'):
        for field in ('user','password','key','key_data','passphrase'):settings[field]=''
        notes.append('Interactive Telnet logins must be entered in the terminal.')
    if protocol in ('FTP','FTPS'):
        for field in ('key','key_data','passphrase'):settings[field]=''
    return dict(kind='profile',name=name or host,data=settings,notes=' '.join(notes))


def decode_export(raw):
    if len(raw)>LIMIT:raise ValueError('Export is larger than 10 MiB. Split it into smaller files.')
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return raw.decode('utf-16')
    try:return raw.decode('utf-8-sig')
    except UnicodeDecodeError:return raw.decode('cp1252')


def ini(text):
    parser=configparser.RawConfigParser(strict=False,delimiters=('=',),comment_prefixes=(';','#'),inline_comment_prefixes=None)
    parser.optionxform=str;parser.read_string(text);return parser


def parse_export(raw,format):
    text=decode_export(raw);items=[];warnings=[]
    def add(row,name='',group='',note=''):
        try:
            item=session_item(row,name,group)
            if note:item['notes']=(item['notes']+' '+note).strip()
            items.append(item)
        except (ValueError,TypeError) as error:warnings.append(f'{name or "Entry"}: {error}')
    if format in ('sessions','passwords'):
        csv.field_size_limit(1024*1024)
        try:dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
        except csv.Error:dialect=csv.excel
        reader=csv.DictReader(io.StringIO(text),dialect=dialect)
        if not reader.fieldnames:raise ValueError('CSV column headers are missing.')
        fields=normalized({k:'' for k in reader.fieldnames})
        if format=='passwords' and not ({'password','passwd','loginpassword'}&set(fields)):raise ValueError('Choose a password CSV with a password column.')
        for index,raw_row in enumerate(reader,2):
            if index>10001:raise ValueError('Export contains more than 10,000 entries. Split it before importing.')
            row=normalized(raw_row)
            if format=='sessions':add(row,f'Row {index}')
            else:
                url=first(row,'url','website','origin','hostname','host','loginuri');user=first(row,'username','user','loginusername');password=first(row,'password','passwd','loginpassword')
                if not (url or user or password):continue
                name=first(row,'name','title',default=display_url(url) or user or f'Login {index-1}')
                items.append(dict(kind='password',name=name,data=dict(name=name,url=url,user=user,password=password,notes=first(row,'notes','note','comment'),group=first(row,'group','folder')),notes='Browser/password export; stored only in the password manager.'))
    elif format=='putty':
        section=None;rows=[]
        for line in text.splitlines():
            line=line.strip()
            if line.startswith('['):
                match=re.fullmatch(r'\[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\([^\]]+)\]',line,re.I)
                section={} if match else None
                if match:rows.append((unquote(match.group(1)),section))
            elif section is not None:
                match=re.fullmatch(r'"((?:[^"\\]|\\.)*)"=(.*)',line)
                if not match:continue
                key,value=match.groups()
                if value.startswith('"') and value.endswith('"'):
                    section[key]=re.sub(r'\\([\\"])',r'\1',value[1:-1])
                elif re.fullmatch(r'dword:[0-9a-fA-F]{8}',value):section[key]=str(int(value[6:],16))
        for name,row in rows:
            if not row.get('HostName') and row.get('Protocol')!='serial':continue
            if row.get('Protocol')=='serial':
                row['parity']={'0':'N','1':'O','2':'E','3':'M','4':'S'}.get(row.get('SerialParity','0'),'N')
                row['stopbits']={'2':'1','3':'1.5','4':'2'}.get(row.get('SerialStopHalfbits','2'),'1')
                row['flow']={'0':'None','1':'XON/XOFF','2':'RTS/CTS','3':'DSR/DTR'}.get(row.get('SerialFlowControl','0'),'None')
            add(row,name,note='PuTTY does not save account passwords. Startup commands and proxy settings are not imported.')
        warnings.append('Only PuTTY session keys are read. Registry files are never executed. PuTTY PPK files need OpenSSH conversion; key paths are retained.')
    elif format=='moba':
        parser=ini(text)
        for section in parser.sections():
            if not section.casefold().startswith('bookmarks'):continue
            group=parser.get(section,'SubRep',fallback='').replace('\\','/')
            for name,value in parser.items(section):
                if name.casefold() in ('subrep','imgnum'):continue
                parts=value.split('#')
                if len(parts)<3:warnings.append(f'{name}: Unrecognized bookmark format.');continue
                fields=parts[2].split('%')
                types={'0':'SSH','6':'FTP','7':'SFTP'}
                if len(fields)<4 or fields[0] not in types:warnings.append(f'{name}: This native bookmark type is unsupported; use session CSV for this service.');continue
                row=dict(protocol=types[fields[0]],host=fields[1],port=fields[2],user=fields[3])
                if fields[0]=='0':
                    if len(fields)>14:row['key']=fields[14]
                    if len(fields)>8 and (fields[7] or fields[8]):row['gateway']='omitted'
                note='Encrypted MobaXterm credentials are not included. Check key paths; drive placeholders must be replaced.'
                add(row,name,group,note)
        warnings.append('Native MobaXterm import covers SSH/FTP/SFTP bookmark fields. Verify service settings in the preview. Passwords/Credentials sections, macros and startup commands are not imported. Other services can use session CSV.')
    elif format=='teraterm':
        parser=ini(text);base=dict(parser.items('Tera Term')) if parser.has_section('Tera Term') else dict(parser.items('TeraTerm')) if parser.has_section('TeraTerm') else {}
        ssh=dict(parser.items('TTSSH')) if parser.has_section('TTSSH') else {}
        default_user=ssh.get('DefaultUserName','');default_key=ssh.get('DefaultRSAPrivateKeyFile','')
        if base.get('HostName'):
            protocol='SSH' if ssh.get('Enabled')=='1' else 'Telnet'
            add(dict(protocol=protocol,host=base['HostName'],port=base.get('TCPPort','22' if protocol=='SSH' else '23'),user=default_user,key=default_key),base['HostName'])
        if parser.has_section('Hosts'):
            for name,value in parser.items('Hosts'):
                if not re.fullmatch(r'Host\d+',name,re.I) or not value.strip():continue
                # Parse only documented connection arguments. Do not execute macros or shell commands.
                host=re.match(r'\s*("[^"]+"|\S+)',value).group(1).strip('"')
                if re.search(r'/(?:ssh1|1)(?:\s|$)',value,re.I):warnings.append(f'{name}: SSH1 is unsupported.');continue
                protocol='SSH' if ssh.get('Enabled')=='1' or re.search(r'/(?:ssh2?|2|t=2)(?:\s|$)',value,re.I) or host.lower().startswith(('ssh://','ssh2://')) else 'Telnet'
                if re.search(r'/(?:telnet|nossh|t=1)(?:\s|$)',value,re.I):protocol='Telnet'
                row=dict(protocol=protocol,host=host,user=default_user,key=default_key)
                for arg,key in [('user','user'),('passwd','password'),('keyfile','key'),('p','port')]:
                    match=re.search(r'/'+arg+r'=(?:"([^"]*)"|(\S+))',value,re.I)
                    if match:row[key]=match.group(1) if match.group(1) is not None else match.group(2)
                if '://' not in host and host.count(':')==1:
                    h,p=host.rsplit(':',1)
                    if p.isdigit():row.update(host=h,port=p)
                if not any(item['data'].get('host')==row['host'] and item['data'].get('protocol')==protocol for item in items):add(row,row['host'])
        warnings.append('Tera Term imports INI hosts/default SSH login/key paths and explicit /user, /passwd, /keyfile, /p arguments only. Macros and protected password files are not executed or decrypted.')
    elif format=='sshconfig':
        import shlex
        import fnmatch
        blocks=[];patterns=['*'];row={};blocks.append((patterns,row));aliases=[];conditional=False
        for line in text.splitlines():
            try:
                lexer=shlex.shlex(line,posix=True);lexer.whitespace_split=True;lexer.escape='';lexer.commenters='#';parts=list(lexer)
            except ValueError:warnings.append('An SSH configuration line has unsupported quoting.');continue
            if not parts:continue
            if '=' in parts[0]:key,value=parts[0].split('=',1);parts=[key,value]+parts[1:]
            elif len(parts)>1 and parts[1]=='=':parts.pop(1)
            key=parts[0].lower();value=' '.join(parts[1:]).lstrip('= ')
            if key=='host':
                patterns=parts[1:];row={};blocks.append((patterns,row));conditional=False
                aliases.extend(p for p in patterns if not any(char in p for char in '*?!'));continue
            if key=='match':conditional=True;warnings.append('Conditional Match blocks are omitted.');continue
            if conditional:continue
            if key in ('include','proxycommand','proxyjump','localcommand','remotecommand'):
                warnings.append('Include, proxy and startup commands are omitted; imported hosts may need network settings.');continue
            mapping={'hostname':'host','port':'port','user':'user','identityfile':'key'}
            if key in mapping:row.setdefault(mapping[key],value)
        for alias in dict.fromkeys(aliases):
            merged={}
            for patterns,settings in blocks:
                matches=lambda p:fnmatch.fnmatchcase(alias.casefold(),p.casefold())
                if any(matches(p) for p in patterns if not p.startswith('!')) and not any(matches(p[1:]) for p in patterns if p.startswith('!')):
                    for key,value in settings.items():merged.setdefault(key,value)
            merged.setdefault('host',alias);add(merged,alias)
        warnings.append('Termius support uses exported OpenSSH configuration or session CSV; current encrypted Termius vaults are not decrypted. Passwords are not part of OpenSSH config. Wildcard defaults apply only to explicit host aliases; includes are not opened.')
    elif format=='json':
        data=json.loads(text);rows=data if isinstance(data,list) else data.get('hosts',data.get('sessions',[])) if isinstance(data,dict) else None
        if not isinstance(rows,list):raise ValueError('Use an array of hosts or an object with a hosts/sessions array.')
        if len(rows)>10000:raise ValueError('Export contains more than 10,000 entries.')
        for index,row in enumerate(rows,1):
            if isinstance(row,dict):add(row,f'Host {index}')
            else:warnings.append(f'Host {index}: Invalid entry.')
    else:raise ValueError('Select an import format.')
    if not items and not warnings:warnings.append('No supported entries were found. Check the selected format.')
    return items,list(dict.fromkeys(warnings))


class ImportDialog(QDialog):
    def __init__(self,vault,parent=None,passwords=False):
        super().__init__(parent);self.vault=vault;self.items=[];self.setWindowTitle('Import sessions / passwords')
        screen=self.screen().availableGeometry();self.resize(min(940,screen.width()-40),min(650,screen.height()-60))
        layout=QVBoxLayout(self)
        label=QLabel('Choose an export you created. Review the selected entries before importing. Credentials are masked; nothing connects automatically.');label.setWordWrap(True);layout.addWidget(label)
        row=QHBoxLayout();layout.addLayout(row);self.format=QComboBox()
        for label,value in FORMATS:self.format.addItem(label,value)
        if passwords:self.format.setCurrentIndex(self.format.findData('passwords'))
        row.addWidget(self.format,1);choose=QPushButton('Choose export…');choose.clicked.connect(self.choose);row.addWidget(choose)
        self.path=QLabel('No file selected');self.path.setWordWrap(True);layout.addWidget(self.path)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['Use','Name','Service / store','Host / URL','Username','Secrets','Notes']);self.table.setEditTriggers(self.table.NoEditTriggers);self.table.setSelectionBehavior(self.table.SelectRows);self.table.setSelectionMode(self.table.SingleSelection);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setSectionResizeMode(6,QHeaderView.Stretch);layout.addWidget(self.table,1)
        self.details=QLabel();self.details.setWordWrap(True);self.details.setTextFormat(Qt.PlainText);layout.addWidget(self.details);self.table.itemSelectionChanged.connect(self.update_details)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.warnings=QPlainTextEdit();self.warnings.setReadOnly(True);self.warnings.setMaximumHeight(110);layout.addWidget(self.warnings)
        select=QHBoxLayout();layout.addLayout(select)
        for title,checked in [('Select all',True),('Select none',False)]:
            button=QPushButton(title);button.clicked.connect(lambda _,checked=checked:self.select_all(checked));select.addWidget(button)
        select.addWidget(QLabel('Duplicates:'));self.duplicates=QComboBox()
        for title,value in [('Rename imported copies','rename'),('Skip existing entries','skip'),('Replace existing entries','replace')]:self.duplicates.addItem(title,value)
        select.addWidget(self.duplicates,1)
        self.secrets=QCheckBox('Include available passwords, passphrases and private-key copies');self.secrets.setChecked(True);layout.addWidget(self.secrets)
        hint=QLabel('Browser CSV exports are plaintext. After importing, remove the export when you no longer need it. Referenced key files are not copied or opened by the importer.');hint.setWordWrap(True);layout.addWidget(hint)
        row=QHBoxLayout();layout.addLayout(row)
        cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        self.commit=QPushButton('Import selected entries');self.commit.setEnabled(False);self.commit.clicked.connect(self.import_selected);row.addWidget(self.commit)
        self.format.currentIndexChanged.connect(self.clear_preview)
    def clear_preview(self,*args):self.items=[];self.table.setRowCount(0);self.warnings.clear();self.summary.clear();self.details.clear();self.path.setText('No file selected');self.commit.setEnabled(False)
    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose an exported session/password file',str(Path.home()),'Exports (*.csv *.reg *.ini *.mxtsessions *.conf *.json);;All files (*)')
        if not path:return
        self.clear_preview()
        try:
            source=Path(path)
            if source.stat().st_size>LIMIT:raise ValueError('Export is larger than 10 MiB. Split it into smaller files.')
            items,warnings=parse_export(source.read_bytes(),self.format.currentData());self.show_preview(items,warnings);self.path.setText(path)
        except (ValueError,OSError,configparser.Error,csv.Error,OverflowError) as error:QMessageBox.warning(self,'Import could not be read',str(error))
    def show_preview(self,items,warnings):
        self.items=items;self.table.setRowCount(0)
        for item in items:
            row=self.table.rowCount();self.table.insertRow(row);check=QTableWidgetItem();check.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable);check.setCheckState(Qt.Checked);self.table.setItem(row,0,check)
            data=item['data'];available=', '.join(label for key,label in [('password','Password'),('passphrase','Passphrase'),('key_data','Key copy'),('key','Key path')] if data.get(key)) or 'None'
            values=(item['name'],data.get('protocol','Password manager'),display_url(data.get('host',data.get('url',''))),data.get('user',''),available,item.get('notes',''))
            for col,value in enumerate(values,1):self.table.setItem(row,col,QTableWidgetItem(str(value)))
        self.warnings.setPlainText('\n'.join(warnings));self.summary.setText(f'{len(items)} supported entries. Missing or encrypted source secrets must be entered again.');self.commit.setEnabled(bool(items))
        for col,width in enumerate((55,150,125,160,120,135)):self.table.setColumnWidth(col,width)
        if items:self.table.selectRow(0)
    def update_details(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.items):self.details.clear();return
        item=self.items[row];text=item.get('notes','');key=item['data'].get('key')
        if key:text+='\nKey path: '+key
        self.details.setText(text.strip())
    def select_all(self,checked):
        for row in range(self.table.rowCount()):self.table.item(row,0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
    def import_selected(self):
        items=[]
        for row,item in enumerate(self.items):
            if self.table.item(row,0).checkState()!=Qt.Checked:continue
            value=dict(item,data=dict(item['data']))
            if not self.secrets.isChecked():
                for key in ('password','passphrase','key_data'):value['data'].pop(key,None)
                value['data']['remember']=False
            items.append(value)
        if not items:QMessageBox.information(self,'Import','Select at least one entry.');return
        if self.duplicates.currentData()=='replace' and QMessageBox.question(self,'Replace existing entries',f'Import {len(items)} selected entries and replace matching saved entries?')!=QMessageBox.Yes:return
        try:
            counts=self.vault.import_records(items,self.duplicates.currentData())
            QMessageBox.information(self,'Import complete',f'{counts["profiles"]} device profiles and {counts["passwords"]} password-manager entries imported. {counts["skipped"]} duplicates skipped. No sessions were connected.');self.accept()
        except (VaultError,OSError,ValueError) as error:QMessageBox.warning(self,'Import was not saved',str(error))
