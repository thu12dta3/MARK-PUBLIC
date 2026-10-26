import json
import sqlite3
import time
from typing import Any

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .package_install import parse_install_request
from .memory import source_label
from .desktop_theme import message_html, safe_web_link
from .desktop_helpers import _mark_icon, _prepare_attachments, _request
from .cognition.providers import sanitize_chat_output
from .brain_prompt import SYSTEM, build_identity_system_prompt


from .ui.widgets import ErrorCardWidget, MessageBubbleWidget


class ChatMixin:
    def _resolve_model_and_provider(self) -> tuple[str, str]:
        """Extrai deterministicamente active_model_id e provider_name da interface ou estado salvo."""
        active_model_id = ""
        provider_name = ""
        if hasattr(self, "model_selector") and self.model_selector is not None:
            active_model_id = getattr(self.model_selector, "current_model_id", lambda: "")()
            provider_name = getattr(self.model_selector, "current_provider", lambda: "")()

        if not active_model_id:
            active_model_id = getattr(self, "active_model_id", "") or getattr(self, "selected_model", "")
        if not provider_name:
            provider_name = getattr(self, "active_provider", "") or getattr(self, "selected_provider", "")

        if not active_model_id and hasattr(self, "model_selector") and self.model_selector is not None:
            data = self.model_selector.currentData()
            if isinstance(data, str) and ":" in data:
                p, m = data.split(":", 1)
                provider_name = provider_name or p
                active_model_id = m

        return str(active_model_id), str(provider_name)

    def get_system_prompt(self) -> str:
        """Retorna o System Prompt dinâmico contendo active_model_id e regras de identidade."""
        active_model_id, provider_name = self._resolve_model_and_provider()
        return build_identity_system_prompt(active_model_id, provider_name) if active_model_id else SYSTEM

    def _prefill_message(self, text):
        if not self.busy:
            self.input.setPlainText(text)
            self.input.setFocus()

    def _append_bubble(self, text: str, role: str, created_at: int | None = None,
                       auto_scroll: bool = True, source: str = ""):
        if role == "assistant" and isinstance(text, str):
            text = sanitize_chat_output(text)
        if self.empty_state is not None:
            self.chat_layout.removeWidget(self.empty_state)
            self.empty_state.hide()
            self.empty_state.deleteLater()
            self.empty_state = None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        bubble = MessageBubbleWidget(
            text=text,
            role=role,
            source=source,
            link_handler=self._open_message_link,
        )
        if role == "user":
            row.addStretch(1)
        row.addWidget(bubble, 1 if role == "assistant" else 0)
        if role == "assistant":
            row.addStretch(1)
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent; border: none; margin: 0px; padding: 0px;")
        wrapper.setLayout(row)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrapper)
        if auto_scroll:
            self.follow_latest = True
            self._schedule_bottom_scroll()

    def _open_message_link(self, url):
        from . import desktop as desktop_module
        from urllib.parse import parse_qs, unquote, urlparse
        if str(url).startswith("mark-citation://"):
            try:
                parsed = urlparse(str(url))
                doc_id = parsed.netloc or parsed.path.lstrip("/")
                params = parse_qs(parsed.query)
                excerpt = unquote(params.get("excerpt", [""])[0])
                title = unquote(params.get("title", [""])[0])
                page = params.get("page", [None])[0]
                from .ui.widgets import CitationViewerDialog
                dlg = CitationViewerDialog(doc_id=doc_id, excerpt=excerpt, title=title, page=page, parent=self)
                dlg.exec()
                return
            except Exception:
                pass
        if safe_web_link(url):
            desktop_module.QDesktopServices.openUrl(QUrl(url))

    def _save_conversation_message(self, role, content, conversation_id=None):
        if role not in {"user", "assistant"} or not isinstance(content, str):
            return
        if role == "assistant":
            content = sanitize_chat_output(content)
        conversation_id = conversation_id or self.current_conversation_id
        now = int(time.time())
        db = self._conversation_db()
        try:
            db.execute("INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                       (conversation_id, role, content, now))
            if role == "user":
                title_row = db.execute("SELECT title FROM conversations WHERE id=?", (conversation_id,)).fetchone()
                if title_row and title_row[0] == "Nova conversa":
                    clean = " ".join(content.split())
                    title = clean[:42] + ("…" if len(clean) > 42 else "") if clean else "Nova conversa"
                    db.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                               (title, now, conversation_id))
                    if conversation_id == self.current_conversation_id:
                        self._refresh_conversation_selector(conversation_id)
            else:
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                           (now, conversation_id))
            db.commit()
        finally:
            db.close()

    def _conversation_request_messages(self, latest):
        self._ensure_conversation_store()
        db = self._conversation_db()
        try:
            try:
                rows = db.execute(
                    "SELECT role,content FROM conversation_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 30",
                    (self.current_conversation_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        finally:
            db.close()
        rows.reverse()
        messages = [{"role": role, "content": content} for role, content in rows]
        messages.append({"role": "user", "content": [{"type": "text", "text": latest}]})
        return messages[-31:]

    def send_message(self, text: str | None = None):
        if isinstance(text, str):
            message = text.strip()
            paths = []
        else:
            message = self.input.toPlainText().strip()
            paths = list(self.attachments)
            self.input.clear()

        if self.busy or (not message and not paths):
            return
        if paths and not message:
            message = "Analise os arquivos anexados e me ajude com eles."

        self._last_user_message = message
        self._last_user_paths = list(paths)
        visible_message = message
        if paths:
            visible_message += "\n\nArquivos: " + " · ".join(path.name for path in paths)
        conversation_id = self.current_conversation_id
        request_messages = self._conversation_request_messages(message)
        self._save_conversation_message("user", visible_message)
        self._append_bubble(visible_message, "user", source="desktop-chat")
        install_request = parse_install_request(message) if not paths else None
        if install_request:
            self.conversation_selector.setEnabled(True)
            self.new_conversation_button.setEnabled(True)
            self._begin_package_install(install_request)
            return

        # Pre-flight Target Disclosure & Verificação Prévia de Rota
        from .cognition.brain_routing import ProviderType, get_brain_router
        router = get_brain_router()
        preflight_ctx = {
            "specialization": self.current_specialization,
            "session_id": conversation_id,
        }
        if hasattr(self, "model_selector") and self.model_selector is not None:
            if self.model_selector.processing_type() == "Nuvem":
                preflight_ctx["selected_provider"] = self.model_selector.current_provider()
                preflight_ctx["selected_model"] = self.model_selector.current_model_id()
            else:
                preflight_ctx["selected_provider"] = "ollama"
                preflight_ctx["selected_model"] = self.model_selector.current_model_id()

        decision = router.resolve_target(message, context=preflight_ctx)

        if hasattr(self, "_update_routing_target_badge"):
            self._update_routing_target_badge(decision)

        if decision.is_sensitive and decision.provider_type == ProviderType.CLOUD_API:
            if hasattr(self, "confirm_cloud_egress"):
                self.confirm_cloud_egress(decision)
            self.connection_label.setText("Envio cancelado · payload sensível bloqueado de saída externa")
            return

        if decision.requires_user_confirmation:
            confirmed = False
            if hasattr(self, "confirm_cloud_egress"):
                confirmed = self.confirm_cloud_egress(decision)
            if not confirmed:
                self.connection_label.setText("Envio cancelado · restrição de privacidade mantida")
                return
            preflight_ctx["cloud_authorized"] = True

        self.busy = True
        self.send_button.setDisabled(True)
        self.attach_button.setDisabled(True)
        self.conversation_selector.setEnabled(False)
        self.new_conversation_button.setEnabled(False)
        self.remove_attachment_button.setDisabled(True)
        self.send_button.setIcon(QIcon())
        self.connection_label.setText("MARK está pensando…")
        if hasattr(self, "thinking_indicator") and self.thinking_indicator is not None:
            self.thinking_indicator.start()
        specialization = self.current_specialization
        context_payload = {
            "specialization": specialization,
            "session_id": conversation_id,
        }
        if self.current_project_id:
            context_payload["project_id"] = self.current_project_id
        if self.current_task_id:
            context_payload["task_id"] = self.current_task_id
        active_model_id, provider_name = self._resolve_model_and_provider()
        system_prompt = build_identity_system_prompt(active_model_id, provider_name) if active_model_id else SYSTEM

        profile = (
            self.model_selector.currentData()
            or self.model_selector.current_model_id()
            or "mark"
        )
        if not isinstance(profile, str) or not profile:
            profile = "mark"
        reasoning_level = self.reasoning_selector.currentData() if profile == "mark" else "instant"
        engineer_mode = specialization == "engineer"
        engineer_multimodal = engineer_mode and any(
            path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for path in paths
        )
        if engineer_mode:
            self.engineer_work_label.setText(
                "Imagem anexada · usando o perfil multimodal do MARK" if engineer_multimodal
                else "Preparando contexto e consultando o modelo Engineer…"
            )
            self.engineer_chat_badge.setText(
                "ENGINEER · VISÃO MULTIMODAL" if engineer_multimodal else "ENGINEER · PERFIL DE CÓDIGO"
            )
            self.engineer_work_feedback.setVisible(True)

        def ask(task_instance=None):
            from . import desktop as desktop_module
            from .cognition.agent_loop import run_agentic_loop, AgentState
            prepared = _prepare_attachments(paths)
            file_context = []
            for item in prepared:
                if "data" in item:
                    data_url = f"data:{item['mime']};base64,{item['data']}"
                else:
                    file_context.append({"name": item["name"], "text": item["text"]})

            def model_caller(msgs: list[dict[str, Any]]) -> str:
                req_msgs = list(msgs)
                identity_directive = build_identity_system_prompt(active_model_id, provider_name, base_system="") if active_model_id else ""
                if req_msgs and req_msgs[0].get("role") == "system":
                    cur_sys = str(req_msgs[0]["content"])
                    if identity_directive not in cur_sys:
                        req_msgs[0] = {"role": "system", "content": f"{identity_directive}\n\n{cur_sys}"}
                    sys_content = req_msgs[0]["content"]
                else:
                    req_msgs.insert(0, {"role": "system", "content": system_prompt})
                    sys_content = system_prompt

                if req_msgs and isinstance(req_msgs[-1]["content"], str):
                    last_content = [{"type": "text", "text": req_msgs[-1]["content"]}]
                    for item in prepared:
                        if "data" in item:
                            data_url = f"data:{item['mime']};base64,{item['data']}"
                            last_content.append({"type": "image_url", "image_url": {"url": data_url}})
                    req_msgs[-1] = {"role": req_msgs[-1]["role"], "content": last_content}

                payload = {
                    "model": profile,
                    "stream": False,
                    "mark_reasoning_level": reasoning_level,
                    "messages": req_msgs,
                    "system": sys_content,
                    "system_prompt": sys_content,
                    "active_model_id": active_model_id,
                    "provider_name": provider_name,
                    "cloud_authorized": preflight_ctx.get("cloud_authorized", False),
                    "mark_attachments": file_context,
                    "mark_context": context_payload,
                }
                _, body = desktop_module._request("/v1/chat/completions", payload)
                result = json.loads(body)
                return result["choices"][0]["message"]["content"]

            if not engineer_mode:
                content = [{"type": "text", "text": message}]
                for item in prepared:
                    if "data" in item:
                        data_url = f"data:{item['mime']};base64,{item['data']}"
                        content.append({"type": "image_url", "image_url": {"url": data_url}})
                        content.append({"type": "text", "text": f"[Imagem anexada: {item['name']}]"})
                request_messages[-1]["content"] = content

                payload_messages = []
                if request_messages and request_messages[0].get("role") == "system":
                    payload_messages = [{"role": "system", "content": system_prompt}] + list(request_messages[1:])
                else:
                    payload_messages = [{"role": "system", "content": system_prompt}] + list(request_messages)

                payload = {
                    "model": profile,
                    "stream": False,
                    "mark_reasoning_level": reasoning_level,
                    "messages": payload_messages,
                    "system": system_prompt,
                    "system_prompt": system_prompt,
                    "active_model_id": active_model_id,
                    "provider_name": provider_name,
                    "cloud_authorized": preflight_ctx.get("cloud_authorized", False),
                    "mark_attachments": file_context,
                    "mark_context": context_payload,
                }
                _, body = desktop_module._request("/v1/chat/completions", payload)
                result = json.loads(body)
                return result["choices"][0]["message"]["content"]

            cancel_sig = task_instance.cancel_signal if task_instance is not None else None

            def on_state(state: AgentState, action_label: str, tool_name: str, details: str):
                if task_instance is not None:
                    task_instance.notify_state(state.value, tool_name, action_label, details)

            def on_consent(tool_name: str, args: dict[str, Any]) -> bool:
                if hasattr(self, "consent_bridge") and self.consent_bridge is not None:
                    return self.consent_bridge.ask_consent(tool_name, args)
                return False

            res = run_agentic_loop(
                message,
                mode=specialization,
                model_caller=model_caller,
                cancel_signal=cancel_sig,
                consent_callback=on_consent,
                state_callback=on_state,
                initial_history=request_messages[:-1],
                system_prompt=system_prompt,
                max_steps=3,
            )
            return res.output

        def done(reply):
            self.busy = False
            self.engineer_work_feedback.hide()
            if hasattr(self, "thinking_indicator") and self.thinking_indicator is not None:
                self.thinking_indicator.stop()
            if hasattr(self, "gods_eye_widget") and self.gods_eye_widget is not None:
                self.gods_eye_widget.reset()
            if engineer_mode:
                self.engineer_chat_badge.setText("ENGINEER · PERFIL DE CÓDIGO")
            self.send_button.setDisabled(False)
            self.attach_button.setDisabled(False)
            self.conversation_selector.setEnabled(True)
            self.new_conversation_button.setEnabled(True)
            self.send_button.setText("")
            self.send_button.setIcon(_mark_icon("send"))
            self.attachments.clear()
            self._refresh_attachment_list()
            self._save_conversation_message("assistant", reply, conversation_id)
            if self.current_conversation_id == conversation_id:
                self._append_bubble(reply, "assistant")
            self.last_assistant_reply = reply
            self.connection_label.setText("Conectado ao MARK · memória compartilhada")
            self.input.setFocus()

        def failed(message):
            self.busy = False
            self.engineer_work_feedback.hide()
            if hasattr(self, "thinking_indicator") and self.thinking_indicator is not None:
                self.thinking_indicator.stop()
            if hasattr(self, "gods_eye_widget") and self.gods_eye_widget is not None:
                self.gods_eye_widget.reset()
            if engineer_mode:
                self.engineer_chat_badge.setText("ENGINEER · PERFIL DE CÓDIGO")
            self.send_button.setDisabled(False)
            self.attach_button.setDisabled(False)
            self.conversation_selector.setEnabled(True)
            self.new_conversation_button.setEnabled(True)
            self.remove_attachment_button.setEnabled(self.attachment_list.currentRow() >= 0)
            self.send_button.setText("")
            self.send_button.setIcon(_mark_icon("send"))
            clean_err = str(message)

            title, display_msg = self._simplify_error_details(clean_err)
            self._append_error_card(error_message=display_msg, title=title)
            self.connection_label.setText("MARK · Falha temporária no processamento")
            self.refresh_status()

        state_slot = (
            self.gods_eye_widget.update_state
            if hasattr(self, "gods_eye_widget") and self.gods_eye_widget is not None
            else None
        )
        worker = self._task(ask, done, failed, on_state_changed=state_slot)
        if worker is not None:
            self.current_chat_task = worker

    def _simplify_error_details(self, clean_err: str) -> tuple[str, str]:
        lower = clean_err.lower()
        if "contextwindowexceeded" in lower or "limite de contexto" in lower or "context window" in lower:
            return (
                "Limite de Contexto Atingido",
                "A conversa ou anexo excede a capacidade máxima do modelo. O MARK aplicou compressão automática no histórico anterior, mas a solicitação atual continua ultrapassando a janela de contexto. Experimente enviar uma mensagem mais curta ou alternar para um modelo de contexto estendido.",
            )
        elif "timeout" in lower or "timed out" in lower or "tempo limite" in lower:
            return (
                "Tempo Limite de Resposta Excedido",
                "O motor de inferência (Ollama ou API de nuvem) atingiu o tempo limite sem responder. Verifique se o serviço está sobrecarregado ou tente novamente.",
            )
        elif "localinferenceunavailable" in lower or ("ollama" in lower and ("indisponível" in lower or "inoperante" in lower or "unavailable" in lower)):
            return (
                "Inferência Local Indisponível",
                "O serviço Ollama não está em execução na máquina local (127.0.0.1:11434) e a saída para a nuvem não foi autorizada. Inicie o serviço com 'systemctl start ollama' ou 'ollama serve' no terminal, ou alterne o provedor.",
            )
        elif "emptyresponse" in lower or "resposta vazia" in lower or "empty_final" in lower:
            return (
                "Resposta Vazia do Modelo",
                "O modelo de linguagem encerrou a geração sem produzir conteúdo visível após tentativas automáticas de re-prompt. Tente reenviar ou reformular sua instrução.",
            )
        elif "http 429" in lower or "limite de taxa" in lower or "ratelimit" in lower:
            return (
                "Limite de Requisições Atingido (HTTP 429)",
                "O limite de taxa de requisições por minuto do provedor configurado foi excedido. Aguarde alguns instantes antes de enviar uma nova consulta.",
            )
        elif "privacypolicyviolation" in lower or "sensíveis" in lower:
            return (
                "Bloqueio por Política de Privacidade",
                f"A solicitação foi bloqueada para proteger sua privacidade: {clean_err}",
            )
        elif "connectionrefused" in lower or "falha de conexão" in lower:
            return (
                "Falha de Conexão com o Provedor",
                "Não foi possível conectar ao motor de inferência. O Circuit Breaker gerenciará a recuperação automática.",
            )
        else:
            return (
                "Falha no Processamento",
                f"Não foi possível concluir a resposta: {clean_err}",
            )

    def _append_error_card(self, error_message: str, title: str = "Falha no Processamento", retry_callback=None, switch_model_callback=None, auto_scroll: bool = True):
        if self.empty_state is not None:
            self.chat_layout.removeWidget(self.empty_state)
            self.empty_state.hide()
            self.empty_state.deleteLater()
            self.empty_state = None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        card = ErrorCardWidget(
            error_text=error_message,
            title=title,
            retry_callback=retry_callback or self._retry_last_message,
            switch_model_callback=switch_model_callback or self._switch_model_action,
        )
        row.addWidget(card, 0)
        row.addStretch(1)
        wrapper = QWidget()
        wrapper.setLayout(row)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrapper)
        if auto_scroll:
            self.follow_latest = True
            self._schedule_bottom_scroll()

    def _retry_last_message(self):
        last_msg = getattr(self, "_last_user_message", None)
        if last_msg:
            self.send_message(last_msg)

    def _switch_model_action(self):
        if hasattr(self, "model_selector") and self.model_selector is not None:
            count = self.model_selector.count()
            if count > 1:
                next_idx = (self.model_selector.currentIndex() + 1) % count
                self.model_selector.setCurrentIndex(next_idx)
                selected_text = self.model_selector.currentText()
                self.connection_label.setText(f"Modelo alternado para: {selected_text}")
            else:
                self.connection_label.setText("Nenhum outro modelo configurado no momento.")

