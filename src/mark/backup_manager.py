"""Sistema de Portabilidade, Criptografia Autenticada (AEAD) e Backup/Restauração Offline do MARK (Fase P2).

Permite exportar, validar e restaurar de forma 100% local e offline as bases
SQLite de memória persistente (`conversation.sqlite3`, `desktop-conversations.sqlite3`),
bases FTS5 de conhecimento (`knowledge.sqlite3`) e diretórios de conhecimento,
usando snapshots consistentes (`sqlite3.Connection.backup`), criptografia autenticada
AEAD (AES-256-GCM / ChaCha20-Poly1305) com derivação de chave PBKDF2-HMAC-SHA256,
salt aleatório e nonce de uso único por backup, blindagem estrita contra Zip-Slip,
Zip-Bomb e symlinks, verificação de compatibilidade de schema do SQLite e mecanismo
de rollback atômico em caso de falha de restauração.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import tempfile
from typing import Any
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .cognition.schemas import SCHEMA_VERSION
from .cognition.storage import private_file
from .cognition.tools import BridgeCapabilityGuard, CapabilityToken, ToolContract, ToolRegistry
from .gods_eye_view import _schema_check

log = logging.getLogger("mark.backup_manager")

BACKUP_FORMAT_VERSION_V1 = 1
BACKUP_FORMAT_VERSION_V2 = 2
BACKUP_FORMAT_VERSION = BACKUP_FORMAT_VERSION_V2

BACKUP_MAGIC_V1 = "MARK_OFFLINE_BACKUP_V1"
BACKUP_MAGIC_V2 = "MARK_AEAD_BACKUP_V2"
BACKUP_MAGIC = BACKUP_MAGIC_V2

DEFAULT_PBKDF2_ITERATIONS = 310_000
MIN_PBKDF2_ITERATIONS = 100_000
MAX_PBKDF2_ITERATIONS = 2_000_000
SUPPORTED_BACKUP_CIPHERS = frozenset({"AES-256-GCM", "ChaCha20-Poly1305"})

MAX_BACKUP_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100.0

DEFAULT_BACKUP_KEY_FILE = Path.home() / ".local/state/mark/backup_master.key"

FORBIDDEN_BACKUP_FILENAMES = frozenset({
    "home_assistant.token",
    "mobile.token",
    "api.token",
    "id_rsa",
    "id_ed25519",
    ".env",
    "backup_master.key",
    "smart_home_vault.key",
})


class BackupError(RuntimeError):
    """Erro base de exportação, verificação ou restauração de backup."""


class BackupIntegrityError(BackupError):
    """Violação de integridade (checksum, SQLite corrompido, zip-slip, zip-bomb, symlink ou AEAD inválido)."""


@dataclass(frozen=True)
class BackupEntry:
    archive_path: str
    category: str
    sha256: str
    size_bytes: int
    ciphertext_sha256: str = ""
    encrypted_size_bytes: int = 0
    nonce: str = ""
    sqlite_user_version: int = 0
    row_counts: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupManifest:
    magic: str
    format_version: int
    schema_version: int
    created_at: str
    entries: list[dict[str, Any]]
    cipher: str = "AES-256-GCM"
    kdf: str = "PBKDF2-HMAC-SHA256"
    iterations: int = DEFAULT_PBKDF2_ITERATIONS
    salt: str = ""
    base_nonce: str = ""
    manifest_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _compute_manifest_digest(payload: dict[str, Any]) -> str:
    canonical: dict[str, Any] = {
        "magic": payload.get("magic"),
        "format_version": payload.get("format_version"),
        "schema_version": payload.get("schema_version"),
        "created_at": payload.get("created_at"),
        "entries": payload.get("entries", []),
    }
    for opt in ("cipher", "kdf", "iterations", "salt", "base_nonce"):
        if opt in payload:
            canonical[opt] = payload[opt]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_archive_relpath(rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise BackupIntegrityError("Caminho interno vazio no arquivo de backup.")
    if "\x00" in rel_path:
        raise BackupIntegrityError("Caractere nulo detectado no caminho do backup.")
    normalized = rel_path.replace("\\", "/").strip("/")
    parts = [p for p in normalized.split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts) or rel_path.startswith(("/", "\\")):
        raise BackupIntegrityError(f"Caminho inseguro detectado no pacote de backup (path traversal / Zip-Slip): {rel_path!r}")
    for part in parts:
        if ":" in part:
            raise BackupIntegrityError(f"Caractere não permitido no caminho do arquivo: {rel_path!r}")
    if parts[-1] in FORBIDDEN_BACKUP_FILENAMES or parts[-1].endswith(".token") or parts[-1].endswith(".key"):
        raise BackupIntegrityError(f"Arquivo de credencial/chave proibido em pacote de backup: {parts[-1]!r}")
    return "/".join(parts)


def get_or_create_backup_master_key(key_path: Path | str | None = None) -> bytes:
    """Carrega ou gera uma chave mestra de 256 bits com permissão POSIX 0600 fora dos backups."""
    path = Path(key_path) if key_path is not None else DEFAULT_BACKUP_KEY_FILE
    if path.is_symlink():
        raise BackupIntegrityError(f"Chave mestra de backup não pode ser um symlink: {path}")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o077):
            raise BackupIntegrityError("Arquivo de chave mestra de backup deve pertencer ao usuário e ter permissão 0600.")
        raw = path.read_bytes().strip()
        if len(raw) == 32:
            return raw
        if len(raw) == 64:
            try:
                return bytes.fromhex(raw.decode("ascii"))
            except ValueError:
                pass
        return raw[:32].ljust(32, b"\x00")

    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise BackupIntegrityError(f"Diretório de chave mestra não pode ser symlink: {parent}")
    key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    path.chmod(0o600)
    return key


def _derive_backup_key(
    master_secret: bytes,
    salt: bytes,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> bytes:
    if len(salt) < 16:
        raise BackupIntegrityError("Salt criptográfico inválido para derivação PBKDF2.")
    if not MIN_PBKDF2_ITERATIONS <= int(iterations) <= MAX_PBKDF2_ITERATIONS:
        raise BackupIntegrityError("Iterações PBKDF2 fora dos limites de segurança permitidos (100.000 a 2.000.000).")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=int(iterations),
    )
    return kdf.derive(master_secret)


def _aead_encrypt(cipher_name: str, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if cipher_name == "AES-256-GCM":
        return AESGCM(key).encrypt(nonce, plaintext, aad)
    if cipher_name == "ChaCha20-Poly1305":
        return ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)
    raise ValueError(f"Algoritmo AEAD não suportado: {cipher_name}")


def _aead_decrypt(cipher_name: str, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    try:
        if cipher_name == "AES-256-GCM":
            return AESGCM(key).decrypt(nonce, ciphertext, aad)
        if cipher_name == "ChaCha20-Poly1305":
            return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
        raise ValueError(f"Algoritmo AEAD não suportado: {cipher_name}")
    except InvalidTag as exc:
        raise BackupIntegrityError("Falha de autenticação AEAD: tag inválida ou backup adulterado.") from exc


def verify_sqlite_file(db_path: Path, *, expected_category: str = "") -> tuple[int, dict[str, int]]:
    """Executa PRAGMA integrity_check e valida tabelas/versão em um arquivo SQLite offline."""
    if db_path.is_symlink():
        raise BackupIntegrityError(f"Banco SQLite não pode ser um symlink: {db_path}")
    if not db_path.is_file():
        raise BackupIntegrityError(f"Banco SQLite ausente: {db_path}")
    with db_path.open("rb") as fh:
        header = fh.read(16)
    if header != b"SQLite format 3\x00":
        raise BackupIntegrityError(f"Cabeçalho SQLite inválido ou corrompido em {db_path.name}.")

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchall()
            if not integrity or integrity[0][0] != "ok":
                raise BackupIntegrityError(f"Falha no PRAGMA integrity_check para {db_path.name}: {integrity}")
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if expected_category == "knowledge_db" and user_version not in (0, SCHEMA_VERSION):
                raise BackupIntegrityError(
                    f"Versão de schema incompatível na base de conhecimento ({user_version} != {SCHEMA_VERSION})."
                )
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            row_counts: dict[str, int] = {}
            for table in tables:
                if not table.replace("_", "").isalnum():
                    continue
                if table.endswith(("_data", "_idx", "_content", "_docsize", "_config")):
                    continue
                try:
                    cnt = int(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])  # nosec: B608
                    row_counts[table] = cnt
                except sqlite3.Error:
                    continue
            return user_version, row_counts
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BackupIntegrityError(f"Banco SQLite corrompido ({db_path.name}): {exc}") from exc


def _snapshot_sqlite(source_db: Path, dest_db: Path) -> None:
    """Realiza cópia atômica e consistente do banco SQLite via API nativa de backup."""
    if source_db.is_symlink():
        raise BackupIntegrityError(f"Origem do banco SQLite é um symlink não permitido: {source_db}")
    src_conn = sqlite3.connect(str(source_db), timeout=10)
    try:
        dst_conn = sqlite3.connect(str(dest_db), timeout=10)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    dest_db.chmod(0o600)


class BackupManager:
    """Gerenciador offline de backup e restauração autenticada (AEAD) do MARK."""

    _process_master_key: bytes | None = None

    def __init__(
        self,
        *,
        state_dir: str | Path | None = None,
        knowledge_db: str | Path | None = None,
        knowledge_files_dir: str | Path | None = None,
        master_key_file: str | Path | None = None,
    ):
        base_state = Path(state_dir) if state_dir is not None else (Path.home() / ".local/state/mark")
        self.state_dir = base_state
        self.conversation_db = self.state_dir / "conversation.sqlite3"
        self.desktop_conversations_db = self.state_dir / "desktop-conversations.sqlite3"
        self.knowledge_db = (
            Path(knowledge_db)
            if knowledge_db is not None
            else (self.state_dir / "cognition" / "knowledge.sqlite3")
        )
        self.knowledge_files_dir = (
            Path(knowledge_files_dir)
            if knowledge_files_dir is not None
            else (self.state_dir / "cognition" / "files")
        )
        self.master_key_file = (
            Path(master_key_file)
            if master_key_file is not None
            else (self.state_dir / "backup_master.key")
        )

    def get_or_create_master_key(self) -> bytes:
        # 1. Se é um arquivo customizado e já existe no disco, use-o
        if self.master_key_file != DEFAULT_BACKUP_KEY_FILE and self.master_key_file.exists():
            key = get_or_create_backup_master_key(self.master_key_file)
            BackupManager._process_master_key = key
            return key

        # 2. Se já temos uma chave ativa no processo (ex: ponte de testes ou registry padrão), use-a
        if BackupManager._process_master_key is not None:
            # Se a instância atual possui um master_key_file específico que ainda não existe, persiste nele
            if self.master_key_file != DEFAULT_BACKUP_KEY_FILE and not self.master_key_file.exists():
                parent = self.master_key_file.parent
                parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.master_key_file, flags, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(BackupManager._process_master_key)
                self.master_key_file.chmod(0o600)
            return BackupManager._process_master_key

        # 3. Caso contrário, carrega ou cria no arquivo do caminho configurado
        key = get_or_create_backup_master_key(self.master_key_file)
        BackupManager._process_master_key = key
        return key

    def resolve_key(self, key: bytes | None = None, passphrase: str | None = None) -> bytes:
        if key is not None:
            return key
        if passphrase is not None:
            return passphrase.encode("utf-8")
        return self.get_or_create_master_key()

    def create_backup(
        self,
        destination_path: str | Path,
        *,
        include_knowledge_files: bool = True,
        cipher: str = "AES-256-GCM",
        key: bytes | None = None,
        passphrase: str | None = None,
        iterations: int = DEFAULT_PBKDF2_ITERATIONS,
        envelope_format: bool = False,
    ) -> dict[str, Any]:
        """Exporta bases SQLite e arquivos para um pacote criptografado com AEAD (AES-256-GCM ou ChaCha20-Poly1305)."""
        if cipher not in SUPPORTED_BACKUP_CIPHERS:
            raise ValueError(f"Algoritmo AEAD não suportado: {cipher}")

        dest = Path(destination_path).expanduser().resolve()
        if dest.is_symlink():
            raise BackupIntegrityError("Destino do backup não pode ser um symlink.")
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if dest.parent.is_symlink():
            raise BackupIntegrityError("Diretório pai do destino do backup não pode ser um symlink.")

        # Resolução da chave mestra (fora do arquivo)
        master_secret = self.resolve_key(key=key, passphrase=passphrase)

        salt = secrets.token_bytes(32)
        derived_key = _derive_backup_key(master_secret, salt, iterations=iterations)
        base_nonce = secrets.token_bytes(12)

        entries: list[BackupEntry] = []
        created_at = datetime.now(timezone.utc).isoformat()

        with tempfile.TemporaryDirectory(prefix="mark-backup-stage-") as stage_tmp:
            stage_dir = Path(stage_tmp)
            stage_dir.chmod(0o700)
            db_targets = [
                ("databases/conversation.sqlite3", self.conversation_db, "conversation_db"),
                ("databases/desktop-conversations.sqlite3", self.desktop_conversations_db, "desktop_conversations_db"),
                ("databases/knowledge.sqlite3", self.knowledge_db, "knowledge_db"),
            ]

            raw_staged_files: list[tuple[str, Path, str, int, dict[str, int]]] = []
            for arc_name, src_path, category in db_targets:
                if not src_path.is_file():
                    continue
                if src_path.is_symlink():
                    raise BackupIntegrityError(f"Banco de dados é um symlink não permitido: {src_path}")
                staged_db = stage_dir / "raw" / arc_name
                staged_db.parent.mkdir(parents=True, exist_ok=True)
                _snapshot_sqlite(src_path, staged_db)
                user_ver, counts = verify_sqlite_file(staged_db, expected_category=category)
                raw_staged_files.append((arc_name, staged_db, category, user_ver, counts))

            if include_knowledge_files and self.knowledge_files_dir.is_dir():
                for file_path in sorted(self.knowledge_files_dir.rglob("*")):
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    if file_path.name in FORBIDDEN_BACKUP_FILENAMES or file_path.name.endswith(".token") or file_path.name.endswith(".key"):
                        continue
                    rel = file_path.relative_to(self.knowledge_files_dir).as_posix()
                    arc_name = _validate_archive_relpath(f"knowledge_files/{rel}")
                    raw_staged_files.append((arc_name, file_path, "knowledge_file", 0, {}))

            if not raw_staged_files:
                raise BackupError("Nenhuma base de memória ou conhecimento encontrada para exportar.")

            # Criptografia AEAD por membro
            encrypted_files: list[tuple[str, Path]] = []
            for arc_name, local_file, category, user_ver, counts in raw_staged_files:
                plain_bytes = local_file.read_bytes()
                plain_size = len(plain_bytes)
                if plain_size > MAX_MEMBER_BYTES:
                    raise BackupIntegrityError(f"Arquivo excede limite de {MAX_MEMBER_BYTES} bytes: {arc_name}")
                plain_sha = hashlib.sha256(plain_bytes).hexdigest()

                entry_nonce = secrets.token_bytes(12)
                entry_aad = f"{arc_name}:{category}:{plain_sha}:{plain_size}".encode("utf-8")
                cipher_bytes = _aead_encrypt(cipher, derived_key, entry_nonce, plain_bytes, aad=entry_aad)

                enc_path = stage_dir / "encrypted" / arc_name
                enc_path.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(enc_path, flags, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(cipher_bytes)
                enc_path.chmod(0o600)

                cipher_sha = hashlib.sha256(cipher_bytes).hexdigest()
                entries.append(
                    BackupEntry(
                        archive_path=arc_name,
                        category=category,
                        sha256=plain_sha,
                        size_bytes=plain_size,
                        ciphertext_sha256=cipher_sha,
                        encrypted_size_bytes=len(cipher_bytes),
                        nonce=entry_nonce.hex(),
                        sqlite_user_version=user_ver,
                        row_counts=counts or None,
                    )
                )
                encrypted_files.append((arc_name, enc_path))

            manifest_dict: dict[str, Any] = {
                "magic": BACKUP_MAGIC_V2,
                "format_version": BACKUP_FORMAT_VERSION_V2,
                "schema_version": SCHEMA_VERSION,
                "created_at": created_at,
                "cipher": cipher,
                "kdf": "PBKDF2-HMAC-SHA256",
                "iterations": iterations,
                "salt": salt.hex(),
                "base_nonce": base_nonce.hex(),
                "entries": [e.to_dict() for e in entries],
            }
            manifest_dict["manifest_sha256"] = _compute_manifest_digest(manifest_dict)

            temp_archive = stage_dir / "bundle.zip"
            with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest_dict, indent=2, sort_keys=True).encode("utf-8"))
                for arc_name, local_file in encrypted_files:
                    zf.write(local_file, arcname=arc_name)

            if envelope_format or dest.name.endswith(".enc"):
                bundle_bytes = temp_archive.read_bytes()
                env_nonce = secrets.token_bytes(12)
                env_aad = f"{BACKUP_MAGIC_V2}:{cipher}:{salt.hex()}".encode("utf-8")
                env_ciphertext = _aead_encrypt(cipher, derived_key, env_nonce, bundle_bytes, aad=env_aad)
                envelope_payload = {
                    "magic": "MARK_AEAD_ENVELOPE_V2",
                    "cipher": cipher,
                    "kdf": "PBKDF2-HMAC-SHA256",
                    "iterations": iterations,
                    "salt": salt.hex(),
                    "nonce": env_nonce.hex(),
                    "created_at": created_at,
                    "schema_version": SCHEMA_VERSION,
                    "ciphertext": base64.b64encode(env_ciphertext).decode("ascii"),
                }
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                dest.write_text(json.dumps(envelope_payload, indent=2), encoding="utf-8")
                dest.chmod(0o600)
                target = dest
            else:
                target = private_file(dest)
                shutil.copyfile(temp_archive, target)
                target.chmod(0o600)

        verification = self.verify_backup(target, key=master_secret)
        return {
            "backup_path": str(target),
            "created_at": created_at,
            "cipher": cipher,
            "entry_count": len(entries),
            "manifest_sha256": manifest_dict["manifest_sha256"],
            "verified": verification["valid"],
            "entries": [e.to_dict() for e in entries],
        }

    def verify_backup(
        self,
        backup_path: str | Path,
        *,
        key: bytes | None = None,
        passphrase: str | None = None,
    ) -> dict[str, Any]:
        """Valida manifesto, criptografia AEAD, ausência de path traversal, limites anti-zip bomb e integridade SQLite."""
        archive = Path(backup_path).expanduser().resolve()
        if archive.is_symlink() or not archive.is_file():
            raise BackupIntegrityError("Arquivo de backup inexistente, inválido ou symlink não permitido.")
        if archive.stat().st_size > MAX_BACKUP_ARCHIVE_BYTES:
            raise BackupIntegrityError("Arquivo de backup excede o tamanho máximo permitido.")

        with tempfile.TemporaryDirectory(prefix="mark-backup-verify-") as tmp:
            tmp_dir = Path(tmp)
            tmp_dir.chmod(0o700)

            # Detecta se é envelope JSON criptografado
            is_envelope = False
            try:
                head = archive.read_text(encoding="utf-8")[:512]
                if '"magic": "MARK_AEAD_ENVELOPE_V2"' in head:
                    is_envelope = True
            except Exception:
                pass

            if is_envelope:
                env_dict = json.loads(archive.read_text(encoding="utf-8"))
                env_cipher = str(env_dict.get("cipher", "AES-256-GCM"))
                env_salt = bytes.fromhex(str(env_dict["salt"]))
                env_nonce = bytes.fromhex(str(env_dict["nonce"]))
                env_iter = int(env_dict.get("iterations", DEFAULT_PBKDF2_ITERATIONS))

                master_secret = self.resolve_key(key=key, passphrase=passphrase)

                derived_key = _derive_backup_key(master_secret, env_salt, iterations=env_iter)
                env_aad = f"{BACKUP_MAGIC_V2}:{env_cipher}:{env_salt.hex()}".encode("utf-8")
                raw_cipher = base64.b64decode(env_dict["ciphertext"])
                decrypted_zip = _aead_decrypt(env_cipher, derived_key, env_nonce, raw_cipher, aad=env_aad)
                zip_path = tmp_dir / "unpacked.zip"
                zip_path.write_bytes(decrypted_zip)
                zip_path.chmod(0o600)
                zip_to_check = zip_path
            else:
                if not zipfile.is_zipfile(archive):
                    raise BackupIntegrityError("Arquivo fornecido não é um pacote ZIP válido do MARK.")
                zip_to_check = archive

            with zipfile.ZipFile(zip_to_check, "r") as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ARCHIVE_MEMBERS:
                    raise BackupIntegrityError(f"Pacote de backup contém membros demais ({len(infos)} > {MAX_ARCHIVE_MEMBERS}).")

                names = {info.filename for info in infos}
                if "manifest.json" not in names:
                    raise BackupIntegrityError("Pacote de backup não contém manifest.json.")

                total_uncompressed = 0
                for info in infos:
                    # Checagem rigorosa de Symlink no ZIP
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise BackupIntegrityError(f"Symlink detectado no pacote de backup: {info.filename}")

                    # Checagem de Zip-Slip
                    _validate_archive_relpath(info.filename)

                    # Checagens Anti-Zip Bomb
                    if info.file_size > MAX_MEMBER_BYTES:
                        raise BackupIntegrityError(f"Membro do backup excede o tamanho seguro ({info.file_size} > {MAX_MEMBER_BYTES}): {info.filename}")

                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise BackupIntegrityError("Volume total de descompressão excede limite seguro (anti-Zip Bomb).")

                    if info.compress_size > 0 and info.file_size > 1024 * 1024:
                        ratio = info.file_size / info.compress_size
                        if ratio > MAX_COMPRESSION_RATIO:
                            raise BackupIntegrityError(f"Taxa de compressão suspeita detectada (possível Zip-Bomb) em {info.filename}: ratio={ratio:.1f}")

                try:
                    manifest_raw = json.loads(zf.read("manifest.json").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupIntegrityError("manifest.json corrompido no backup.") from exc

                if not isinstance(manifest_raw, dict):
                    raise BackupIntegrityError("Estrutura do manifest.json inválida.")

                magic = manifest_raw.get("magic")
                if magic not in (BACKUP_MAGIC_V1, BACKUP_MAGIC_V2):
                    raise BackupIntegrityError(f"Assinatura (magic) do backup incompatível ou desconhecida: {magic!r}")

                if manifest_raw.get("format_version") not in (BACKUP_FORMAT_VERSION_V1, BACKUP_FORMAT_VERSION_V2):
                    raise BackupIntegrityError("Versão de formato do backup não suportada.")

                if manifest_raw.get("schema_version") not in (0, SCHEMA_VERSION):
                    raise BackupIntegrityError("Versão de schema do conhecimento incompatível com esta instância.")

                expected_digest = _compute_manifest_digest(manifest_raw)
                if manifest_raw.get("manifest_sha256") != expected_digest:
                    raise BackupIntegrityError("Assinatura SHA-256 do manifesto não confere (manifesto adulterado).")

                manifest_entries = manifest_raw.get("entries")
                if not isinstance(manifest_entries, list) or not manifest_entries:
                    raise BackupIntegrityError("Manifesto de backup não possui entradas válidas.")

                is_encrypted = magic == BACKUP_MAGIC_V2 or "cipher" in manifest_raw
                derived_key: bytes | None = None
                cipher_name = "AES-256-GCM"
                if is_encrypted:
                    cipher_name = str(manifest_raw.get("cipher", "AES-256-GCM"))
                    if cipher_name not in SUPPORTED_BACKUP_CIPHERS:
                        raise BackupIntegrityError(f"Cifra AEAD desconhecida no backup: {cipher_name}")
                    salt = bytes.fromhex(str(manifest_raw["salt"]))
                    iterations = int(manifest_raw.get("iterations", DEFAULT_PBKDF2_ITERATIONS))

                    master_secret = self.resolve_key(key=key, passphrase=passphrase)

                    derived_key = _derive_backup_key(master_secret, salt, iterations=iterations)

                recorded_paths: set[str] = set()
                verified_entries: list[dict[str, Any]] = []

                for entry in manifest_entries:
                    if not isinstance(entry, dict):
                        raise BackupIntegrityError("Entrada inválida no manifesto.")
                    arc_path = _validate_archive_relpath(str(entry.get("archive_path", "")))
                    if arc_path not in names:
                        raise BackupIntegrityError(f"Arquivo listado no manifesto está ausente no pacote: {arc_path}")
                    recorded_paths.add(arc_path)

                    raw_bytes = zf.read(arc_path)
                    if is_encrypted:
                        entry_nonce = bytes.fromhex(str(entry.get("nonce", "")))
                        expected_plain_sha = str(entry.get("sha256", ""))
                        expected_plain_size = int(entry.get("size_bytes", -1))
                        expected_cipher_sha = entry.get("ciphertext_sha256")
                        if expected_cipher_sha and hashlib.sha256(raw_bytes).hexdigest() != expected_cipher_sha:
                            raise BackupIntegrityError(f"Ciphertext SHA-256 divergente para {arc_path} (dados adulterados).")

                        category = str(entry.get("category", ""))
                        entry_aad = f"{arc_path}:{category}:{expected_plain_sha}:{expected_plain_size}".encode("utf-8")
                        assert derived_key is not None
                        plain_bytes = _aead_decrypt(cipher_name, derived_key, entry_nonce, raw_bytes, aad=entry_aad)

                        actual_sha = hashlib.sha256(plain_bytes).hexdigest()
                        actual_size = len(plain_bytes)
                        if actual_sha != expected_plain_sha or actual_size != expected_plain_size:
                            raise BackupIntegrityError(f"Checksum SHA-256 pós-decriptografia divergente para {arc_path}.")
                    else:
                        plain_bytes = raw_bytes
                        actual_sha = hashlib.sha256(plain_bytes).hexdigest()
                        actual_size = len(plain_bytes)
                        if actual_sha != entry.get("sha256") or actual_size != int(entry.get("size_bytes", -1)):
                            raise BackupIntegrityError(f"Checksum SHA-256 ou tamanho divergente para {arc_path}.")

                    extracted = tmp_dir / arc_path
                    extracted.parent.mkdir(parents=True, exist_ok=True)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(extracted, flags, 0o600)
                    with os.fdopen(fd, "wb") as dst_stream:
                        dst_stream.write(plain_bytes)
                    extracted.chmod(0o600)

                    category = str(entry.get("category", ""))
                    if category.endswith("_db") or arc_path.endswith(".sqlite3"):
                        user_ver, row_counts = verify_sqlite_file(extracted, expected_category=category)
                        verified_entries.append({
                            "archive_path": arc_path,
                            "category": category,
                            "sha256": actual_sha,
                            "size_bytes": actual_size,
                            "sqlite_user_version": user_ver,
                            "row_counts": row_counts,
                        })
                    else:
                        verified_entries.append({
                            "archive_path": arc_path,
                            "category": category,
                            "sha256": actual_sha,
                            "size_bytes": actual_size,
                        })

                unexpected = (names - {"manifest.json"}) - recorded_paths
                if unexpected:
                    raise BackupIntegrityError(f"Arquivos não declarados no manifesto foram detectados no pacote: {sorted(unexpected)}")

        return {
            "valid": True,
            "backup_path": str(archive),
            "cipher": cipher_name if is_encrypted else "NONE",
            "encrypted": is_encrypted,
            "created_at": manifest_raw.get("created_at", ""),
            "schema_version": manifest_raw.get("schema_version", SCHEMA_VERSION),
            "manifest_sha256": expected_digest,
            "entry_count": len(verified_entries),
            "entries": verified_entries,
        }

    def restore_backup(
        self,
        backup_path: str | Path,
        *,
        key: bytes | None = None,
        passphrase: str | None = None,
    ) -> dict[str, Any]:
        """Valida integralmente o backup em área temporária antes de substituir atomicamente com suporte a rollback."""
        verification = self.verify_backup(backup_path, key=key, passphrase=passphrase)
        archive = Path(backup_path).expanduser().resolve()

        master_secret = self.resolve_key(key=key, passphrase=passphrase)

        restored_paths: list[str] = []

        with tempfile.TemporaryDirectory(prefix="mark-backup-restore-") as tmp:
            tmp_dir = Path(tmp)
            tmp_dir.chmod(0o700)
            rollback_dir = tmp_dir / "rollback_snapshots"
            rollback_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

            is_envelope = False
            try:
                head = archive.read_text(encoding="utf-8")[:512]
                if '"magic": "MARK_AEAD_ENVELOPE_V2"' in head:
                    is_envelope = True
            except Exception:
                pass

            if is_envelope:
                env_dict = json.loads(archive.read_text(encoding="utf-8"))
                env_cipher = str(env_dict.get("cipher", "AES-256-GCM"))
                env_salt = bytes.fromhex(str(env_dict["salt"]))
                env_nonce = bytes.fromhex(str(env_dict["nonce"]))
                env_iter = int(env_dict.get("iterations", DEFAULT_PBKDF2_ITERATIONS))
                derived_key = _derive_backup_key(master_secret, env_salt, iterations=env_iter)
                env_aad = f"{BACKUP_MAGIC_V2}:{env_cipher}:{env_salt.hex()}".encode("utf-8")
                raw_cipher = base64.b64decode(env_dict["ciphertext"])
                decrypted_zip = _aead_decrypt(env_cipher, derived_key, env_nonce, raw_cipher, aad=env_aad)
                zip_path = tmp_dir / "unpacked.zip"
                zip_path.write_bytes(decrypted_zip)
                zip_path.chmod(0o600)
                zip_to_read = zip_path
            else:
                zip_to_read = archive

            is_encrypted = verification.get("encrypted", False)
            cipher_name = verification.get("cipher", "AES-256-GCM")

            with zipfile.ZipFile(zip_to_read, "r") as zf:
                manifest_raw = json.loads(zf.read("manifest.json").decode("utf-8"))
                manifest_map = {e["archive_path"]: e for e in manifest_raw.get("entries", [])}

                if is_encrypted:
                    salt = bytes.fromhex(str(manifest_raw["salt"]))
                    iterations = int(manifest_raw.get("iterations", DEFAULT_PBKDF2_ITERATIONS))
                    derived_key = _derive_backup_key(master_secret, salt, iterations=iterations)
                else:
                    derived_key = None

                for entry in verification["entries"]:
                    arc_path = entry["archive_path"]
                    raw_bytes = zf.read(arc_path)
                    if is_encrypted:
                        meta = manifest_map[arc_path]
                        entry_nonce = bytes.fromhex(str(meta["nonce"]))
                        entry_aad = f"{arc_path}:{entry['category']}:{entry['sha256']}:{entry['size_bytes']}".encode("utf-8")
                        assert derived_key is not None
                        plain_bytes = _aead_decrypt(cipher_name, derived_key, entry_nonce, raw_bytes, aad=entry_aad)
                    else:
                        plain_bytes = raw_bytes

                    staged = tmp_dir / arc_path
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(staged, flags, 0o600)
                    with os.fdopen(fd, "wb") as dst:
                        dst.write(plain_bytes)
                    staged.chmod(0o600)

            # Planejamento de alvos
            planned_restores: list[tuple[Path, Path]] = []
            for entry in verification["entries"]:
                arc_path = entry["archive_path"]
                category = entry["category"]
                staged = tmp_dir / arc_path

                if category == "conversation_db":
                    target = self.conversation_db
                elif category == "desktop_conversations_db":
                    target = self.desktop_conversations_db
                elif category == "knowledge_db":
                    target = self.knowledge_db
                elif category == "knowledge_file" and arc_path.startswith("knowledge_files/"):
                    rel = arc_path.removeprefix("knowledge_files/")
                    safe_rel = _validate_archive_relpath(rel)
                    dest_file = (self.knowledge_files_dir / safe_rel).resolve()
                    try:
                        dest_file.relative_to(self.knowledge_files_dir.resolve())
                    except ValueError as exc:
                        raise BackupIntegrityError("Tentativa de escrita fora do diretório de conhecimento.") from exc
                    target = dest_file
                else:
                    raise BackupIntegrityError(f"Categoria de restauração desconhecida: {category}")

                if target.is_symlink():
                    raise BackupIntegrityError(f"Alvo de restauração não pode ser um symlink: {target}")
                planned_restores.append((target, staged))

            # Execução atômica com Rollback
            modified_targets: list[tuple[Path, Path | None]] = []
            try:
                for target, staged in planned_restores:
                    snapshot_copy: Path | None = None
                    if target.exists():
                        snapshot_copy = rollback_dir / f"snap_{secrets.token_hex(6)}_{target.name}"
                        shutil.copyfile(target, snapshot_copy)
                        snapshot_copy.chmod(0o600)

                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if target.parent.is_symlink():
                        raise BackupIntegrityError(f"Diretório pai do alvo não pode ser symlink: {target.parent}")

                    temp_replace = target.parent / f".restore_{os.getpid()}_{secrets.token_hex(6)}_{target.name}"
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(temp_replace, flags, 0o600)
                    with os.fdopen(fd, "wb") as dst:
                        dst.write(staged.read_bytes())
                    temp_replace.chmod(0o600)
                    os.replace(temp_replace, target)
                    target.chmod(0o600)
                    modified_targets.append((target, snapshot_copy))
                    restored_paths.append(str(target))
            except Exception as exc:
                log.error("Falha na restauração do backup. Iniciando reversão atômica (rollback)...", exc_info=True)
                for target, snapshot_copy in reversed(modified_targets):
                    try:
                        if snapshot_copy is not None and snapshot_copy.exists():
                            shutil.copyfile(snapshot_copy, target)
                            target.chmod(0o600)
                        elif target.exists():
                            target.unlink()
                    except Exception as rb_exc:
                        log.critical(f"Falha catastrófica ao reverter {target}: {rb_exc}")
                raise BackupError(f"Restauração abortada e revertida com sucesso (rollback): {exc}") from exc

        return {
            "restored": True,
            "backup_path": str(archive),
            "restored_count": len(restored_paths),
            "restored_files": restored_paths,
            "manifest_sha256": verification["manifest_sha256"],
        }


BACKUP_MCP_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "backup_verify_archive": {
        "description": "Verifica offline a integridade criptográfica (AEAD/SHA-256), estrutura e PRAGMA integrity_check de um arquivo de backup do MARK.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "backup_path": {"type": "string", "minLength": 1, "maxLength": 512},
                "passphrase": {"type": "string", "maxLength": 256},
            },
            "required": ["backup_path"],
            "additionalProperties": False,
        },
    },
    "backup_create_archive": {
        "description": "Cria um pacote de backup local verificado e criptografado com AEAD contendo as bases de memória e conhecimento do MARK.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "destination_path": {"type": "string", "minLength": 1, "maxLength": 512},
                "include_knowledge_files": {"type": "boolean"},
                "cipher": {"type": "string", "enum": ["AES-256-GCM", "ChaCha20-Poly1305"]},
                "passphrase": {"type": "string", "maxLength": 256},
            },
            "required": ["destination_path"],
            "additionalProperties": False,
        },
    },
    "backup_restore_archive": {
        "description": "Restaura um pacote de backup local somente após aprovação completa na verificação pré-restauração com reversão atômica em caso de falha.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "backup_path": {"type": "string", "minLength": 1, "maxLength": 512},
                "passphrase": {"type": "string", "maxLength": 256},
            },
            "required": ["backup_path"],
            "additionalProperties": False,
        },
    },
}


BACKUP_TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "backup_verify_archive": ("backup.verify",),
    "backup_create_archive": ("backup.create",),
    "backup_restore_archive": ("backup.restore", "backup.verify"),
}

PRIVILEGED_BACKUP_TOOLS = frozenset({
    "backup_create_archive",
    "backup_restore_archive",
})


class BackupMCPBridge:
    """Ponte MCP para o sistema de Backup e Restauração offline."""

    def __init__(
        self,
        manager: BackupManager | None = None,
        *,
        require_explicit_capabilities: bool = False,
    ):
        self.manager = manager or BackupManager()
        self.capability_guard = BridgeCapabilityGuard(
            "backup",
            BACKUP_TOOL_PERMISSIONS,
            privileged_tools=PRIVILEGED_BACKUP_TOOLS,
            strict_mode=require_explicit_capabilities,
        )

    def issue_mission_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: list[str] | set[str] | tuple[str, ...],
        permissions: list[str] | set[str] | tuple[str, ...] | None = None,
        ttl_seconds: float = 900.0,
        allow_privileged: bool = False,
    ) -> CapabilityToken:
        return self.capability_guard.issue_mission_capability(
            mission_id,
            allowed_tools=allowed_tools,
            permissions=permissions,
            ttl_seconds=ttl_seconds,
            allow_privileged=allow_privileged,
        )

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in sorted(BACKUP_MCP_TOOL_SCHEMAS.items())
        ]

    def validate_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        if name not in BACKUP_MCP_TOOL_SCHEMAS:
            raise PermissionError(f"Ferramenta MCP de Backup não autorizada: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos devem ser um objeto JSON.")
        self.capability_guard.check(name, arguments, grants=grants, mission_id=mission_id)
        _schema_check(BACKUP_MCP_TOOL_SCHEMAS[name]["inputSchema"], arguments)
        return arguments

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        args = self.validate_call(name, arguments, grants=grants, mission_id=mission_id)
        passphrase = args.get("passphrase")
        if name == "backup_verify_archive":
            res = self.manager.verify_backup(args["backup_path"], passphrase=passphrase)
            return {"tool": name, "status": "ok", "result": res}
        if name == "backup_create_archive":
            cipher = str(args.get("cipher", "AES-256-GCM"))
            res = self.manager.create_backup(
                args["destination_path"],
                include_knowledge_files=bool(args.get("include_knowledge_files", True)),
                cipher=cipher,
                passphrase=passphrase,
            )
            return {"tool": name, "status": "ok", "result": res}
        if name == "backup_restore_archive":
            res = self.manager.restore_backup(args["backup_path"], passphrase=passphrase)
            return {"tool": name, "status": "ok", "result": res}
        raise PermissionError(f"Ferramenta não suportada: {name}")

    def register_in_cognition_registry(self, registry: ToolRegistry | None = None) -> ToolRegistry:
        """Registra a verificação somente leitura de backups no ToolRegistry de cognição."""
        target = registry if registry is not None else ToolRegistry()
        output_schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "maxLength": 128},
                "status": {"type": "string", "maxLength": 64},
                "result": {"type": "object"},
            },
            "required": ["tool", "status", "result"],
            "additionalProperties": False,
        }
        spec = BACKUP_MCP_TOOL_SCHEMAS["backup_verify_archive"]
        contract = ToolContract(
            name="backup.verify_archive",
            description=spec["description"],
            input_schema=spec["inputSchema"],
            output_schema=output_schema,
            risk_level="READ_ONLY",
            timeout=10.0,
            permissions=("backup.verify",),
        )
        target.register(contract, lambda **kwargs: self.execute_tool("backup_verify_archive", kwargs))
        return target
