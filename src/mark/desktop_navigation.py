import os

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from .desktop_engineer_tasks import EngineerTasksWidget
from .desktop_helpers import _mark_icon
from .cognition.professor import MODES as STUDY_MODES


class NavigationMixin:
    def _area_page(self, key: str, title: str, description: str):
        page = QWidget()
        page.setObjectName(f"area_{key}")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 34, 40, 28)
        top_bar = QHBoxLayout()
        back_btn = QPushButton("← Voltar para Conversa")
        back_btn.setObjectName("backToChatButton")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet(
            "QPushButton#backToChatButton { background: #22262B; color: #58A6FF; border: 1px solid #30363D; "
            "border-radius: 6px; padding: 6px 12px; font-weight: 500; font-family: Inter, sans-serif; } "
            "QPushButton#backToChatButton:hover { background: #2D333B; border-color: #58A6FF; }"
        )
        back_btn.clicked.connect(lambda: self.open_specialized_chat("general") if hasattr(self, "open_specialized_chat") else None)
        top_bar.addWidget(back_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        top_bar.addStretch(1)
        layout.addLayout(top_bar)
        layout.addSpacing(6)
        eyebrow = QLabel("MARK · ÁREA DE TRABALHO")
        eyebrow.setObjectName("welcomeEyebrow")
        heading = QLabel(title)
        heading.setObjectName("welcomeTitle")
        detail = QLabel(description)
        detail.setObjectName("welcomeHint")
        detail.setWordWrap(True)
        detail.setMaximumWidth(720)
        layout.addWidget(eyebrow)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addSpacing(8)
        return page, layout

    def _build_specialization_pages(self):
        self.area_pages = {}

        engineer, engineer_layout = self._area_page(
            "engineer", "Engenheiro",
            "Um espaço de trabalho focado em programação, com modelo, contexto de projeto e tarefas visíveis.",
        )
        model_card = QFrame()
        model_card.setObjectName("engineerHero")
        model_layout = QHBoxLayout(model_card)
        model_layout.setContentsMargins(18, 14, 18, 14)
        model_layout.setSpacing(14)
        model_icon = QLabel()
        model_icon.setPixmap(_mark_icon("github").pixmap(QSize(28, 28)))
        model_layout.addWidget(model_icon)
        model_copy = QVBoxLayout()
        model_copy.setSpacing(4)
        model_title = QLabel("Perfil Engineer")
        model_title.setObjectName("engineerCardTitle")
        model_copy.addWidget(model_title)
        self.engineer_model_status = QLabel("Verificando o modelo configurado…")
        self.engineer_model_status.setObjectName("engineerModelStatus")
        self.engineer_model_status.setWordWrap(True)
        model_copy.addWidget(self.engineer_model_status)
        model_layout.addLayout(model_copy, 1)
        self.engineer_model_indicator = QLabel("…")
        self.engineer_model_indicator.setObjectName("engineerModelIndicator")
        model_layout.addWidget(self.engineer_model_indicator)
        engineer_layout.addWidget(model_card)

        project_title = QLabel("Contexto do projeto")
        project_title.setObjectName("engineerSectionTitle")
        engineer_layout.addWidget(project_title)
        self.engineer_project_input = QLineEdit()
        self.engineer_project_input.setObjectName("engineerProjectId")
        self.engineer_project_input.setPlaceholderText("ID curto e opcional, por exemplo mark-core")
        self.engineer_project_input.setAccessibleName("Projeto para associar ao contexto e à memória")
        engineer_layout.addWidget(self.engineer_project_input)
        project_hint = QLabel("O ID separa o contexto e as memórias deste projeto. Para permitir leitura, edição ou testes em arquivos, abra uma Tarefa e escolha a pasta de trabalho.")
        project_hint.setObjectName("engineerHint")
        project_hint.setWordWrap(True)
        engineer_layout.addWidget(project_hint)

        workflow_title = QLabel("Fluxo de trabalho")
        workflow_title.setObjectName("engineerSectionTitle")
        engineer_layout.addWidget(workflow_title)
        workflow_row = QHBoxLayout()
        workflow_row.setSpacing(10)
        for number, title, detail in (
            ("01", "Analisar", "Converse sobre código, erros ou arquitetura."),
            ("02", "Preparar", "Revise o plano e escolha uma pasta."),
            ("03", "Verificar", "Autorize operações e confira o relatório."),
        ):
            tile = QFrame()
            tile.setObjectName("engineerStepCard")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(14, 12, 14, 12)
            tile_layout.setSpacing(5)
            number_label = QLabel(number)
            number_label.setObjectName("engineerStepNumber")
            tile_layout.addWidget(number_label)
            step_title = QLabel(title)
            step_title.setObjectName("engineerStepTitle")
            tile_layout.addWidget(step_title)
            step_detail = QLabel(detail)
            step_detail.setObjectName("engineerHint")
            step_detail.setWordWrap(True)
            tile_layout.addWidget(step_detail)
            workflow_row.addWidget(tile, 1)
        engineer_layout.addLayout(workflow_row)

        quick_title = QLabel("Começar com")
        quick_title.setObjectName("engineerSectionTitle")
        engineer_layout.addWidget(quick_title)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for label, prompt in (
            ("Investigar um erro", "Ajude a investigar este erro. Explique a causa provável, indique quais arquivos ou informações precisa e proponha uma correção verificável."),
            ("Revisar código", "Revise este código considerando bugs, segurança, legibilidade e desempenho. Primeiro liste os achados com gravidade e evidência."),
            ("Entender um projeto", "Ajude a entender a arquitetura deste projeto. Diga quais arquivos devo anexar ou qual pasta selecionar numa Tarefa."),
        ):
            quick = QPushButton(label)
            quick.setObjectName("engineerQuickAction")
            quick.setMinimumHeight(40)
            quick.clicked.connect(lambda checked=False, text=prompt: self._start_engineer_prompt(text))
            quick_row.addWidget(quick, 1)
        engineer_layout.addLayout(quick_row)

        self.engineer_tasks = EngineerTasksWidget(
            lambda: self.open_specialized_chat("engineer"),
            self.open_missions,
            self.open_github,
            _mark_icon("github"),
        )
        engineer_layout.addWidget(self.engineer_tasks)
        engineer_content = engineer
        engineer_scroll = QScrollArea()
        engineer_scroll.setObjectName("engineerScroll")
        engineer_scroll.setWidgetResizable(True)
        engineer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        engineer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        engineer_scroll.setWidget(engineer_content)
        engineer = engineer_scroll
        self.area_pages["engineer"] = engineer
        self.page_stack.addWidget(engineer)

        professor, professor_layout = self._area_page(
            "professor", "Professor",
            "Conversas de estudo independentes, com a mesma memória salva do MARK. A Biblioteca é opcional; planos e progresso ficam locais.",
        )
        professor_layout.addWidget(QLabel("Assunto"))
        self.professor_subject = QLineEdit();self.professor_subject.setPlaceholderText("Ex.: circuitos elétricos")
        professor_layout.addWidget(self.professor_subject)
        professor_layout.addWidget(QLabel("Objetivo (opcional)"))
        self.professor_objective = QLineEdit();self.professor_objective.setPlaceholderText("O que você quer conseguir fazer?")
        professor_layout.addWidget(self.professor_objective)
        professor_layout.addWidget(QLabel("Fonte da Biblioteca (opcional; título exato)"))
        self.professor_source = QLineEdit();self.professor_source.setPlaceholderText("Ex.: Manual de eletrônica.pdf")
        professor_layout.addWidget(self.professor_source)
        study_options=QHBoxLayout()
        self.professor_mode=QComboBox()
        for mode in STUDY_MODES:self.professor_mode.addItem(mode.label,mode.key)
        self.professor_level=QComboBox()
        for level in ("iniciante","intermediário","avançado"):self.professor_level.addItem(level.capitalize(),level)
        study_options.addWidget(self.professor_mode,2);study_options.addWidget(self.professor_level,1);professor_layout.addLayout(study_options)
        study_actions=QHBoxLayout()
        professor_start=QPushButton("Preparar estudo");professor_start.setObjectName("primaryButton");professor_start.clicked.connect(self.prepare_study)
        professor_start.setAccessibleName("Iniciar um plano de estudo")
        professor_chat=QPushButton("Abrir conversa");professor_chat.clicked.connect(lambda: self.open_specialized_chat("professor"))
        professor_plan=QPushButton("Plano e progresso ▾")
        plan_menu=QMenu(professor_plan)
        plan_menu.addAction("Retomar último plano",self.resume_study_plan)
        plan_menu.addAction("Ver progresso",self.view_study_progress)
        plan_menu.addAction("Concluir plano atual",self.complete_study_plan)
        plan_menu.addSeparator();plan_menu.addAction("Apagar progresso…",self.reset_study_progress)
        professor_plan.setMenu(plan_menu)
        study_actions.addWidget(professor_chat);study_actions.addWidget(professor_start);study_actions.addWidget(professor_plan);study_actions.addStretch(1)
        professor_layout.addLayout(study_actions)
        self.professor_status=QLabel("Seu progresso só é guardado quando você prepara um plano.");self.professor_status.setWordWrap(True)
        professor_layout.addWidget(self.professor_status)
        professor_layout.addStretch(1)
        self.area_pages["professor"] = professor
        self.page_stack.addWidget(professor)

        evolution, evolution_layout = self._area_page(
            "evolution", "Evolução",
            "Acompanhe experimentos em ambiente separado. Propostas continuam dependendo de avaliação e revisão antes de serem instaladas.",
        )
        evolution_layout.addWidget(QLabel(
            "A Autoevolução de código propõe mudanças, mede e verifica cada ciclo isoladamente; só uma revisão sua instala uma proposta. O treino QLoRA é outro processo: produz um adaptador candidato e exige avaliação de qualidade, VRAM e energia."
        ))
        evolution_cards = QHBoxLayout()
        for title, detail in (
            ("Código · ciclos de melhoria", "Observa métricas → prepara uma hipótese → altera um candidato isolado → compara e registra relatório. Nunca promove mudanças sozinho."),
            ("Modelo · treino QLoRA", "Treino experimental separado do MARK em uso. O checkpoint da primeira rodada está pausado; avaliação comparativa ainda pendente."),
        ):
            card = QFrame(); card.setObjectName("engineerStepCard")
            card_layout = QVBoxLayout(card)
            heading = QLabel(title); heading.setObjectName("engineerStepTitle")
            body = QLabel(detail); body.setObjectName("engineerHint"); body.setWordWrap(True)
            card_layout.addWidget(heading); card_layout.addWidget(body)
            evolution_cards.addWidget(card, 1)
        evolution_layout.addLayout(evolution_cards)
        self.evolution_status = QLabel("Consultando o estado da rotina…")
        self.evolution_status.setObjectName("engineerMissionSummary")
        self.evolution_status.setWordWrap(True)
        evolution_layout.addWidget(self.evolution_status)
        self.evolution_progress_bar = QProgressBar()
        self.evolution_progress_bar.setObjectName("evolutionProgress")
        self.evolution_progress_bar.setRange(0, 7)
        self.evolution_progress_bar.setValue(0)
        self.evolution_progress_bar.setFormat("%v de %m etapas")
        self.evolution_progress_bar.setMaximumHeight(18)
        evolution_layout.addWidget(self.evolution_progress_bar)
        evolution_open = QPushButton("Abrir painel de evolução")
        evolution_open.setObjectName("primaryButton")
        evolution_open.clicked.connect(self.open_evolution_dialog)
        evolution_layout.addWidget(evolution_open, alignment=Qt.AlignmentFlag.AlignLeft)
        evolution_layout.addStretch(1)
        self.area_pages["evolution"] = evolution
        self.page_stack.addWidget(evolution)

        settings, settings_layout = self._area_page(
            "settings", "Configurações",
            "Resumo local e seguro das opções compartilhadas. Segredos e credenciais não são exibidos.",
        )
        self.settings_summary = QLabel("Carregando…")
        self.settings_summary.setObjectName("settingsSummary")
        self.settings_summary.setWordWrap(True)
        settings_layout.addWidget(self.settings_summary)
        refresh_settings = QPushButton("Atualizar resumo")
        refresh_settings.clicked.connect(self.refresh_settings_summary)
        settings_layout.addWidget(refresh_settings, alignment=Qt.AlignmentFlag.AlignLeft)
        providers_button = QPushButton("Modelos e APIs…")
        providers_button.setObjectName("primaryButton")
        providers_button.clicked.connect(self.open_provider_manager)
        settings_layout.addWidget(providers_button, alignment=Qt.AlignmentFlag.AlignLeft)
        manage_memories = QPushButton("Gerenciar memórias salvas…")
        manage_memories.setObjectName("manageMemories")
        manage_memories.clicked.connect(self.open_memory_manager)
        settings_layout.addWidget(manage_memories, alignment=Qt.AlignmentFlag.AlignLeft)
        settings_layout.addStretch(1)
        self.area_pages["settings"] = settings
        self.page_stack.addWidget(settings)

    def open_area(self, area: str):
        if area in self.area_buttons:
            self._set_active_area(area)
        if area == "library":
            self.open_library()
            return
        page = self.area_pages.get(area)
        if page is None:
            if area == "general":
                self.open_specialized_chat("general")
            return
        self.page_stack.setCurrentWidget(page)

        if area == "engineer":
            self._refresh_engineer_overview()
        if area == "evolution":
            self._refresh_evolution_overview()

    def _start_engineer_prompt(self, prompt: str):
        self.open_specialized_chat("engineer")
        self._prefill_message(prompt)

    def open_specialized_chat(self, specialization: str):
        if specialization not in {"general", "engineer", "professor"}:
            raise ValueError("Área de conversa desconhecida")
        if self.current_specialization in {"engineer", "professor"}:
            self.specialization_conversations[self.current_specialization] = self.current_conversation_id
        self.current_specialization = specialization
        self._set_active_area(specialization)
        self.engineer_work_feedback.hide()
        if specialization == "engineer":
            if self._engineer_profile_restore is None:
                self._engineer_profile_restore = self.model_selector.currentIndex()
            if hasattr(self.model_selector, "select_model_by_name"):
                if not self.model_selector.select_model_by_name("Claude 3.5 Sonnet"):
                    self.model_selector.select_model_by_name("Qwen 2.5 Coder")
            self.model_selector.setToolTip("O Engineer usa o perfil de código configurado e a memória compartilhada.")
            self.model_selector.setEnabled(False)
            self.engineer_chat_badge.setText("ENGINEER · PERFIL DE CÓDIGO")
            self.engineer_chat_badge.show()
        else:
            if self._engineer_profile_restore is not None:
                self.model_selector.setEnabled(True)
                self.model_selector.setCurrentIndex(self._engineer_profile_restore)
                self._engineer_profile_restore = None
            self.engineer_chat_badge.hide()
        self.current_project_id = None
        self.current_task_id = None
        if specialization == "engineer":
            project_input = getattr(self, "engineer_project_input", None)
            raw_project_id = project_input.text().strip().lower() if project_input is not None else ""
            if raw_project_id:
                import re
                project_id = re.sub(r"[^a-z0-9_.:-]+", "-", raw_project_id).strip("-.:_")
                if not project_id or len(project_id) > 128:
                    QMessageBox.warning(self, "Projeto", "Use um ID de até 128 caracteres.")
                    return
                self.current_project_id = project_id
        self.page_heading.setText({"general": "Geral", "engineer": "Engenheiro", "professor": "Professor"}[specialization])
        if specialization == "general":
            self.current_conversation_id = "general-main"
        else:
            self.current_conversation_id = self.specialization_conversations.get(specialization, "")
        self._refresh_conversation_selector(self.current_conversation_id or None)
        self._load_active_conversation()
        self.page_stack.setCurrentWidget(self.chat_page)
        self.input.setFocus()

    def _set_active_area(self, active: str):
        for key, button in self.area_buttons.items():
            button.setObjectName("navButtonActive" if key == active else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)
