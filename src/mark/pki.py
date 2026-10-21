"""PKI, Certificate Pinning, mTLS e Proteção contra Interceptação MITM para o MARK."""

from __future__ import annotations

import hashlib
import re
import ssl
from typing import Iterable, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class CertificatePinError(Exception):
    """Lançada quando o certificado apresentado pelo servidor não confere com o pin SHA-256 fixado."""


class TlsSecurityError(Exception):
    """Lançada quando a versão de TLS negociada é inferior a TLS 1.3 ou quando a segurança da conexão é violada."""


def compute_cert_pin(cert_der: bytes) -> str:
    """Calcula o pin SHA-256 em formato hexadecimal minúsculo a partir do certificado codificado em DER."""
    if not isinstance(cert_der, (bytes, bytearray)) or len(cert_der) < 32:
        raise ValueError("Certificado DER inválido ou corrompido.")
    return hashlib.sha256(cert_der).hexdigest().lower()


class CertificatePinningValidator:
    """Validador rigoroso de Certificate Pinning, mTLS e mitigação de MITM para conexões móveis e WearOS."""

    def __init__(
        self,
        primary_pin: str,
        backup_pins: Sequence[str] | None = None,
        min_tls_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3,
    ):
        self.min_tls_version = min_tls_version
        self._pins: set[str] = set()
        self._primary_pin = self._sanitize_pin(primary_pin)
        self._pins.add(self._primary_pin)
        if backup_pins:
            for pin in backup_pins:
                self._pins.add(self._sanitize_pin(pin))

    @staticmethod
    def _sanitize_pin(pin: str) -> str:
        if not isinstance(pin, str):
            raise ValueError("O pin de certificado deve ser uma string.")
        clean = pin.strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", clean):
            raise ValueError(f"Pin inválido '{pin}': deve ser um digest SHA-256 em hexadecimal com 64 caracteres.")
        return clean

    @property
    def primary_pin(self) -> str:
        return self._primary_pin

    @property
    def active_pins(self) -> frozenset[str]:
        return frozenset(self._pins)

    def is_pinned(self, cert_der: bytes) -> bool:
        """Verifica se o certificado DER fornecido corresponde a um dos pins autorizados."""
        try:
            pin = compute_cert_pin(cert_der)
            return pin in self._pins
        except ValueError:
            return False

    def validate_peer_certificate(self, cert_der: bytes | None) -> str:
        """Valida o certificado de folha do servidor contra a lista estrita de pins SHA-256.

        Bloqueia imediatamente a conexão se o certificado for ausente ou não coincidir.
        """
        if cert_der is None:
            raise CertificatePinError("Certificado do servidor MARK ausente na negociação TLS.")
        actual_pin = compute_cert_pin(cert_der)
        if actual_pin not in self._pins:
            raise CertificatePinError(
                f"Identidade do servidor MARK mudou (obtido {actual_pin[:16]}…, esperado {self._primary_pin[:16]}…). "
                "Conexão bloqueada imediatamente contra interceptação MITM ou autoridade certificadora não reconhecida."
            )
        return actual_pin

    def validate_tls_version(self, protocol_version: str | ssl.TLSVersion) -> None:
        """Garante que a versão negociada do protocolo é estritamente TLS 1.3."""
        if isinstance(protocol_version, ssl.TLSVersion):
            if protocol_version != ssl.TLSVersion.TLSv1_3:
                raise TlsSecurityError(
                    f"Versão de TLS insegura '{protocol_version.name}'. MARK exige TLS 1.3 estrito."
                )
            return

        clean = str(protocol_version).strip().upper()
        if clean not in {"TLSV1.3", "TLS 1.3", "TLS1.3"}:
            raise TlsSecurityError(
                f"Protocolo '{protocol_version}' rejeitado. A conexão com o MARK exige exclusivamente TLS 1.3."
            )

    def validate_connection(self, tls_version: str | ssl.TLSVersion, peer_cert_der: bytes | None) -> str:
        """Executa a validação combinada de versão TLS e certificate pinning."""
        self.validate_tls_version(tls_version)
        return self.validate_peer_certificate(peer_cert_der)

    def simulate_mitm_interception(self, proxy_or_rogue_cert_der: bytes) -> tuple[bool, str]:
        """Simula tentativa de interceptação MITM (proxy, CA corporativa ou certificado fraudulento).

        Retorna (False, mensagem_de_bloqueio) caso o tráfego seja abortado com sucesso antes de qualquer vazamento.
        """
        try:
            self.validate_peer_certificate(proxy_or_rogue_cert_der)
            return True, "Tráfego permitido incorretamente"
        except CertificatePinError as exc:
            return False, str(exc)

    def rotate_pin(self, new_pin: str, keep_previous_as_backup: bool = True) -> None:
        """Aplica rotação segura de certificado com suporte a fallback gracioso."""
        sanitized = self._sanitize_pin(new_pin)
        if not keep_previous_as_backup:
            self._pins.clear()
        self._primary_pin = sanitized
        self._pins.add(sanitized)

    def validate_client_mtls_proof(
        self,
        client_public_der: bytes,
        proof_signature: bytes,
        challenge_message: bytes,
    ) -> bool:
        """Verifica prova criptográfica de mTLS/posse de chave EC P-256 enviada pelo cliente."""
        try:
            key = serialization.load_der_public_key(client_public_der)
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
                return False
            key.verify(proof_signature, challenge_message, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            return False
