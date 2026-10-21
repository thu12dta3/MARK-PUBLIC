import sqlite3

from PyQt6.QtWidgets import QMessageBox

from .cognition.professor import MODES as STUDY_MODES, StudyPlanStore, build_study_prompt


class ProfessorMixin:
    def _study_store(self):
        return StudyPlanStore()

    def prepare_study(self):
        try:
            subject=self.professor_subject.text()
            mode=self.professor_mode.currentData()
            level=self.professor_level.currentData()
            objective=self.professor_objective.text()
            source=self.professor_source.text()
            prompt=build_study_prompt(subject,mode,level,objective=objective,source=source)
            with self._study_store() as store:
                plan=store.start(subject,objective,source,level,mode)
        except (ValueError,OSError,sqlite3.Error) as exc:
            QMessageBox.warning(self,"Professor",str(exc) if isinstance(exc,ValueError) else "Não consegui salvar o plano local; nada foi apagado.")
            return
        self.professor_plan_id=plan.id
        self.professor_status.setText(f"Plano salvo · {plan.subject} · {plan.status.lower()}")
        self.open_specialized_chat("professor")
        self._prefill_message(prompt)

    def resume_study_plan(self):
        try:
            with self._study_store() as store:
                plans=store.list()
                plan=next((item for item in plans if item.status=="ACTIVE"),None)
        except (OSError,sqlite3.Error,ValueError):
            QMessageBox.warning(self,"Professor","Não consegui ler o progresso local.");return
        if plan is None:
            QMessageBox.information(self,"Professor","Não há plano ativo para retomar.");return
        self.professor_plan_id=plan.id
        self.professor_subject.setText(plan.subject);self.professor_objective.setText(plan.objective)
        self.professor_source.setText(plan.source);self.professor_level.setCurrentIndex(self.professor_level.findData(plan.level))
        self.professor_mode.setCurrentIndex(self.professor_mode.findData(plan.mode))
        prompt=build_study_prompt(plan.subject,plan.mode,plan.level,objective=plan.objective,source=plan.source)
        self.professor_status.setText(f"Plano retomado · {plan.subject}")
        self.open_specialized_chat("professor");self._prefill_message(prompt)

    def view_study_progress(self):
        try:
            with self._study_store() as store:plans=store.list()
        except (OSError,sqlite3.Error,ValueError):
            QMessageBox.warning(self,"Progresso","Não consegui ler o progresso local.");return
        if not plans:
            QMessageBox.information(self,"Progresso","Ainda não há planos de estudo salvos.");return
        lines=[]
        for plan in plans:
            line=f"{plan.subject} · {plan.status.lower()} · nível {plan.level}"
            line+=f" · modo {next((item.label for item in STUDY_MODES if item.key==plan.mode),plan.mode)}"
            if plan.objective:line+=f"\nObjetivo: {plan.objective}"
            if plan.source:line+=f"\nFonte: {plan.source}"
            lines.append(line)
        QMessageBox.information(self,"Progresso de estudo","\n\n".join(lines))

    def complete_study_plan(self):
        plan_id=getattr(self,"professor_plan_id",None)
        if plan_id is None:
            QMessageBox.information(self,"Professor","Prepare um plano nesta sessão para marcá-lo como concluído.");return
        try:
            with self._study_store() as store:completed=store.complete(plan_id)
        except (OSError,sqlite3.Error,ValueError):
            QMessageBox.warning(self,"Professor","Não consegui atualizar o progresso local.");return
        if completed:self.professor_status.setText("Plano concluído; histórico preservado localmente.")
        else:self.professor_status.setText("Esse plano já estava concluído ou foi apagado.")

    def reset_study_progress(self):
        if QMessageBox.question(self,"Apagar progresso","Apagar somente planos de estudo do MARK? Conversas, Biblioteca e arquivos não serão alterados.")!=QMessageBox.StandardButton.Yes:return
        try:
            with self._study_store() as store:store.reset()
        except (OSError,sqlite3.Error,ValueError):
            QMessageBox.warning(self,"Professor","Não consegui apagar o progresso local.");return
        self.professor_plan_id=None;self.professor_status.setText("Progresso de estudo apagado; conversa e Biblioteca preservadas.")
