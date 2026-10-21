
import json
from pathlib import Path
import queue
import threading
import time
from PyQt6.QtCore import QThread,QTimer,Qt,pyqtSignal,QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QLineEdit,
                            QTableWidget,QTableWidgetItem,QProgressBar,QFileDialog,QInputDialog,QMessageBox,QSplitter,QGridLayout,QMenu,QDialog,QFormLayout,QDialogButtonBox,QHeaderView)
from .cognition.library import Library,DEFAULT_DB,ERRORS

class LibraryWorker(QThread):
    updated=pyqtSignal(dict)
    failed=pyqtSignal(str)
    def __init__(self,db=DEFAULT_DB,root=None,parent=None):
        super().__init__(parent);self.db=db;self.root=root;self.commands=queue.Queue();self.stop=threading.Event()
        self.cancel_ids=set();self.cancel_all=False;self.cancel_guard=threading.Lock();self.running=False
    def submit(self,command,*args):

        if command=='cancel':
            with self.cancel_guard:
                if args and args[0]:self.cancel_ids.update(args[0])
                else:self.cancel_all=True
        self.commands.put((command,args))
    def cancelled(self,item):
        with self.cancel_guard:return self.cancel_all or item in self.cancel_ids
    def import_loop(self,index):
        try:
            with Library(self.db,self.root) as library:
                while not self.stop.is_set():
                    if index>=library.concurrency() or not library.process_one(self.stop.is_set,self.cancelled):self.stop.wait(.1)
        except Exception as exc:self.failed.emit(type(exc).__name__+': worker encerrado; fila preservada')
    def run(self):
        try:
            with Library(self.db,self.root) as library,library.owner():
                self.running=True;last=0;query='';collection=None;offset=0
                threads=[]
                for index in range(2):
                    thread=threading.Thread(target=self.import_loop,args=(index,),daemon=False);thread.start();threads.append(thread)
                while not self.stop.is_set():
                    while True:
                        try:command,args=self.commands.get_nowait()
                        except queue.Empty:break
                        try:
                            if command=='search':query,collection,offset=args
                            elif command=='enqueue':library.enqueue(*args,cancel=self.stop.is_set)
                            elif command=='reprocess':
                                for document in library.documents():
                                    if document['id'] in args[0]:library.enqueue([document['managed'] or document['original']],document['collection'],'managed' if document['managed'] else 'reference')
                            else:getattr(library,command)(*args)
                            if command=='cancel':
                                with self.cancel_guard:self.cancel_all=False;self.cancel_ids.clear()
                        except Exception as exc:self.failed.emit(type(exc).__name__+': operação não concluída')
                        last=0
                    if time.monotonic()-last>.5:
                        self.updated.emit({'stats':library.stats(),'documents':library.documents(query,collection,limit=501,offset=offset),'queue':library.queue()});last=time.monotonic()
                    self.stop.wait(.1)
                for thread in threads:thread.join()
        except Exception as exc:self.failed.emit(type(exc).__name__+': biblioteca indisponível; dados preservados')
        finally:self.running=False

class DropArea(QLabel):
    files=pyqtSignal(list)
    def __init__(self):
        super().__init__('Arraste arquivos ou pastas aqui\nImportação local · PDF, DOCX, EPUB e textos');self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter);self.setMinimumHeight(90);self.setObjectName('libraryDrop')
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):event.acceptProposedAction()
    def dropEvent(self,event):
        urls=event.mimeData().urls()
        if urls and all(u.isLocalFile() for u in urls):self.files.emit([u.toLocalFile() for u in urls]);event.acceptProposedAction()

class LibraryPage(QWidget):
    def __init__(self,parent=None,db=DEFAULT_DB,root=None):
        super().__init__(parent);self.setObjectName('contentArea');self.documents=[];self.jobs=[];self.snapshot={};self.offset=0
        layout=QVBoxLayout(self);layout.setContentsMargins(30,26,30,22);layout.setSpacing(16)
        header=QHBoxLayout();title=QLabel('Biblioteca de Memória');title.setObjectName('title');header.addWidget(title);header.addStretch()
        self.add_button=QPushButton('Adicionar');self.add_button.setObjectName('headerAction')
        add_menu=QMenu(self);add_menu.addAction('Adicionar arquivos…',self.pick_files);add_menu.addAction('Adicionar pasta…',self.pick_folder);self.add_button.setMenu(add_menu);header.addWidget(self.add_button)
        self.more_button=QPushButton('⋯');self.more_button.setAccessibleName('Opções da biblioteca');self.more_button.setToolTip('Coleções, importação e armazenamento');self.more_button.setObjectName('iconButton')
        more=QMenu(self);more.addAction('Nova coleção…',self.new_collection);self.collection_action=more.addAction('Ativar/desativar coleção',self.toggle_collection)
        more.addSeparator();more.addAction('Opções de importação…',self.import_options);more.addAction('Espaço usado…',self.storage_details)
        more.addSeparator();more.addAction('Reprocessar arquivos com erro',lambda:self.worker.submit('retry'));more.addAction('Cancelar importações…',self.cancel)
        self.more_button.setMenu(more);header.addWidget(self.more_button);layout.addLayout(header)
        hint=QLabel('Seus documentos, disponíveis para o MARK em todos os dispositivos.');hint.setObjectName('libraryHint');layout.addWidget(hint)

        self.mode=QComboBox(self);self.mode.addItem('Referenciar original · sem cópia','reference');self.mode.addItem('Importar cópia privada','managed');self.mode.hide()
        self.parallel=QComboBox(self);self.parallel.addItem('1 worker · recomendado',1);self.parallel.addItem('2 workers',2);self.parallel.hide()
        self.parallel.currentIndexChanged.connect(lambda:self.worker.submit('concurrency',self.parallel.currentData()) if hasattr(self,'worker') else None)
        search_row=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('Buscar nos documentos…');search_row.addWidget(self.search,1)
        self.collections=QComboBox();self.collections.addItem('Todas as coleções',None);self.collections.setMaximumWidth(220);search_row.addWidget(self.collections);layout.addLayout(search_row)
        self.search_timer=QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(250);self.search_timer.timeout.connect(self.query)
        self.search.textChanged.connect(lambda:self.search_timer.start());self.collections.currentIndexChanged.connect(self.query)
        self.drop=DropArea();self.drop.setText('Arraste arquivos ou pastas para adicionar à biblioteca');self.drop.setMinimumHeight(64);self.drop.files.connect(self.enqueue);layout.addWidget(self.drop)
        status_row=QHBoxLayout();self.summary=QLabel('Abrindo biblioteca…');self.summary.setObjectName('libraryHint');status_row.addWidget(self.summary,1)
        self.queue_toggle=QPushButton('Importações');self.queue_toggle.setObjectName('secondaryButton');self.queue_toggle.setCheckable(True);self.queue_toggle.toggled.connect(lambda value:self.queue_panel.setVisible(value));status_row.addWidget(self.queue_toggle);layout.addLayout(status_row)
        self.progress=QProgressBar();self.progress.setMaximumHeight(8);self.progress.setTextVisible(False);self.progress.hide();layout.addWidget(self.progress)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Documento','Coleção','Páginas','Estado']);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide();self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setToolTip('Botão direito: ações do documento. Duplo clique: metadados.');self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.table.customContextMenuRequested.connect(self.document_menu);self.table.cellDoubleClicked.connect(lambda *_:self.metadata());layout.addWidget(self.table,1)
        self.queue_panel=QWidget();queue_layout=QVBoxLayout(self.queue_panel);queue_layout.setContentsMargins(0,0,0,0)
        queue_controls=QHBoxLayout();self.pause_button=QPushButton('Pausar');self.pause_button.clicked.connect(self.pause);queue_controls.addWidget(self.pause_button)
        cancel_button=QPushButton('Cancelar selecionados');cancel_button.clicked.connect(self.cancel);queue_controls.addWidget(cancel_button);queue_controls.addStretch();queue_layout.addLayout(queue_controls)
        self.queue_table=QTableWidget(0,4);self.queue_table.setHorizontalHeaderLabels(['Arquivo','Estado','Progresso','Motivo']);self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.queue_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection);self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.queue_table.verticalHeader().hide();self.queue_table.horizontalHeader().setStretchLastSection(True);self.queue_table.setMaximumHeight(170);queue_layout.addWidget(self.queue_table);layout.addWidget(self.queue_panel);self.queue_panel.hide()
        self.paging=QWidget();paging=QHBoxLayout(self.paging);paging.setContentsMargins(0,0,0,0);self.previous=QPushButton('Anterior');self.previous.clicked.connect(lambda:self.page(-1));paging.addWidget(self.previous)
        self.page_label=QLabel('');paging.addWidget(self.page_label);paging.addStretch();self.next=QPushButton('Próxima');self.next.clicked.connect(lambda:self.page(1));paging.addWidget(self.next);layout.addWidget(self.paging);self.paging.hide()
        self.error=QLabel();self.error.setWordWrap(True);self.error.hide();layout.addWidget(self.error)
        self.worker=LibraryWorker(db,root,self);self.worker.updated.connect(self.refresh);self.worker.failed.connect(self.show_error);self.worker.start()
    def show_error(self,message):self.error.setText(message);self.error.show()
    def import_options(self):
        dialog=QDialog(self);dialog.setWindowTitle('Opções de importação');layout=QVBoxLayout(dialog);form=QFormLayout()
        mode=QComboBox();mode.addItem('Referenciar original · sem cópia','reference');mode.addItem('Importar cópia privada','managed');mode.setCurrentIndex(self.mode.currentIndex());form.addRow('Armazenamento',mode)
        workers=QComboBox();workers.addItem('1 worker · recomendado',1);workers.addItem('2 workers',2);workers.setCurrentIndex(self.parallel.currentIndex());form.addRow('Processamento',workers);layout.addLayout(form)
        note=QLabel('As opções valem para novas importações. O modo de referência preserva o original sem copiá-lo. Nenhum documento é usado para treinamento.');note.setWordWrap(True);layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.mode.setCurrentIndex(mode.currentIndex());self.parallel.setCurrentIndex(workers.currentIndex())
    def storage_details(self):
        s=self.snapshot
        text='\n'.join(f'{title}: {s.get(key,0)/1024**2:.2f} MiB' for title,key in [('Índice','knowledge_bytes'),('Fila','queue_bytes'),('Cópias privadas','managed_bytes'),('Backup de recuperação','backup_bytes'),('Temporários de extração','temporary_bytes')])
        QMessageBox.information(self,'Espaço usado pela biblioteca',text)
    def document_menu(self,position):
        if not self.selected():return
        menu=QMenu(self)
        for label,operation in [('Abrir original',self.open_original),('Detalhes do documento',self.metadata),('Ativar/desativar para o MARK',self.toggle_document),('Reindexar',self.reprocess),('Localizar origem…',self.relocate)]:menu.addAction(label,operation)
        menu.addSeparator();menu.addAction('Remover do índice…',self.remove);menu.exec(self.table.viewport().mapToGlobal(position))
    def enqueue(self,paths):self.worker.submit('enqueue',paths,self.collections.currentData() or 'Geral',self.mode.currentData())
    def pick_files(self):
        files,_=QFileDialog.getOpenFileNames(self,'Adicionar à biblioteca','','Todos os arquivos (*);;Documentos (*.pdf *.docx *.epub *.txt *.md *.html *.csv *.json *.yaml *.xml)')
        if files:self.enqueue(files)
    def pick_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'Selecionar pasta para importar')
        if folder:self.enqueue([folder])
    def query(self,*_):
        self.offset=0
        if hasattr(self,'worker'):self.worker.submit('search',self.search.text(),self.collections.currentData(),self.offset)
    def page(self,direction):
        self.offset=max(0,self.offset+direction*500);self.worker.submit('search',self.search.text(),self.collections.currentData(),self.offset)
    def new_collection(self):
        name,ok=QInputDialog.getText(self,'Nova coleção','Nome:')
        if ok and name.strip():self.worker.submit('collection',name.strip())
    def toggle_collection(self):
        name=self.collections.currentData()
        if not name:return
        current=next((c for c in self.snapshot.get('collections',[]) if c['name']==name),None)
        if current:self.worker.submit('collection',name,not current['enabled'])
    def pause(self):self.worker.submit('pause',not self.snapshot.get('paused',False))
    def cancel(self):
        ids=[self.jobs[i.row()]['id'] for i in self.queue_table.selectionModel().selectedRows() if i.row()<len(self.jobs)]
        if not ids:
            if QMessageBox.question(self,'Cancelar fila','Cancelar todos os arquivos pendentes?')!=QMessageBox.StandardButton.Yes:return
        self.worker.submit('cancel',ids or None)
    def selected(self):
        rows=self.table.selectionModel().selectedRows();return [self.documents[i.row()] for i in rows if i.row()<len(self.documents)]
    def open_original(self):
        for doc in self.selected():
            from .cognition.library import safe_path
            try:
                path=safe_path(doc['managed'] or doc['original'])
                if path.is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                else:self.error.setText('Origem indisponível. Use Localizar origem.')
            except ValueError:self.error.setText('Caminho inválido.')
    def metadata(self):
        docs=self.selected()
        if docs:
            box=QMessageBox(self);box.setWindowTitle('Metadados do documento');box.setTextFormat(Qt.TextFormat.PlainText);box.setText(docs[0]['title']);box.setDetailedText(json.dumps(docs[0],ensure_ascii=False,indent=2));box.exec()
    def toggle_document(self):
        for doc in self.selected():self.worker.submit('enable',doc['id'],not doc['enabled'])
    def reprocess(self):self.worker.submit('reprocess',[d['id'] for d in self.selected()])
    def relocate(self):
        docs=self.selected()
        if len(docs)!=1:return
        file,_=QFileDialog.getOpenFileName(self,'Localizar o mesmo documento')
        if file:self.worker.submit('relocate',docs[0]['id'],file)
    def remove(self):
        docs=self.selected()
        if docs and QMessageBox.question(self,'Remover do índice','Remover os documentos selecionados e seus trechos? Os arquivos originais serão preservados.')==QMessageBox.StandardButton.Yes:
            for doc in docs:self.worker.submit('remove',doc['id'])
    def refresh(self,data):
        selected_ids={d['id'] for d in self.selected()};s=data['stats'];self.snapshot=s;self.documents=data['documents'][:500];self.jobs=data['queue']
        self.previous.setEnabled(self.offset>0);self.next.setEnabled(len(data['documents'])>500);self.page_label.setText(f'Página {self.offset//500+1}');self.paging.setVisible(self.offset>0 or len(data['documents'])>500)
        self.collection_action.setEnabled(self.collections.currentData() is not None)
        self.parallel.blockSignals(True);self.parallel.setCurrentIndex(self.parallel.findData(s['concurrency']));self.parallel.blockSignals(False)
        chosen=self.collections.currentData();names=[self.collections.itemData(i) for i in range(self.collections.count())]
        new=[None]+[c['name'] for c in s['collections']]
        if names!=new:
            self.collections.blockSignals(True);self.collections.clear();self.collections.addItem('Todas as coleções',None)
            for c in s['collections']:self.collections.addItem(c['name'],c['name'])
            self.collections.setCurrentIndex(max(0,self.collections.findData(chosen)));self.collections.blockSignals(False)
        pending=s['queue'].get('QUEUED',0)+s['queue'].get('RUNNING',0);completed=s['files']-pending
        self.progress.setRange(0,max(1,s['files']));self.progress.setValue(completed)
        self.add_button.setToolTip('Importar na coleção selecionada · '+self.mode.currentText());self.drop.setToolTip('PDF, DOCX, EPUB e textos. Opções de armazenamento no menu ⋯.');
        self.summary.setText(f"{s['indexed']} documentos"+(f" · {pending} em processamento" if pending else '')+(f" · {s['errors']} erros" if s['errors'] else '')+(' · Pausado' if s['paused'] else ''))
        self.progress.setVisible(pending>0);self.pause_button.setText('Retomar' if s['paused'] else 'Pausar')
        self.queue_toggle.setText('Importações'+(f' · {pending}' if pending else '')+(' ▴' if self.queue_toggle.isChecked() else ' ▾'))
        self.queue_toggle.setToolTip(f"{s.get('unsupported',0)} formatos ignorados · {s.get('ocr_required',0)} PDFs precisam de OCR · {s['errors']} erros de extração")
        self.table.setRowCount(len(self.documents))
        for i,d in enumerate(self.documents):
            level=d['metadata'].get('metadata',{}).get('extraction_level','TEXT')
            state='Referência · sem texto' if level=='METADATA_ONLY' else 'Texto parcial' if level=='PARTIAL_TEXT' else 'Desativado' if not d['enabled'] or not d['collection_enabled'] else {'AVAILABLE':'Disponível','CHANGED':'Origem alterada','UNAVAILABLE':'Origem indisponível'}.get(d['origin_status'],'Disponível')
            for col,text in enumerate([d['title'],d['collection'],d['pages'] or '—',state]):self.table.setItem(i,col,QTableWidgetItem(str(text)))
            if d['id'] in selected_ids:self.table.selectRow(i)
        self.queue_table.setRowCount(len(self.jobs))
        for i,j in enumerate(self.jobs):
            for col,text in enumerate([Path(j['path']).name,{'QUEUED':'Na fila','RUNNING':'Extraindo','INDEXED':'Indexado','DUPLICATE':'Duplicado','CANCELLED':'Cancelado','ERROR':'Erro','REFERENCE_ONLY':'Referência · sem texto','PARTIAL':'Texto parcial','SKIPPED':'Formato ignorado','NEEDS_OCR':'Precisa de OCR'}.get(j['status'],j['status']),str(j['progress'])+'%',ERRORS.get(j['error'],j['error'] or '')]):self.queue_table.setItem(i,col,QTableWidgetItem(str(text)))
        self.queue_table.resizeColumnToContents(0)
    def shutdown(self):self.worker.stop.set()
