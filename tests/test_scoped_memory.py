"""Suíte de Testes para Memória Particionada, Portátil e Deduplicada (Fase 3)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from mark.core.context import CoreContext
from mark.memory import (
    DeduplicationEngine,
    MemoryConflict,
    MemoryScope,
    SharedMemory,
    create_portable_payload,
    parse_portable_payload,
)


class TestScopedMemoryPartitioning(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="mark-test-scoped-mem-")
        self.tmp_path = Path(self.tmp_dir.name)
        self.db_path = self.tmp_path / "test_memory.sqlite3"
        self.mem = SharedMemory(path=self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_scope_isolation_prevents_state_leakage(self):
        """Valida isolamento estrito entre escopos Global, Especialização, Projeto e Tarefa."""
        ctx_proj_a = CoreContext.create(project_id="projeto_alpha", specialization="engineer")
        ctx_proj_b = CoreContext.create(project_id="projeto_beta", specialization="engineer")
        ctx_prof = CoreContext.create(specialization="professor")

        # Salva em escopos distintos
        self.mem.remember_fact("O usuário prefere respostas em português brasileiro.", scope="global")
        self.mem.remember_fact("Configuração de compilação: rustc flag -O3.", scope="project", context=ctx_proj_a)
        self.mem.remember_fact("Configuração de compilação: gcc flag -Wall.", scope="project", context=ctx_proj_b)
        self.mem.remember_fact("Metodologia de ensino: usar analogias práticas.", scope="specialization", context=ctx_prof)

        # Consulta sob contexto do Projeto Alpha
        recalled_a = self.mem.recall_facts("compilação flag", context=ctx_proj_a)
        contents_a = [r["content"] for r in recalled_a]
        self.assertTrue(any("rustc flag -O3" in c for c in contents_a))
        self.assertFalse(any("gcc flag -Wall" in c for c in contents_a), "Vazamento: Projeto Alpha leu dados do Projeto Beta!")

        # Consulta sob contexto do Projeto Beta
        recalled_b = self.mem.recall_facts("compilação flag", context=ctx_proj_b)
        contents_b = [r["content"] for r in recalled_b]
        self.assertTrue(any("gcc flag -Wall" in c for c in contents_b))
        self.assertFalse(any("rustc flag -O3" in c for c in contents_b), "Vazamento: Projeto Beta leu dados do Projeto Alpha!")

        # Consulta sob modo Professor
        recalled_prof = self.mem.recall_facts("ensino metodologia", context=ctx_prof)
        self.assertTrue(any("analogias práticas" in r["content"] for r in recalled_prof))

        # Fato global deve ser visível para todos os contextos
        recalled_global = self.mem.recall_facts("português brasileiro", context=ctx_proj_a)
        self.assertTrue(any("português brasileiro" in r["content"] for r in recalled_global))

    def test_deduplication_engine_detects_conflicts_without_silent_deletion(self):
        """Valida que o motor de deduplicação detecta divergências e NUNCA apaga por baixo dos panos."""
        # 1. Fato inicial
        res1 = self.mem.remember_fact("O servidor web escuta na porta 8080.", scope="global")
        self.assertTrue(res1["saved"])
        facts = self.mem.list_facts(scope="global")
        self.assertEqual(len(facts), 1)

        # 2. Inserção de duplicata exata (não deve duplicar)
        res2 = self.mem.remember_fact("O servidor web escuta na porta 8080.", scope="global")
        self.assertFalse(res2["saved"], "Duplicata exata não pode criar nova entrada")

        # 3. Inserção de fato conflitante no mesmo escopo
        res3 = self.mem.remember_fact("O servidor web escuta na porta 9090.", scope="global")
        self.assertTrue(res3["saved"])
        self.assertIn("conflicts", res3)
        self.assertGreaterEqual(len(res3["conflicts"]), 1)

        # Verifica que o conflito foi registrado como PENDING
        conflicts = self.mem.list_conflicts(status="PENDING")
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertIn("8080", conflict["existing_content"])
        self.assertIn("9090", conflict["new_content"])

        # O conteúdo antigo NÃO foi apagado silenciosamente
        facts_after = self.mem.list_facts(scope="global")
        contents = [f["content"] for f in facts_after]
        self.assertIn("O servidor web escuta na porta 8080.", contents)

        # 4. Resolução explícita pelo usuário
        cid = conflict["conflict_id"]
        resolved = self.mem.resolve_conflict(cid, choice="use_new")
        self.assertTrue(resolved)

        # Agora a memória foi atualizada para 9090
        facts_resolved = self.mem.list_facts(scope="global")
        contents_resolved = [f["content"] for f in facts_resolved]
        self.assertTrue(any("9090" in c for c in contents_resolved))
        self.assertEqual(len(self.mem.list_conflicts(status="PENDING")), 0)

    def test_portable_memory_export_encrypted_vault_and_restore(self):
        """Valida exportação e importação criptografadas via Cofre com Argon2id + AES-256-GCM."""
        self.mem.remember_fact("Chave de autenticação mútua TLS configurada.", scope="global")
        self.mem.remember_fact("Diretório de cache local: /var/cache/mark.", scope="global")

        export_file = self.tmp_path / "memoria_export.enc"
        master_pass = "senha_mestra_ultra_secreta_2026"

        # Exporta cifrado
        count = self.mem.export_facts(export_file, passphrase=master_pass, encrypted=True)
        self.assertEqual(count, 2)
        self.assertTrue(export_file.exists())

        # Valida que o arquivo exportado está protegido por 0600
        mode = export_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

        # Cria novo banco para simular migração
        target_db = self.tmp_path / "migrated_memory.sqlite3"
        target_mem = SharedMemory(path=target_db)

        # 1. Tentar restaurar com senha errada deve falhar
        with self.assertRaises((ValueError, RuntimeError, PermissionError)):
            target_mem.restore_facts(export_file, passphrase="senha_incorreta")

        # 2. Restaurar com senha correta
        result = target_mem.restore_facts(export_file, passphrase=master_pass)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["duplicates"], 0)

        migrated_facts = target_mem.list_facts(scope="global")
        self.assertEqual(len(migrated_facts), 2)
        migrated_contents = [f["content"] for f in migrated_facts]
        self.assertIn("Chave de autenticação mútua TLS configurada.", migrated_contents)

    def test_import_detects_conflicts_and_flags_them(self):
        """Valida que importar backup com fatos conflitantes não destrói o banco existente."""
        self.mem.remember_fact("O endereço do broker MQTT é 192.168.1.100.", scope="global")

        # Cria payload portátil contendo um valor diferente para o broker
        competing_data = [
            {
                "scope": "global",
                "scope_id": "",
                "content": "O endereço do broker MQTT é 192.168.1.250.",
                "provenance": "migrated_backup",
            }
        ]
        payload_bytes = create_portable_payload(competing_data, encrypted=False)

        import_res = self.mem.restore_facts(payload_bytes)
        self.assertIn("conflicts_flagged", import_res)
        self.assertGreaterEqual(import_res["conflicts_flagged"], 1)

        conflicts = self.mem.list_conflicts(status="PENDING")
        self.assertGreaterEqual(len(conflicts), 1)
        self.assertIn("192.168.1.100", conflicts[0]["existing_content"])
        self.assertIn("192.168.1.250", conflicts[0]["new_content"])


if __name__ == "__main__":
    unittest.main()
