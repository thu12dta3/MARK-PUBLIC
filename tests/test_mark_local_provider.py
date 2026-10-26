"""Suíte de Testes para o MarkLocalProvider (Mark 0.1 IQ4_NL Engine & Ecossistema MARK).

Valida:
1. Registro oficial do provedor ("mark-local", "mark_ai") em PROVIDERS e create_llm_provider.
2. Política segura de escaneamento de checkpoints.
3. Roteamento nativo e ativação de especialistas para os 10 domínios oficiais.
4. Integração com o EngineerAgent (código limpo, ausência de emojis e blocos markdown).
5. Formatador acústico de voz para ElevenLabs e Fish Speech.
6. Janela de contexto de 32k tokens via DeepSeek MLA.
7. Compatibilidade com streaming SSE e geração unificada ModelResponse.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mark.cognition.domains import Domain, route_domains
from mark.cognition.mark_local_provider import (
    DEFAULT_CHECKPOINTS_DIR,
    MARK_LOCAL_CONTEXT_WINDOW,
    MARK_LOCAL_VRAM_CEILING_GB,
    SATA_SSD_CHECKPOINTS_DIR,
    MarkLocalProvider,
    MarkVoiceAcousticFormatter,
)
from mark.cognition.provider_adapter import (
    ContextWindowExceededError,
    LLMRequest,
    create_llm_provider,
)
from mark.cognition.providers import (
    PROVIDERS,
    ModelRequest,
    ModelResponse,
    get_provider,
)
from mark.config.llm_config import get_llm_config


class TestMarkLocalProvider(unittest.TestCase):
    """Testes unitários para o MarkLocalProvider."""

    def setUp(self):
        self.provider = MarkLocalProvider()

    def test_provider_registration_and_aliases(self):
        """Verifica se mark-local e mark_ai estão registrados em todas as fábricas do MARK."""
        p1 = create_llm_provider("mark-local")
        self.assertIsInstance(p1, MarkLocalProvider)
        self.assertEqual(p1.provider_name, "mark-local")

        p2 = create_llm_provider("mark_ai")
        self.assertIsInstance(p2, MarkLocalProvider)

        # Registro no dicionário PROVIDERS de providers.py
        self.assertIn("mark-local", PROVIDERS)
        self.assertIn("mark_ai", PROVIDERS)

        cfg1 = {"cognition": {"model_provider": "mark-local"}}
        prov_from_cfg1 = get_provider(cfg1)
        self.assertIsInstance(prov_from_cfg1, MarkLocalProvider)

        cfg2 = {"cognition": {"model_provider": "mark_ai"}}
        prov_from_cfg2 = get_provider(cfg2)
        self.assertIsInstance(prov_from_cfg2, MarkLocalProvider)

    def test_checkpoints_dir_policy(self):
        """Garante que o diretório padrão de checkpoints é respeitado e seguro."""
        self.assertEqual(self.provider.checkpoints_dir, DEFAULT_CHECKPOINTS_DIR)

        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_provider = MarkLocalProvider(checkpoints_dir=tmp_dir)
            # Diretório vazio sem arquivos .pt deve retornar None
            self.assertIsNone(custom_provider.get_latest_checkpoint())

            # Criando um checkpoint fictício para validar descoberta
            ckpt_path = Path(tmp_dir) / "mark_0_1_step_100.pt"
            ckpt_path.write_text("test_ckpt")
            self.assertEqual(custom_provider.get_latest_checkpoint(), ckpt_path)

    def test_context_window_32k(self):
        """Verifica a janela nativa de 32k tokens do DeepSeek MLA."""
        self.assertEqual(self.provider.get_context_window(), 32_768)

        # Requisição gigante que ultrapassa 32k tokens deve levantar ContextWindowExceededError
        huge_text = "palavra " * 35_000
        req = LLMRequest(prompt=huge_text)
        with self.assertRaises(ContextWindowExceededError):
            self.provider.check_context_window(req)

    def test_10_domains_steering(self):
        """Verifica se perguntas nos 10 domínios oficiais ativam a rota correta."""
        cases = [
            ("Qual o impacto do Linux kernel na rede?", Domain.COMPUTING),
            ("Explique o transistor mosfet no circuito", Domain.ELECTRONICS),
            ("Como funciona a ECU do motor automotivo?", Domain.AUTOMOTIVE),
            ("Calcule a massa molar de H2SO4", Domain.CHEMISTRY),
            ("Qual a jurisdição do artigo da lei?", Domain.LAW),
            ("Relate os fatos da revolução no império", Domain.HISTORY),
            ("A doutrina militar na primeira guerra mundial", Domain.MILITARY_HISTORY),
            ("Detalhes técnicos sobre este fuzil e armamento", Domain.WEAPONS_KNOWLEDGE),
            ("Blindagem e especificações deste blindado tank", Domain.AFV_ARMOR),
            ("Cuidados de saúde e acompanhamento médico", Domain.HEALTH),
        ]

        for text, expected_domain in cases:
            decision, steered = self.provider._handle_domain_steering(text, system="Sistema")
            self.assertIn(expected_domain, decision.domains)
            self.assertIn(expected_domain.value, steered)

    def test_voice_formatting_elevenlabs_and_fish_speech(self):
        """Testa a extração de áudio acústico limpo removendo <think>, código e referências."""
        raw_text = (
            "<think>\nRaciocinando sobre o motor elétrico.\n</think>\n"
            "O motor trifásico possui alta eficiência [1].\n"
            "```python\nprint('ignorar na voz')\n```\n"
            "Para mais detalhes consulte (ref.: DOC123)."
        )

        eleven_clean = MarkVoiceAcousticFormatter.extract_clean_voice_text(raw_text, engine="elevenlabs")
        self.assertNotIn("<think>", eleven_clean)
        self.assertNotIn("```", eleven_clean)
        self.assertNotIn("[1]", eleven_clean)
        self.assertNotIn("(ref.:", eleven_clean)
        self.assertIn("O motor trifásico possui alta eficiência", eleven_clean)

        fish_clean = MarkVoiceAcousticFormatter.extract_clean_voice_text(raw_text, engine="fish_speech")
        self.assertNotIn("<think>", fish_clean)
        self.assertIn("O motor trifásico possui alta eficiência", fish_clean)

    def test_engineer_agent_code_generation(self):
        """Valida que prompts do EngineerAgent retornam código Python em bloco markdown válido."""
        prompt = (
            "TAREFA DE ENGENHARIA (Tentativa 1/3 | Rota: LOCAL_EXECUTION):\n"
            "Corrija o bug no módulo.\n\n"
            "ARQUIVO ALVO: src/mark/test_sample.py\n"
            "CÓDIGO ATUAL:\n```python\ndef buggy(): pass\n```\n"
        )
        req = LLMRequest(prompt=prompt)
        resp = self.provider.completion(req)

        self.assertTrue(resp.content)
        self.assertIn("```python", resp.content)
        self.assertIn("def execute_task()", resp.content)
        self.assertNotIn("\U0001F600", resp.content)  # Zero emojis

    def test_model_provider_generate_bridge(self):
        """Valida a compatibilidade direta com ModelRequest e ModelResponse do MARK."""
        req = ModelRequest(prompt="Como testar o Mark 0.1?")
        res = self.provider.generate(req)
        self.assertIsInstance(res, ModelResponse)
        self.assertTrue(res.text)
        self.assertGreater(res.metrics.prompt_tokens, 0)
        self.assertGreater(res.metrics.output_tokens, 0)
        self.assertTrue(res.metrics.success)

    def test_streaming_sse_chunks(self):
        """Valida a geração em fluxo contínuo de tokens via streaming."""
        req = LLMRequest(prompt="Gere uma resposta curta.")
        chunks = list(self.provider.stream(req))
        self.assertGreater(len(chunks), 0)
        self.assertTrue(any(c.is_final for c in chunks))
        reconstructed = "".join(c.delta_text for c in chunks)
        self.assertTrue(reconstructed.strip())

    def test_describe_metadata(self):
        """Valida metadados técnicos retornados pelo método describe()."""
        meta = self.provider.describe()
        self.assertEqual(meta["provider"], "mark-local")
        self.assertEqual(meta["architecture"], "DeepSeek-MLA-MoE-MTP-Mark-0.1")
        self.assertEqual(meta["quantization"], "IQ4_NL")
        self.assertEqual(meta["vram_ceiling_gb"], MARK_LOCAL_VRAM_CEILING_GB)
        self.assertEqual(meta["context_window"], MARK_LOCAL_CONTEXT_WINDOW)
        self.assertEqual(len(meta["domains"]), 10)


if __name__ == "__main__":
    unittest.main()
