"""Persistent console appearance options shared by all session tabs."""
from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QPalette,QColor
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QDialog,QFormLayout,QSpinBox,QComboBox,QCheckBox,QDialogButtonBox


def preferences():
    s=QSettings('MusicCityTelecom','SerialDesk')
    return dict(ui_font=max(9,min(16,s.value('console/ui_font',10,type=int))),font=max(8,min(28,s.value('console/font',11,type=int))),scrollback=max(1000,min(100000,s.value('console/scrollback',12000,type=int))),blink=s.value('console/blink',True,type=bool),shape=s.value('console/cursor','Block'))


class ConsoleSettings(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Console appearance');form=QFormLayout(self);p=preferences()
        self.ui_font=QSpinBox();self.ui_font.setRange(9,16);self.ui_font.setValue(p['ui_font']);form.addRow('Interface font size',self.ui_font)
        self.font_size=QSpinBox();self.font_size.setRange(8,28);self.font_size.setValue(p['font']);form.addRow('Font size',self.font_size)
        self.scrollback=QSpinBox();self.scrollback.setRange(1000,100000);self.scrollback.setSingleStep(1000);self.scrollback.setValue(p['scrollback']);form.addRow('Scrollback lines',self.scrollback)
        self.shape=QComboBox();self.shape.addItems(['Block','Bar']);self.shape.setCurrentText(p['shape']);form.addRow('Console cursor',self.shape)
        self.blink=QCheckBox('Blink the active cursor');self.blink.setChecked(p['blink']);form.addRow(self.blink)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
    def accept(self):
        s=QSettings('MusicCityTelecom','SerialDesk')
        for key,value in dict(ui_font=self.ui_font.value(),font=self.font_size.value(),scrollback=self.scrollback.value(),cursor=self.shape.currentText(),blink=self.blink.isChecked()).items():s.setValue('console/'+key,value)
        super().accept()


def apply_palette():
    palette=QPalette()
    for role,color in ((QPalette.Window,'#121a25'),(QPalette.WindowText,'#dce6f2'),(QPalette.Base,'#090f18'),(QPalette.AlternateBase,'#1c2939'),(QPalette.Text,'#dce6f2'),(QPalette.Button,'#243449'),(QPalette.ButtonText,'#dce6f2'),(QPalette.Highlight,'#326e91'),(QPalette.HighlightedText,'#ffffff'),(QPalette.ToolTipBase,'#243449'),(QPalette.ToolTipText,'#ffffff'),(QPalette.Light,'#64748b'),(QPalette.Mid,'#34465e'),(QPalette.Dark,'#0c1018')):palette.setColor(role,QColor(color))
    palette.setColor(QPalette.Disabled,QPalette.Text,QColor('#8095ad'));palette.setColor(QPalette.Disabled,QPalette.ButtonText,QColor('#8095ad'))
    QApplication.setPalette(palette)
