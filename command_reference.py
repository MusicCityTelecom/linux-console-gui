"""Offline diagnostic quick reference. Copy/insert only; never execute commands."""
from PyQt5.QtCore import Qt,QSettings,QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QComboBox,QLineEdit,QTableWidget,
    QTableWidgetItem,QHeaderView,QLabel,QPushButton,QApplication,QMessageBox)

# Short examples, independently written descriptions, and upstream references.
# Device syntax varies by firmware, model and privilege level; ?/help is useful.
SOURCES={
 'Linux':'https://man7.org/linux/man-pages/',
 'Windows — Command Prompt':'https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/windows-commands',
 'Windows — PowerShell':'https://learn.microsoft.com/en-us/powershell/module/',
 'macOS':'https://github.com/apple-oss-distributions/network_cmds',
 'Cisco IOS / IOS XE':'https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9600/software/release/17-9/command_reference/b_179_9600_cr/interface_and_hardware_commands.html',
 'HP / ArubaOS-Switch':'https://arubanetworking.hpe.com/techdocs/AOS-Switch/',
 'Aruba AOS-CX':'https://arubanetworking.hpe.com/techdocs/AOS-CX/10.16/PDF/cli_5420.pdf',
 'HPE / H3C Comware':'https://www.h3c.com/en/Support/Resource_Center/EN/Home/Public/00-Public/Technical_Documents/Reference_Guides/Command_References/H3C_CR-28394/05/202508/2623864_294551_0.htm',
 'ADTRAN AOS':'https://community.adtran.com/',
 'Juniper Junos':'https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/command/show-interfaces-terse.html',
 'MikroTik RouterOS':'https://manual.mikrotik.com/docs/introduction/',
}
GROUPS={
 'Linux':[
 ('System','uname -a','Kernel and machine information',''),
 ('Network','ip address show','Interface addresses','iproute2'),
 ('Network','ip route show','Routing table','iproute2'),
 ('Network','ip neighbor show','ARP / neighbor cache','iproute2'),
 ('Network','ss -tulpn','Listening TCP/UDP sockets','Process details may require elevated privileges.'),
 ('Network','ping -c 4 <host>','Test reachability','Replace <host>; sends diagnostic traffic.'),
 ('Network','traceroute <host>','Trace the network path','Optional traceroute package.'),
 ('Network','dig <name>','Query DNS','Optional DNS utilities package.'),
 ('Files','ls -lah','List files, including hidden files',''),
 ('Files','df -h','Filesystem space',''),
 ('Files','lsblk','Block devices and mounts','Linux util-linux'),
 ('Files','sha256sum <file>','SHA-256 checksum','Quote paths containing spaces.'),
 ('Services','systemctl status <unit>','Service status','systemd systems; press q to leave the pager.'),
 ('Services','journalctl -u <unit> --since "1 hour ago"','Recent service logs','systemd; logs can contain sensitive data.'),
 ],
 'Windows — Command Prompt':[
 ('System','systeminfo','Operating-system details',''),('System','whoami /all','Current identity and privileges',''),
 ('System','tasklist','Running processes',''),('Network','ipconfig /all','Adapter, address and DNS details',''),
 ('Network','route print','Routing table',''),('Network','arp -a','ARP cache',''),
 ('Network','netstat -ano','Connections and process IDs',''),('Network','ping -n 4 <host>','Test reachability','Replace <host>.'),
 ('Network','tracert <host>','Trace the network path',''),('Network','nslookup <name>','Query DNS',''),
 ('Services','sc query <service>','Service state','Command Prompt; use sc.exe in PowerShell.'),
 ],
 'Windows — PowerShell':[
 ('Network','Get-NetIPConfiguration','IP and gateway configuration','PowerShell on Windows.'),
 ('Network','Get-NetAdapter','Network adapters',''),('Network','Get-NetRoute','Routing table',''),
 ('Network','Test-NetConnection <host> -Port <port>','Test a TCP service','Replace both placeholders; sends diagnostic traffic.'),
 ('Network','Resolve-DnsName <name>','Query DNS',''),('Services','Get-Service <name>','Service state',''),
 ('System','Get-Process','Running processes',''),('Files','Get-FileHash -Algorithm SHA256 <path>','SHA-256 checksum','Quote paths containing spaces.'),
 ],
 'macOS':[
 ('System','sw_vers','macOS version','Use man sw_vers for local help.'),
 ('Network','ifconfig','Network interfaces','Apple network_cmds; differs from Linux ip.'),
 ('Network','netstat -rn','Routing table',''),('Network','arp -a','ARP cache',''),
 ('Network','ping -c 4 <host>','Test reachability','Replace <host>.'),('Network','traceroute <host>','Trace the network path',''),
 ('Files','df -h','Filesystem space',''),('Files','ls -lah','List files, including hidden files',''),
 ('Files','shasum -a 256 <file>','SHA-256 checksum','Quote paths containing spaces.'),
 ],
 'Cisco IOS / IOS XE':[
 ('System','show version','Software, uptime and hardware','EXEC mode; syntax depends on platform.'),
 ('System','show inventory','Inventory and serial numbers',''),('Network','show ip interface brief','Interface/IP summary',''),
 ('Network','show interfaces <interface>','Interface counters and errors','Replace the interface placeholder.'),
 ('Switching','show interfaces status','Switchport status','Catalyst switches; not all routers support this.'),
 ('Switching','show vlan brief','VLAN summary','Switching platforms.'),('Switching','show mac address-table','Learned MAC addresses',''),
 ('Network','show ip route','IPv4 routing table',''),('Network','show arp','ARP cache',''),
 ('Network','show lldp neighbors detail','LLDP neighbors','LLDP must be enabled.'),
 ('Network','show cdp neighbors detail','CDP neighbors','CDP must be enabled.'),
 ('System','show logging','Device logs','Logs may contain sensitive data.'),
 ('Configuration','show running-config','Active configuration','Sensitive output; privileged EXEC.'),
 ('Session','terminal length 0','Disable paging for this session','Session setting; not a persistent configuration change.'),
 ],
 'HP / ArubaOS-Switch':[
 ('System','show version','Software version','ProCurve / ArubaOS-Switch; CX and Comware use different syntax.'),
 ('Network','show interfaces brief','Interface summary',''),('Switching','show vlan','VLANs',''),
 ('Switching','show mac-address','Learned MAC addresses',''),('Network','show ip route','IPv4 routing table',''),
 ('Network','show arp','ARP cache',''),('Network','show lldp info remote-device','LLDP neighbors',''),
 ('Configuration','show running-config','Active configuration','Sensitive output.'),
 ],
 'Aruba AOS-CX':[
 ('System','show version','Software version','CX family; differs from ArubaOS-Switch.'),
 ('Network','show interface brief','Interface summary','Singular interface in CX syntax.'),
 ('Switching','show vlan','VLANs',''),('Switching','show mac-address-table','Learned MAC addresses',''),
 ('Network','show ip route','IPv4 routing table',''),('Network','show arp','ARP cache',''),
 ('Network','show lldp neighbor-info','LLDP neighbors',''),
 ('Configuration','show running-config','Active configuration','Sensitive output.'),
 ],
 'HPE / H3C Comware':[
 ('System','display version','Software version','Comware uses display; not ProCurve/CX show syntax.'),
 ('Network','display interface brief','Interface summary',''),('Network','display ip interface brief','IPv4 interface summary',''),
 ('Switching','display vlan','VLANs',''),('Switching','display mac-address','Learned MAC addresses',''),
 ('Network','display ip routing-table','IPv4 routing table',''),('Network','display arp','ARP cache',''),
 ('Configuration','display current-configuration','Active configuration','Sensitive output.'),
 ],
 'ADTRAN AOS':[
 ('System','show version','AOS software version','NetVanta / Total Access AOS; other ADTRAN operating systems differ.'),
 ('Network','show interfaces','Interface status and counters',''),('Network','show ip interface brief','IPv4 interface summary',''),
 ('Network','show ip route','IPv4 routing table',''),('Network','show arp','ARP cache',''),
 ('Configuration','show running-config','Active configuration','Sensitive output; may require enable mode.'),
 ('Help','show ?','List supported show commands','Use device help to check model/firmware-specific syntax.'),
 ],
 'Juniper Junos':[
 ('System','show version','Junos software version','Operational mode.'),
 ('Network','show interfaces terse','Interface summary',''),('Network','show route','Routing table',''),
 ('Network','show arp','ARP cache',''),('Network','show lldp neighbors','LLDP neighbors',''),
 ('System','show chassis hardware','Hardware inventory',''),('System','show log messages | last 50','Recent messages',''),
 ('Configuration','show configuration | display set','Configuration as set statements','Sensitive output; operational mode.'),
 ],
 'MikroTik RouterOS':[
 ('System','/system resource print','System resources and version','RouterOS CLI, not a Linux shell.'),
 ('Network','/interface print','Interfaces',''),('Network','/ip address print','IPv4 addresses',''),
 ('Network','/ip route print','IPv4 routes',''),('Network','/ip arp print','ARP cache',''),
 ('System','/log print','Device logs','Logs may contain sensitive data.'),
 ]}
COMMANDS=[dict(platform=platform,category=category,command=command,purpose=purpose,notes=notes,source=SOURCES[platform])
          for platform,rows in GROUPS.items() for category,command,purpose,notes in rows]


class CommandReference(QDialog):
    def __init__(self,window):
        super().__init__(window,Qt.Window);self.window=window;self.setWindowTitle('Command reference — F1');self.setModal(False)
        screen=self.screen().availableGeometry();self.resize(min(970,screen.width()-40),min(500,screen.height()-60))
        layout=QVBoxLayout(self);row=QHBoxLayout();layout.addLayout(row)
        self.platform=QComboBox();self.platform.addItems(['All platforms']+list(GROUPS));row.addWidget(self.platform)
        self.search=QLineEdit();self.search.setPlaceholderText('Find a task, command or keyword…');row.addWidget(self.search,1)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Platform','Category','Command','Purpose','Notes']);self.table.setSelectionBehavior(self.table.SelectRows);self.table.setSelectionMode(self.table.SingleSelection);self.table.setEditTriggers(self.table.NoEditTriggers);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch);layout.addWidget(self.table,1)
        self.table.setColumnWidth(0,155);self.table.setColumnWidth(1,90);self.table.setColumnWidth(2,250);self.table.setColumnWidth(3,190)
        self.details=QLabel();self.details.setWordWrap(True);self.details.setTextFormat(Qt.PlainText);self.details.setTextInteractionFlags(Qt.TextSelectableByMouse);layout.addWidget(self.details)
        self.table.itemSelectionChanged.connect(self.update_details)
        hint=QLabel('Offline quick reference. Replace <placeholders> and verify the platform, firmware and privilege level. Insert only fills the command bar; press Send yourself. Configuration/log output may include secrets.');hint.setWordWrap(True);layout.addWidget(hint)
        row=QHBoxLayout();layout.addLayout(row)
        for title,callback in [('Copy command',self.copy),('Insert for review',self.insert),('Documentation',self.docs),('Close',self.hide)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        self.platform.currentTextChanged.connect(self.refresh);self.search.textChanged.connect(self.refresh);self.refresh()
    def open_reference(self):self.show();self.raise_();self.activateWindow();self.search.setFocus()
    def refresh(self,*args):
        query=self.search.text().casefold();platform=self.platform.currentText();self.table.setRowCount(0)
        for entry in COMMANDS:
            if platform!='All platforms' and entry['platform']!=platform:continue
            if query and query not in ' '.join(entry.values()).casefold():continue
            row=self.table.rowCount();self.table.insertRow(row)
            for col,key in enumerate(('platform','category','command','purpose','notes')):
                item=QTableWidgetItem(entry[key]);item.setData(Qt.UserRole,entry);self.table.setItem(row,col,item)
        if self.table.rowCount():self.table.selectRow(0)
    def selected(self):
        row=self.table.currentRow();return self.table.item(row,0).data(Qt.UserRole) if row>=0 else None
    def update_details(self):
        entry=self.selected();self.details.setText((entry['command']+' — '+entry['purpose']+'\n'+entry['platform']+': '+entry['notes']) if entry else '')
    def copy(self):
        entry=self.selected()
        if entry:QApplication.clipboard().setText(entry['command'])
    def insert(self):
        entry=self.selected();target=self.window.tabs.currentWidget();field=getattr(target,'input',getattr(target,'send_input',None))
        if not entry:return
        if field is None:QMessageBox.information(self,'Command reference','Select a console tab with a command bar first.');return
        field.setText(entry['command']);self.window.statusBar().showMessage('Command inserted for review. Replace placeholders before sending.',7000)
    def docs(self):
        entry=self.selected()
        if entry:QDesktopServices.openUrl(QUrl(entry['source']))
