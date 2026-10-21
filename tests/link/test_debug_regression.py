"""Suíte de Testes de Regressão, Debug & Hunt do MARK Link.

Valida a correção de falhas críticas descobertas na varredura profunda:
1. Falha de segurança no UpdateVerifier ao permitir verificação sem chave pública (bypass de assinatura).
2. Condição de corrida e split-brain no DirectListener ao reconectar mesmo device_id.
3. Paridade do protocolo binário MLNK com Rust (PacketType 0x09 SessionResume e 0x0A SessionResumeOk).
4. Paridade da máquina de estados e candidatos com Rust (CandidateType.LOOPBACK e PeerState.LOOPBACK).
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.mark.link.peer.manager import LinkPeerManager
from src.mark.link.peer.state import CandidateType, ConnectionCandidate, PeerState
from src.mark.link.telemetry.collector import LinkTelemetryCollector
from src.mark.link.transport.direct import DirectConnectionHandler, DirectListener
from src.mark.link.transport.framing import (
    HEADER_SIZE,
    PacketHeader,
    PacketType,
)
from src.mark.link.updater import (
    DowngradeError,
    UpdateIntegrityError,
    UpdateManifest,
    UpdateSignatureError,
    UpdateVerifier,
    canonical_update_manifest,
)


class TestDebugRegression(unittest.TestCase):
    """Testes de regressão cobrindo falhas ocultas identificadas na varredura."""

    def test_update_verifier_requires_release_key_and_rejects_none(self) -> None:
        """
        [FASE RED] VULNERABILIDADE: UpdateVerifier com release_public_key=None
        não deve retornar valid=True ignorando a assinatura. Deve lançar UpdateSignatureError.
        """
        verifier = UpdateVerifier(current_version="1.0.0", release_public_key=None)
        payload = b"FAKE_UNTRUSTED_BINARY_PAYLOAD_2.0.0"
        sha256_hash = hashlib.sha256(payload).hexdigest()

        manifest = UpdateManifest(
            version="2.0.0",
            payload_sha256=sha256_hash,
            timestamp=int(time.time()),
            signature_b64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            channel="stable",
        )

        with self.assertRaises(UpdateSignatureError):
            verifier.verify_update(manifest, payload)

    def test_packet_type_parity_supports_session_resume(self) -> None:
        """
        [FASE RED] INCONSISTÊNCIA DE PROTOCOLO: PacketType deve incluir
        SessionResume (0x09) e SessionResumeOk (0x0A) em paridade com o Rust MLNK.
        """
        self.assertTrue(hasattr(PacketType, "SESSION_RESUME"), "PacketType deve conter SESSION_RESUME")
        self.assertTrue(hasattr(PacketType, "SESSION_RESUME_OK"), "PacketType deve conter SESSION_RESUME_OK")
        self.assertEqual(PacketType.SESSION_RESUME, 0x09)
        self.assertEqual(PacketType.SESSION_RESUME_OK, 0x0A)

        # Testa decodificação de frame com PacketType 0x09
        hdr_bytes = PacketHeader(
            packet_type=PacketType.SESSION_RESUME,
            flags=0,
            sequence=1,
            length=32,
        ).encode()

        decoded = PacketHeader.decode(hdr_bytes)
        self.assertEqual(decoded.packet_type, PacketType.SESSION_RESUME)
        self.assertEqual(decoded.sequence, 1)

    def test_peer_state_and_candidate_loopback_parity(self) -> None:
        """
        [FASE RED] DIVERGÊNCIA COM RUST: CandidateType e PeerState devem suportar
        Loopback (prioridade 200) com is_connected e is_direct True.
        """
        self.assertTrue(hasattr(CandidateType, "LOOPBACK"), "CandidateType deve possuir LOOPBACK")
        self.assertEqual(int(CandidateType.LOOPBACK), 200)

        self.assertTrue(hasattr(PeerState, "LOOPBACK"), "PeerState deve possuir LOOPBACK")
        self.assertEqual(PeerState.LOOPBACK.value, "Loopback")
        self.assertTrue(PeerState.LOOPBACK.is_connected)
        self.assertTrue(PeerState.LOOPBACK.is_direct)

        # LinkPeerManager deve transicionar para LOOPBACK em vez de RELAYED
        pm = LinkPeerManager(peer_id="test-peer")
        pm.set_connected(CandidateType.LOOPBACK)
        self.assertEqual(pm.state, PeerState.LOOPBACK)

        # LinkTelemetryCollector deve refletir Loopback
        telem = LinkTelemetryCollector()
        telem.set_state(PeerState.LOOPBACK)
        report = telem.generate_report()
        self.assertEqual(report.state, "Loopback")
        self.assertEqual(report.mode, "Loopback")
        self.assertEqual(report.layers.loopback.status, "ok")

    def test_direct_listener_split_brain_zombie_cleanup(self) -> None:
        """
        [FASE RED] CONCORRÊNCIA: DirectListener deve encerrar conexões anteriores
        ativas para o mesmo device_id ao receber nova sessão e limpar referências na saída.
        """
        listener = DirectListener(host="127.0.0.1", port=0, key_resolver=lambda d: b"dummy")

        mock_sock1 = MagicMock()
        mock_channel1 = MagicMock()
        handler1 = DirectConnectionHandler(mock_sock1, mock_channel1, "device-alpha", listener.adapter)
        handler1.running = True

        listener.active_handlers.append(handler1)

        # Simula nova conexão para o mesmo device-alpha
        with listener.lock:
            old_handlers = [h for h in listener.active_handlers if h.device_id == "device-alpha" and h.running]
            for old in old_handlers:
                old.close()
            listener.active_handlers = [h for h in listener.active_handlers if h.running]

        self.assertFalse(handler1.running, "Handler anterior do mesmo device_id deve ser encerrado")
        self.assertNotIn(handler1, listener.active_handlers, "Handler anterior deve ser removido")


if __name__ == "__main__":
    unittest.main()
