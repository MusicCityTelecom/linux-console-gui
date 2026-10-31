"""Local file pane for terminal tabs; navigation never sends shell input."""
import os
import shlex
from pathlib import Path
from PyQt5.QtCore import Qt,QDir,QUrl,pyqtSignal,QModelIndex
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QCheckBox,QFileSystemModel,QTreeView,QMenu,QInputDialog,QMessageBox,QApplication)
from file_editing import TextEditor,read_local,decode_text,ENCODINGS


def quoted_path(path):
    if any(ord(char)<32 or ord(char)==127 for char in path):raise ValueError('Copy this path manually; control characters cannot be inserted into a command bar.')
    if os.name=='nt':
        if any(char in path for char in '%!"'):raise ValueError('This path needs manual Command Prompt escaping. Use Copy path instead.')
        return '"'+path+'"'
    return shlex.quote(path)


class LocalBrowser(QWidget):
    command_requested=pyqtSignal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.directory=str(Path.home());self.terminal_directory=self.directory;self.editors=[];self.history=[];self.history_index=-1
        self.setMinimumWidth(210);layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        title=QLabel('LOCAL FILES');layout.addWidget(title)
        self.path=QLineEdit(self.directory);self.path.setAccessibleName('Local directory');self.path.returnPressed.connect(lambda:self.navigate(self.path.text()));layout.addWidget(self.path)
        row=QHBoxLayout();layout.addLayout(row)
        for title,callback in [('←',lambda:self.go_history(-1)),('→',lambda:self.go_history(1)),('Up',lambda:self.navigate(str(Path(self.directory).parent))),('Home',lambda:self.navigate(str(Path.home())))]:
            button=QPushButton(title);button.setToolTip({'←':'Previous folder','→':'Next folder'}.get(title,title));button.clicked.connect(callback);row.addWidget(button)
        self.follow=QCheckBox('Follow terminal folder');self.follow.setChecked(True);self.follow.toggled.connect(self.follow_changed);layout.addWidget(self.follow)
        self.hidden=QCheckBox('Show hidden files');self.hidden.toggled.connect(self.update_filter);layout.addWidget(self.hidden)
        self.filter=QLineEdit();self.filter.setPlaceholderText('Filter filenames…');self.filter.textChanged.connect(self.update_filter);layout.addWidget(self.filter)
        self.model=QFileSystemModel(self);self.model.setReadOnly(True)
        self.tree=QTreeView();self.tree.setModel(self.model);self.tree.setRootIsDecorated(False);self.tree.setSortingEnabled(True);self.tree.sortByColumn(0,Qt.AscendingOrder);self.tree.setColumnWidth(0,185);self.tree.hideColumn(2);self.tree.hideColumn(3);self.tree.setSelectionMode(self.tree.SingleSelection);layout.addWidget(self.tree,1)
        self.tree.doubleClicked.connect(self.open_selected);self.tree.setContextMenuPolicy(Qt.CustomContextMenu);self.tree.customContextMenuRequested.connect(self.context_menu)
        row=QHBoxLayout();layout.addLayout(row)
        for title,callback in [('Edit text',self.edit_selected),('New…',self.new_menu),('More…',self.more_menu)]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        self.status=QLabel('Double-click a folder or text file.');self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);layout.addWidget(self.status)
        self.navigate(self.directory,manual=False);self.update_filter()
    def navigate(self,path,manual=True,history=True):
        try:
            target=Path(path).expanduser().resolve(strict=True)
            if not target.is_dir():raise ValueError('Choose a folder.')
            self.directory=str(target);self.path.setText(self.directory);self.model.setRootPath(self.directory);self.tree.setRootIndex(self.model.index(self.directory))
            self.tree.clearSelection();self.tree.setCurrentIndex(QModelIndex())
            if manual:self.follow.setChecked(False)
            if history and (self.history_index<0 or self.history[self.history_index]!=self.directory):
                self.history=self.history[:self.history_index+1]+[self.directory];self.history_index=len(self.history)-1
            self.status.setText('Following terminal folder.' if self.follow.isChecked() else 'Browsing independently • check Follow to return to the terminal folder.')
        except (OSError,ValueError) as error:self.path.setText(self.directory);self.status.setText(str(error))
    def go_history(self,offset):
        index=self.history_index+offset
        if 0<=index<len(self.history):self.history_index=index;self.navigate(self.history[index],history=False)
    def terminal_changed(self,path):
        self.terminal_directory=path
        if self.follow.isChecked() and path!=self.directory:self.navigate(path,manual=False)
    def follow_changed(self,checked):
        if checked:self.navigate(self.terminal_directory,manual=False)
        else:self.status.setText('Browsing independently • check Follow to return to the terminal folder.')
    def update_filter(self,*args):
        flags=QDir.AllEntries|QDir.AllDirs|QDir.NoDotAndDotDot
        if self.hidden.isChecked():flags|=QDir.Hidden|QDir.System
        self.model.setFilter(flags);query=self.filter.text().strip();self.model.setNameFilters(['*'+query+'*'] if query else []);self.model.setNameFilterDisables(False)
    def selected_path(self):
        index=self.tree.currentIndex();return self.model.filePath(index) if index.isValid() else self.directory
    def open_selected(self,index=None):
        path=self.model.filePath(index) if index is not None and index.isValid() else self.selected_path()
        if Path(path).is_dir():self.navigate(path)
        else:self.edit_path(path)
    def edit_selected(self):self.edit_path(self.selected_path())
    def edit_path(self,path,encoding=None):
        for editor in self.editors:
            if editor.snapshot['path']==str(Path(path).absolute()):editor.show();editor.raise_();editor.activateWindow();return editor
        try:
            snapshot=read_local(path);decode_text(snapshot['data'],encoding);editor=TextEditor(snapshot,self,encoding=encoding);self.editors.append(editor)
            editor.finished.connect(lambda *_:self.editors.remove(editor) if editor in self.editors else None);editor.show();return editor
        except (OSError,ValueError,UnicodeError) as error:QMessageBox.warning(self,'Cannot open text file',str(error))
    def edit_with_encoding(self):
        encoding,ok=QInputDialog.getItem(self,'Text encoding','Open selected text file as:',list(ENCODINGS),0,False)
        if ok:self.edit_path(self.selected_path(),encoding)
    def copy_path(self):QApplication.clipboard().setText(self.selected_path())
    def insert_path(self,cd=False):
        try:
            path=self.selected_path();path=str(Path(path) if Path(path).is_dir() else Path(path).parent) if cd else path
            command=('cd /d ' if os.name=='nt' else 'cd -- ') if cd else ''
            self.command_requested.emit(command+quoted_path(path));self.status.setText('Inserted into the command bar for review. Press Send at a shell prompt.')
        except ValueError as error:QMessageBox.information(self,'Insert path',str(error))
    def new_item(self,directory=False):
        name,ok=QInputDialog.getText(self,'New folder' if directory else 'New text file','Name:')
        if not ok or not name:return
        if name in ('.','..') or any(char in name for char in '/\\\0\r\n'):QMessageBox.warning(self,'Name','Enter a single file or folder name.');return
        path=Path(self.directory)/name
        try:
            if directory:path.mkdir()
            else:
                with path.open('xb'):pass
                path.chmod(0o600);self.edit_path(str(path))
        except OSError as error:QMessageBox.warning(self,'Create item',str(error))
    def rename(self):
        source=Path(self.selected_path())
        if str(source)==self.directory:return
        name,ok=QInputDialog.getText(self,'Rename local item','New name:',text=source.name)
        if not ok or not name or name==source.name:return
        if name in ('.','..') or any(char in name for char in '/\\\0\r\n'):QMessageBox.warning(self,'Rename','Enter a single file or folder name.');return
        target=source.with_name(name)
        if target.exists() or target.is_symlink():QMessageBox.warning(self,'Rename','That name already exists.');return
        if any(Path(editor.snapshot['path'])==source for editor in self.editors):QMessageBox.information(self,'Rename','Close the text editor for this file before renaming it.');return
        try:source.rename(target)
        except OSError as error:QMessageBox.warning(self,'Rename',str(error))
    def new_menu(self):
        menu=QMenu(self);menu.addAction('New text file…',self.new_item);menu.addAction('New folder…',lambda:self.new_item(True));menu.exec_(self.mapToGlobal(self.rect().center()))
    def more_menu(self):self.context_menu(self.tree.rect().center(),select=False)
    def context_menu(self,position,select=True):
        if select:
            index=self.tree.indexAt(position)
            if index.isValid():self.tree.setCurrentIndex(index)
        menu=QMenu(self)
        for title,callback in [('Edit text',self.edit_selected),('Open text with encoding…',self.edit_with_encoding),('Rename…',self.rename),('Copy full path',self.copy_path),('Insert quoted path',self.insert_path),('Insert cd command',lambda:self.insert_path(True)),('Open folder in system file manager',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(self.directory)))]:menu.addAction(title,callback)
        menu.exec_(self.tree.viewport().mapToGlobal(position))
    def prepare_close(self):
        for editor in list(self.editors):
            if not editor.close():return False
        return True
    def settings(self):return dict(local_cwd=self.terminal_directory,local_folder=self.directory,follow_terminal=self.follow.isChecked(),show_hidden=self.hidden.isChecked())
    def apply(self,settings):
        self.terminal_directory=settings.get('local_cwd') or str(Path.home());self.follow.setChecked(bool(settings.get('follow_terminal',True)));self.hidden.setChecked(bool(settings.get('show_hidden',False)));self.navigate(settings.get('local_folder') or self.terminal_directory,manual=False)
    def shutdown(self):
        for editor in list(self.editors):
            editor.text.document().setModified(False);editor.close()
