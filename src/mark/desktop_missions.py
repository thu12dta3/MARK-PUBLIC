
import json
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .missions import MissionStore, MissionRunner, default_workspace_tools, generate_plan


class _PlanWorker(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None
        self.finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    @pyqtSlot()
    def _deliver(self):
        if self.error:
            self.failed.emit(self.error)
        else:
            self.ready.emit(self.result)


OPERATIONS = (
    ("workspace.list", "Listar arquivos do workspace", True),
    ("workspace.read", "Ler arquivos de texto", True),
    ("workspace.write", "Criar/editar arquivos e gerar diff", False),
    ("workspace.verify", "Executar testes Python isolados", False),
)


class MissionDialog(QDialog):
    def __init__(self, parent=None, store: MissionStore | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tarefas do MARK")
        self.setMinimumSize(780, 600)
        self.store = store or MissionStore()
        self.runner = MissionRunner(self.store, default_workspace_tools(self.store))
        self.grants = {}
        self.active_mission = None
        self.planner = None
        root = QVBoxLayout(self)
        heading = QLabel("Missões locais do Engineer")
        heading.setObjectName("welcomeTitle")
        root.addWidget(heading)
        note = QLabel("Cada missão fica no PC, tem escopo e expiração próprios. O plano é uma lista validada de ferramentas; nenhuma ordem do Qwen executa por conta própria.")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        self.objective = QLineEdit()
        self.objective.setPlaceholderText("Objetivo da missão")
        form.addRow("Objetivo", self.objective)
        workspace_row = QHBoxLayout()
        self.workspace = QLineEdit()
        self.workspace.setPlaceholderText("Selecione uma pasta de projeto")
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self.choose_workspace)
        workspace_row.addWidget(self.workspace, 1)
        workspace_row.addWidget(browse)
        form.addRow("Workspace", workspace_row)
        root.addLayout(form)

        self.operation_checks = {}
        permissions = QHBoxLayout()
        for operation, label, default in OPERATIONS:
            check = QCheckBox(label)
            check.setChecked(default)
            self.operation_checks[operation] = check
            permissions.addWidget(check)
        permissions.addStretch(1)
        root.addWidget(QLabel("Permissões desta missão"))
        root.addLayout(permissions)
        ttl_row = QHBoxLayout()
        ttl_row.addWidget(QLabel("Expira em"))
        self.ttl = QSpinBox()
        self.ttl.setRange(1, 24)
        self.ttl.setValue(1)
        self.ttl.setSuffix(" h")
        ttl_row.addWidget(self.ttl)
        ttl_row.addWidget(QLabel("A autorização é local e fica vinculada à pasta selecionada."))
        ttl_row.addStretch(1)
        root.addLayout(ttl_row)
        create = QPushButton("Criar e autorizar missão")
        create.setObjectName("primaryButton")
        create.clicked.connect(self.create_mission)
        root.addWidget(create, alignment=Qt.AlignmentFlag.AlignLeft)

        self.missions = QTableWidget(0, 4)
        self.missions.setObjectName("missionTable")
        self.missions.setHorizontalHeaderLabels(["Estado", "Objetivo", "Etapas", "Atualização"])
        self.missions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.missions.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.missions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.missions.horizontalHeader().setStretchLastSection(True)
        self.missions.horizontalHeader().setSectionResizeMode(1, self.missions.horizontalHeader().ResizeMode.Stretch)
        self.missions.itemSelectionChanged.connect(self._select_from_table)
        root.addWidget(self.missions, 1)
        self.report = QPlainTextEdit()
        self.report.setObjectName("missionReport")
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(150)
        self.report.setPlaceholderText("O relatório real de cada etapa aparece aqui.")
        root.addWidget(self.report)

        steps_label = QLabel("Etapas autorizadas")
        root.addWidget(steps_label)
        self.plan_button = QPushButton("Planejar com o Qwen local")
        self.plan_button.clicked.connect(self.generate_plan)
        root.addWidget(self.plan_button, alignment=Qt.AlignmentFlag.AlignLeft)
        step_row = QHBoxLayout()
        self.tool = QComboBox()
        for operation, label, _ in OPERATIONS:
            self.tool.addItem(label, operation)
        self.arguments = QPlainTextEdit()
        self.arguments.setMaximumHeight(74)
        self.arguments.setPlaceholderText('Argumentos JSON, por exemplo: {"relative":"README.md"}')
        add = QPushButton("Adicionar etapa")
        add.clicked.connect(self.add_step)
        step_row.addWidget(self.tool)
        step_row.addWidget(self.arguments, 1)
        step_row.addWidget(add)
        root.addLayout(step_row)

        controls = QHBoxLayout()
        self.reauthorize = QPushButton("Vincular pasta e autorizar")
        self.reauthorize.clicked.connect(self.reauthorize_selected)
        self.execute = QPushButton("Executar fila")
        self.execute.setObjectName("primaryButton")
        self.execute.clicked.connect(self.execute_selected)
        self.pause = QPushButton("Pausar")
        self.pause.clicked.connect(self.pause_selected)
        self.cancel = QPushButton("Cancelar")
        self.cancel.clicked.connect(self.cancel_selected)
        refresh = QPushButton("Atualizar")
        refresh.clicked.connect(self.refresh)
        for button in (self.reauthorize, self.execute, self.pause, self.cancel, refresh):
            controls.addWidget(button)
        root.addLayout(controls)
        self.status = QLabel("Nenhuma missão em execução.")
        root.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def choose_workspace(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar workspace")
        if folder:
            self.workspace.setText(folder)

    def create_mission(self):
        try:
            operations = {key for key, checkbox in self.operation_checks.items() if checkbox.isChecked()}
            if not operations:
                raise ValueError("Marque pelo menos uma permissão para esta missão.")
            mission_id = self.store.create(self.objective.text(), workspace=Path(self.workspace.text()))
            grant = self.store.approve(mission_id, operations, int(self.ttl.value()) * 3600)
            self.grants[mission_id] = grant
            self.active_mission = mission_id
            self.refresh()
            self.status.setText("Missão criada e escopo autorizado. Adicione as etapas antes de executar.")
        except Exception as exc:
            QMessageBox.warning(self, "Missão", f"Não foi possível criar a missão ({type(exc).__name__}): {exc}")

    def selected_id(self):
        row = self.missions.currentRow()
        item = self.missions.item(row, 1) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else self.active_mission

    def _select_from_table(self):
        selected = self.selected_id()
        if selected:
            self.active_mission = selected

    def add_step(self):
        mission_id = self.selected_id()
        try:
            if not mission_id:
                raise ValueError("Selecione ou crie uma missão primeiro.")
            tool = self.tool.currentData()
            grant = self.grants.get(mission_id)
            if grant is None or tool not in grant.operations:
                raise PermissionError("A missão não recebeu essa permissão. Reautorize com o escopo necessário.")
            arguments = json.loads(self.arguments.toPlainText() or "{}")
            ordinal = self.store.add_step(mission_id, tool, arguments)
            self.arguments.clear()
            self.status.setText(f"Etapa {ordinal} adicionada.")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Etapa", f"Não foi possível adicionar ({type(exc).__name__}): {exc}")

    def generate_plan(self):
        mission_id = self.selected_id()
        grant = self.grants.get(mission_id)
        try:
            if not mission_id or not grant:
                raise PermissionError("Crie ou reautorize a missão nesta sessão.")
            mission = next((item for item in self.store.list_missions() if item["id"] == mission_id), None)
            if not mission:
                raise ValueError("Missão inexistente.")
            from .config import load_config
            cfg = load_config()
            self.plan_button.setEnabled(False)
            self.status.setText("Qwen local analisando a lista de arquivos e alguns fontes…")
            self.planner = _PlanWorker(lambda: generate_plan(mission["objective"], Path(mission["workspace"]),
                                                              grant, cfg, self.store), self)
            self.planner.ready.connect(lambda steps, mid=mission_id: self._plan_ready(mid, steps))
            self.planner.failed.connect(self._plan_failed)
            self.planner.start()
        except Exception as exc:
            QMessageBox.warning(self, "Planejamento", f"Não iniciei o plano ({type(exc).__name__}): {exc}")

    def _plan_ready(self, mission_id, steps):
        try:
            self.store.add_steps(mission_id, steps)
            self.status.setText(f"Plano local gerado: {len(steps)} etapa(s). Revise-as antes de iniciar a execução.")
            self.report.setPlainText(json.dumps({"mission": mission_id, "proposed_steps": steps},
                                                ensure_ascii=False, indent=2))
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Plano", f"Não consegui salvar o plano ({type(exc).__name__}): {exc}")
        finally:
            self.plan_button.setEnabled(True)
            self.planner = None

    def _plan_failed(self, message):
        self.plan_button.setEnabled(True)
        self.status.setText("Plano não gerado. Nenhuma etapa foi executada.")
        QMessageBox.warning(self, "Planejamento local", message[:1000])
        self.planner = None

    def reauthorize_selected(self):
        mission_id = self.selected_id()
        if not mission_id:
            return
        try:
            mission = next((item for item in self.store.list_missions() if item["id"] == mission_id), None)
            if mission is None:
                raise KeyError("Missão inexistente.")
            if not mission.get("workspace"):
                workspace = self.workspace.text().strip()
                if not workspace:
                    raise ValueError("Escolha a pasta do projeto antes de autorizar esta missão.")
                self.store.set_workspace(mission_id, Path(workspace))
            operations = {key for key, checkbox in self.operation_checks.items() if checkbox.isChecked()}
            grant = self.store.approve(mission_id, operations, int(self.ttl.value()) * 3600)
            self.grants[mission_id] = grant
            self.status.setText("Pasta vinculada e escopo autorizado localmente. Revise o plano antes de executar.")
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Autorização", f"Não foi possível autorizar ({type(exc).__name__}): {exc}")

    def execute_selected(self):
        mission_id = self.selected_id()
        grant = self.grants.get(mission_id)
        try:
            if not mission_id or not grant:
                raise PermissionError("Crie ou reautorize a missão nesta sessão antes de executar.")
            self.runner.enqueue(mission_id, grant)
            self.status.setText("Fila iniciada; o executor permanece no PC e processa uma etapa por vez.")
        except Exception as exc:
            QMessageBox.warning(self, "Execução", f"A fila não iniciou ({type(exc).__name__}): {exc}")
        self.refresh()

    def pause_selected(self):
        mission_id = self.selected_id()
        if mission_id:
            try:
                self.runner.pause(mission_id)
                self.status.setText("Pausa solicitada. Se uma etapa já estava executando, ela será bloqueada para inspeção.")
            except Exception as exc:
                QMessageBox.warning(self, "Pausa", str(exc))

    def cancel_selected(self):
        mission_id = self.selected_id()
        if mission_id:
            try:
                self.runner.cancel(mission_id)
                self.status.setText("Cancelamento solicitado; etapas interrompidas durante uma operação exigem inspeção.")
            except Exception as exc:
                QMessageBox.warning(self, "Cancelar", str(exc))

    def refresh(self):
        try:
            missions = self.store.list_missions()
        except Exception as exc:
            self.status.setText(f"Não consegui carregar tarefas ({type(exc).__name__}).")
            return
        selected = self.selected_id()
        self.missions.setRowCount(0)
        for mission in missions:
            row = self.missions.rowCount()
            self.missions.insertRow(row)
            state = QTableWidgetItem(mission["state"])
            objective = QTableWidgetItem(mission["objective"])
            objective.setData(Qt.ItemDataRole.UserRole, mission["id"])
            count = QTableWidgetItem(f"{len(mission['steps'])} etapa(s)")
            updated = QTableWidgetItem(mission["updated_at"])
            for col, item in enumerate((state, objective, count, updated)):
                item.setToolTip(item.text())
                self.missions.setItem(row, col, item)
            if mission["id"] == selected:
                self.missions.selectRow(row)
        if missions and not selected:
            self.missions.selectRow(0)
        if selected:
            match = next((m for m in missions if m["id"] == selected), None)
            if match:
                self.status.setText(match["last_error"] or f"{match['state']} · {len(match['steps'])} etapa(s)")
                lines = [f"Missão: {match['objective']}", f"Estado: {match['state']}",
                         f"Workspace: {match['workspace']}"]
                if match["last_error"]:
                    lines.append(f"Intervenção: {match['last_error']}")
                for step in match["steps"]:
                    lines.extend((f"", f"Etapa {step['ordinal']} · {step['tool']} · {step['state']}"))
                    if step["error_type"]:
                        lines.append(f"Erro: {step['error_type']}")
                    if step["result"]:
                        lines.append(step["result"][:4000])
                self.report.setPlainText("\n".join(lines))

    def closeEvent(self, event):
        if self.planner is not None and self.planner.isRunning():
            self.status.setText("O planejamento local ainda está aguardando o Qwen. Feche após ele terminar.")
            event.ignore()
            return
        self.timer.stop()
        self.runner.close()
        super().closeEvent(event)
