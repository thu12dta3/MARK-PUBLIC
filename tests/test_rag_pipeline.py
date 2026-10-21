"""Suíte de Testes do Pipeline RAG Híbrido, Reranking e Citações Verificáveis (Fase 3)."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from mark.knowledge import (
    CitationVerifier,
    DocumentRecord,
    HybridRetriever,
    KnowledgeIndexer,
    PassageChunk,
    RAGPipeline,
    Reranker,
    extract_citations,
    format_clickable_citations,
    verify_citations,
)


class TestRAGPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="mark-test-rag-")
        self.tmp_path = Path(self.tmp_dir.name)
        self.db_path = self.tmp_path / "test_rag.sqlite3"
        self.pipeline = RAGPipeline(db_path=self.db_path)

        # Prepara documentos de teste
        self.doc_ai = self.tmp_path / "deep_learning.md"
        self.doc_ai.write_text(
            "# Fundamentos de Deep Learning\n"
            "O algoritmo de retropropagação (backpropagation) calcula gradientes analíticos "
            "usando a regra da cadeia para ajustar pesos sinápticos.\n\n"
            "## Otimizadores\n"
            "O Adam combina os benefícios do AdaGrad e do RMSProp, mantendo taxas de aprendizado "
            "adaptativas por parâmetro com momentos de primeira e segunda ordem.\n",
            encoding="utf-8",
        )

        self.doc_law = self.tmp_path / "constituicao.txt"
        self.doc_law.write_text(
            "Artigo 5 da Constituicao da Republica:\n"
            "Todos sao iguais perante a lei, sem distincao de qualquer natureza, garantindo-se "
            "aos brasileiros e aos estrangeiros residentes no Pais a inviolabilidade do direito a vida, "
            "a liberdade, a igualdade, a seguranca e a propriedade.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.pipeline.indexer.close()
        self.tmp_dir.cleanup()

    def test_ingest_and_index_documents(self):
        """Valida ingestão de arquivos MD e TXT com geração de chunks, FTS5 e metadados."""
        rec_ai = self.pipeline.indexer.ingest_file(self.doc_ai, title="Manual de Redes Neurais")
        rec_law = self.pipeline.indexer.ingest_file(self.doc_law, title="Constituição da República")

        self.assertIsNotNone(rec_ai.doc_id)
        self.assertEqual(rec_ai.title, "Manual de Redes Neurais")
        self.assertEqual(rec_ai.format, "md")

        stats = self.pipeline.indexer.stats()
        self.assertEqual(stats["sources"], 2)
        self.assertGreaterEqual(stats["chunks"], 2)

    def test_hybrid_retrieval_combines_lexical_and_semantic(self):
        """Valida que o recuperador híbrido une correspondência FTS5 com similaridade de embeddings."""
        self.pipeline.indexer.ingest_file(self.doc_ai, title="Manual de Redes Neurais")
        self.pipeline.indexer.ingest_file(self.doc_law, title="Constituição da República")

        # Pergunta com termos específicos do documento de IA
        hits = self.pipeline.retriever.retrieve("como funciona o otimizador Adam e momentos?", limit=5)
        self.assertGreaterEqual(len(hits), 1)
        top_hit = hits[0]
        self.assertEqual(top_hit.document.title, "Manual de Redes Neurais")
        self.assertIn("Adam", top_hit.chunk.text)
        self.assertGreater(top_hit.combined_score, 0.0)

        # Pergunta com termos jurídicos
        hits_law = self.pipeline.retriever.retrieve("garantia do direito a vida e liberdade de acordo com a lei", limit=5)
        self.assertGreaterEqual(len(hits_law), 1)
        self.assertEqual(hits_law[0].document.title, "Constituição da República")

    def test_reranker_prioritizes_relevance_and_filters_redundancy(self):
        """Valida que o Reranker prioriza trechos com maior densidade lexical e diversidade."""
        rec_ai = self.pipeline.indexer.ingest_file(self.doc_ai, title="Manual de Redes Neurais")

        # Ingesta um documento duplicado ou quase idêntico
        doc_dup = self.tmp_path / "deep_learning_dup.md"
        doc_dup.write_text(
            "# Fundamentos de Deep Learning\n"
            "O algoritmo de retropropagação (backpropagation) calcula gradientes analíticos "
            "usando a regra da cadeia para ajustar pesos sinápticos.\n",
            encoding="utf-8",
        )
        self.pipeline.indexer.ingest_file(doc_dup, title="Manual Copia")

        candidates = self.pipeline.retriever.retrieve("retropropagação backpropagation", limit=10)
        reranked = self.pipeline.reranker.rerank("retropropagação backpropagation", candidates, top_k=2)

        self.assertLessEqual(len(reranked), 2)
        self.assertGreater(reranked[0].rerank_score, 0.0)

    def test_citation_verification_strict(self):
        """Valida a verificação estrita de citações [DocID:Trecho] prevenindo alucinações."""
        rec_ai = self.pipeline.indexer.ingest_file(self.doc_ai, title="Manual de Redes Neurais")
        ctx = self.pipeline.retrieve_and_format("Como o backpropagation calcula gradientes?", top_k=2)

        doc_id = rec_ai.doc_id

        # 1. Resposta com citação perfeitamente ancorada
        valid_answer = (
            f"O modelo ajusta os pesos através da regra da cadeia "
            f"[{doc_id}:calcula gradientes analíticos usando a regra da cadeia]."
        )
        report_valid = self.pipeline.verify_response(valid_answer, ctx)
        self.assertEqual(report_valid.total_citations, 1)
        self.assertEqual(report_valid.verified_count, 1)
        self.assertTrue(report_valid.is_strictly_anchored)

        # 2. Resposta com citação inventada (DocID real mas trecho não existe)
        unanchored_answer = (
            f"O algoritmo consome 100 terawatts de energia na GPU [{doc_id}:consome 100 terawatts de energia]."
        )
        report_unanchored = self.pipeline.verify_response(unanchored_answer, ctx)
        self.assertEqual(report_unanchored.unanchored_count, 1)
        self.assertFalse(report_unanchored.is_strictly_anchored)

        # 3. Resposta com DocID alucinado que não existe
        hallucinated_answer = (
            "Segundo dados da NASA [doc_fantasma_99:foguetes lunares utilizam helio]."
        )
        report_hallucinated = self.pipeline.verify_response(hallucinated_answer, ctx)
        self.assertEqual(report_hallucinated.hallucinated_count, 1)
        self.assertFalse(report_hallucinated.is_strictly_anchored)

    def test_document_removal_cleans_indexes(self):
        """Valida que remove_document purga fontes, chunks e FTS5 de forma consistente."""
        rec_ai = self.pipeline.indexer.ingest_file(self.doc_ai, title="Manual de Redes Neurais")
        doc_id = rec_ai.doc_id

        # Verifica presença
        self.assertIsNotNone(self.pipeline.indexer.get_document(doc_id))
        hits_before = self.pipeline.retriever.retrieve("backpropagation", limit=5)
        self.assertGreaterEqual(len(hits_before), 1)

        # Remove documento
        removed = self.pipeline.indexer.remove_document(doc_id)
        self.assertTrue(removed)

        # Verifica ausência total
        self.assertIsNone(self.pipeline.indexer.get_document(doc_id))
        hits_after = self.pipeline.retriever.retrieve("backpropagation", limit=5)
        self.assertEqual(len(hits_after), 0)

        stats = self.pipeline.indexer.stats()
        self.assertEqual(stats["sources"], 0)
        self.assertEqual(stats["chunks"], 0)

    def test_clickable_html_formatting(self):
        """Valida transformação de [DocID:Trecho] em link seguro mark-citation://."""
        raw = "Conforme visto em [doc_123:regra da cadeia]."
        html = format_clickable_citations(raw, {"doc_123": "Documento Teste"})
        self.assertIn("mark-citation://doc_123", html)
        self.assertIn("excerpt=regra%20da%20cadeia", html)

    def test_docx_document_ingestion(self):
        """Valida que documentos DOCX são importados, extraídos e indexados corretamente."""
        import zipfile
        docx_path = self.tmp_path / "documento.docx"
        with zipfile.ZipFile(docx_path, "w") as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>',
            )
            doc_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Estrutura de microkernel e isolamento de processos no MARK OS.</w:t></w:r></w:p></w:body>'
                '</w:document>'
            )
            z.writestr("word/document.xml", doc_xml)

        rec = self.pipeline.indexer.ingest_file(docx_path, title="Microkernel Spec")
        self.assertEqual(rec.format, "docx")
        self.assertEqual(rec.title, "Microkernel Spec")

        hits = self.pipeline.retriever.retrieve("microkernel e isolamento", limit=3)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("microkernel", hits[0].chunk.text.lower())


if __name__ == "__main__":
    unittest.main()
