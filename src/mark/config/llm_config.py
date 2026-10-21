"""Gerenciador de Configuração de LLM e Cofre Criptografado de Chaves do MARK.

Carrega configurações de modelos e provedores através de:
1. Variáveis de ambiente e arquivos .env
2. Arquivo de configuração `config.toml`
3. Cofre privado cifrado em AES-256-GCM com permissões estritas POSIX 0600
4. Modificações em tempo de execução via interface/código

Totalmente integrado ao `logging_sanitizer.py` para prevenção absoluta de vazamento de chaves.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping

try:
    import mark_core
except ImportError:
    mark_core = None

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..logging_sanitizer import register_secret_for_redaction
from ..storage_security import (
    StorageSecurityError,
    atomic_create_temp_file,
    ensure_private_dir,
    private_file,
    verify_private_path,
)

logger = logging.getLogger("mark.config.llm_config")

# Identificador de dados adicionais autenticados (AAD) para o cofre
VAULT_AAD = b"MARK-LLM-VAULT-V1"
DEFAULT_VAULT_DIR = Path.home() / ".local/state/mark/vault"
DEFAULT_VAULT_FILE = DEFAULT_VAULT_DIR / "llm_keys.enc"
DEFAULT_MASTER_KEY_FILE = DEFAULT_VAULT_DIR / ".vault_master.key"


# ============================================================================
# Cofre Privado de Chaves Criptografadas (AES-256-GCM + POSIX 0600)
# ============================================================================


class LLMKeyVault:
    """Cofre local seguro de chaves de API com criptografia autenticada AES-256-GCM."""

    def __init__(
        self,
        vault_file: Path | str | None = None,
        master_key_file: Path | str | None = None,
        master_key_bytes: bytes | None = None,
    ) -> None:
        self.vault_file = Path(vault_file) if vault_file is not None else Path(os.environ.get("MARK_VAULT_PATH", DEFAULT_VAULT_FILE))
        self.master_key_file = Path(master_key_file) if master_key_file is not None else Path(os.environ.get("MARK_MASTER_KEY_FILE", DEFAULT_MASTER_KEY_FILE))
        self._master_key_override = master_key_bytes
        self._cache: dict[str, Any] | None = None

    def _get_or_create_master_key(self) -> bytes:
        """Obtém ou gera uma chave mestra de 256 bits protegida com permissões 0600."""
        if self._master_key_override:
            return self._master_key_override

        env_secret = os.environ.get("MARK_MASTER_KEY") or os.environ.get("MARK_VAULT_KEY")
        if env_secret:
            # Deriva chave de 32 bytes via PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=SHA256(),
                length=32,
                salt=b"MARK_VAULT_STATIC_SALT_V1",
                iterations=100_000,
            )
            return kdf.derive(env_secret.encode("utf-8"))

        ensure_private_dir(self.master_key_file.parent)
        if self.master_key_file.exists():
            verify_private_path(self.master_key_file, is_dir=False)
            raw = self.master_key_file.read_bytes()
            if len(raw) == 32:
                return raw

        new_key = secrets.token_bytes(32)
        tmp = atomic_create_temp_file(dir_path=self.master_key_file.parent, prefix=".master_key_")
        tmp.write_bytes(new_key)
        tmp.chmod(0o600)
        os.replace(tmp, self.master_key_file)
        self.master_key_file.chmod(0o600)
        return new_key

    def _read_vault(self) -> dict[str, Any]:
        """Lê e decifra o cofre do disco, validando tag e permissões POSIX."""
        if not self.vault_file.exists():
            return {}

        verify_private_path(self.vault_file, is_dir=False)
        raw_payload = self.vault_file.read_bytes()
        if len(raw_payload) < 28:  # 12 bytes nonce + 16 bytes auth tag mínima
            raise StorageSecurityError("Arquivo do cofre corrompido ou truncado.")

        master_key = self._get_or_create_master_key()

        # Suporte a schema JSON multi-dispositivo (Desktop, Android, Wear OS) ou binário legado
        stripped = raw_payload.strip()
        if stripped.startswith(b"{") and stripped.endswith(b"}"):
            try:
                payload_str = raw_payload.decode("utf-8")
                if mark_core is not None and mark_core.validate_vault_payload(payload_str):
                    plaintext_bytes = mark_core.decrypt_aes_256_gcm(master_key, payload_str, VAULT_AAD)
                    data = json.loads(plaintext_bytes.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise StorageSecurityError("Conteúdo do cofre inválido.")
                    return data
                else:
                    payload_dict = json.loads(payload_str)
                    nonce = base64.b64decode(payload_dict["nonce"])
                    ciphertext = base64.b64decode(payload_dict["ciphertext"])
                    aesgcm = AESGCM(master_key)
                    plaintext = aesgcm.decrypt(nonce, ciphertext, VAULT_AAD)
                    data = json.loads(plaintext.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise StorageSecurityError("Conteúdo do cofre inválido.")
                    return data
            except Exception as exc:
                raise StorageSecurityError(
                    f"Falha na autenticação do cofre: dados adulterados ou chave incorreta ({exc})."
                ) from exc

        nonce = raw_payload[:12]
        ciphertext = raw_payload[12:]

        try:
            aesgcm = AESGCM(master_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, VAULT_AAD)
            data = json.loads(plaintext.decode("utf-8"))
            if not isinstance(data, dict):
                raise StorageSecurityError("Conteúdo do cofre inválido.")
            return data
        except Exception as exc:
            raise StorageSecurityError(f"Falha na autenticação do cofre: dados adulterados ou chave incorreta ({exc}).") from exc

    def _write_vault(self, data: dict[str, Any]) -> None:
        """Cifra em AES-256-GCM e grava atomicamente com permissão estrita 0600."""
        ensure_private_dir(self.vault_file.parent)
        master_key = self._get_or_create_master_key()
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")

        if mark_core is not None:
            payload_str = mark_core.encrypt_aes_256_gcm(master_key, plaintext, VAULT_AAD)
            payload = payload_str.encode("utf-8")
        else:
            nonce = os.urandom(12)
            aesgcm = AESGCM(master_key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, VAULT_AAD)
            payload = nonce + ciphertext

        tmp = atomic_create_temp_file(dir_path=self.vault_file.parent, prefix=".vault_tmp_")
        tmp.write_bytes(payload)
        tmp.chmod(0o600)
        os.replace(tmp, self.vault_file)
        self.vault_file.chmod(0o600)
        verify_private_path(self.vault_file, is_dir=False)

    def set_key(self, provider: str, api_key: str, metadata: dict[str, Any] | None = None) -> None:
        """Armazena uma chave de API cifrada no cofre para o provedor especificado."""
        norm_provider = provider.strip().lower()
        secret = api_key.strip()
        if not secret:
            raise ValueError("Chave de API não pode ser vazia.")

        # Registra imediatamente no higienizador global para evitar qualquer vazamento
        register_secret_for_redaction(secret)

        data = self._read_vault()
        data[norm_provider] = {
            "key": secret,
            "metadata": metadata or {},
        }
        self._write_vault(data)
        self._cache = data

    def get_key(self, provider: str) -> str | None:
        """Recupera e decifra a chave de API de um provedor do cofre."""
        norm_provider = provider.strip().lower()
        data = self._read_vault()
        entry = data.get(norm_provider)
        if isinstance(entry, dict):
            key = entry.get("key")
            if isinstance(key, str) and key:
                register_secret_for_redaction(key)
                return key
        elif isinstance(entry, str) and entry:
            register_secret_for_redaction(entry)
            return entry
        return None

    def delete_key(self, provider: str) -> bool:
        """Remove a chave de API de um provedor do cofre."""
        norm_provider = provider.strip().lower()
        data = self._read_vault()
        if norm_provider in data:
            del data[norm_provider]
            self._write_vault(data)
            self._cache = data
            return True
        return False

    def list_providers(self) -> list[str]:
        """Lista os nomes de provedores cadastrados sem expor as chaves."""
        data = self._read_vault()
        return sorted(data.keys())


# ============================================================================
# Perfis e Configuração
# ============================================================================


@dataclass
class ProviderProfile:
    """Perfil de configuração para um provedor específico."""

    name: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 60.0
    extra_params: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        masked_key = f"{self.api_key[:4]}...[REDACTED]" if (self.api_key and len(self.api_key) > 6) else "[NONE]"
        return f"ProviderProfile(name='{self.name}', model='{self.model}', api_key='{masked_key}', base_url='{self.base_url}')"

    def to_dict(self, safe: bool = True) -> dict[str, Any]:
        d = {
            "name": self.name,
            "model": self.model,
            "api_key": "[REDACTED]" if (safe and self.api_key) else self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "extra_params": self.extra_params,
        }
        return d


@dataclass
class AgentProfileConfig:
    """Perfil de modelo e cadeia de fallback para um agente especializado do MARK."""

    agent_name: str
    primary_provider: str
    primary_model: str
    fallback_chain: list[tuple[str, str]] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = 2048

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "primary_provider": self.primary_provider,
            "primary_model": self.primary_model,
            "fallback_chain": self.fallback_chain,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


# Mapeamento padrão de variáveis de ambiente para provedores
ENV_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "azure-openai": "AZURE_OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


class LLMConfig:
    """Gerenciador central de configuração de provedores de LLM e perfis de agentes."""

    def __init__(
        self,
        vault: LLMKeyVault | None = None,
        config_path: Path | str | None = None,
        autoload: bool = True,
    ) -> None:
        self.vault = vault or LLMKeyVault()
        self.config_path = Path(config_path) if config_path else Path("config.toml")
        self.default_provider = "ollama"
        self.default_model = "haervwe/qwen3-vl-4b-heretic:latest"
        self.providers: dict[str, ProviderProfile] = {}
        self.agents: dict[str, AgentProfileConfig] = {}

        self._init_defaults()
        if autoload:
            self.load()

    def _init_defaults(self) -> None:
        """Inicializa os perfis padrão para provedores conhecidos e agentes do MARK."""
        self.providers = {
            "ollama": ProviderProfile("ollama", "haervwe/qwen3-vl-4b-heretic:latest", base_url="http://127.0.0.1:11434"),
            "openai": ProviderProfile("openai", "gpt-4o", base_url="https://api.openai.com/v1"),
            "anthropic": ProviderProfile("anthropic", "claude-3-5-sonnet-20241022", base_url="https://api.anthropic.com/v1"),
            "gemini": ProviderProfile("gemini", "gemini-2.0-flash", base_url="https://generativelanguage.googleapis.com/v1beta"),
            "groq": ProviderProfile("groq", "llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1"),
            "openrouter": ProviderProfile("openrouter", "openrouter/auto", base_url="https://openrouter.ai/api/v1"),
            "deepseek": ProviderProfile("deepseek", "deepseek-chat", base_url="https://api.deepseek.com"),
            "mistral": ProviderProfile("mistral", "mistral-large-latest", base_url="https://api.mistral.ai/v1"),
            "local": ProviderProfile("local", "haervwe/qwen3-vl-4b-heretic:latest", base_url="http://127.0.0.1:11434/v1"),
        }

        # Perfis padrão dos agentes
        self.agents = {
            "EngineerAgent": AgentProfileConfig(
                agent_name="EngineerAgent",
                primary_provider="anthropic",
                primary_model="claude-3-5-sonnet-20241022",
                fallback_chain=[
                    ("openai", "gpt-4o"),
                    ("deepseek", "deepseek-chat"),
                    ("groq", "llama-3.3-70b-versatile"),
                    ("ollama", "R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m"),
                ],
                temperature=0.2,
                max_tokens=4096,
            ),
            "SentinelAgent": AgentProfileConfig(
                agent_name="SentinelAgent",
                primary_provider="ollama",
                primary_model="haervwe/qwen3-vl-4b-heretic:latest",
                fallback_chain=[
                    ("ollama", "qwen2.5:3b"),
                    ("groq", "llama-3.1-8b-instant"),
                ],
                temperature=0.1,
                max_tokens=1024,
            ),
            "ChatAgent": AgentProfileConfig(
                agent_name="ChatAgent",
                primary_provider="ollama",
                primary_model="haervwe/qwen3-vl-4b-heretic:latest",
                fallback_chain=[
                    ("gemini", "gemini-2.0-flash"),
                    ("groq", "llama-3.3-70b-versatile"),
                    ("ollama", "haervwe/qwen3-vl-4b-heretic:latest"),
                ],
                temperature=0.7,
                max_tokens=2048,
            ),
            "ProfessorAgent": AgentProfileConfig(
                agent_name="ProfessorAgent",
                primary_provider="gemini",
                primary_model="gemini-2.0-flash",
                fallback_chain=[
                    ("openai", "gpt-4o"),
                    ("anthropic", "claude-3-5-sonnet-20241022"),
                    ("ollama", "haervwe/qwen3-vl-4b-heretic:latest"),
                ],
                temperature=0.5,
                max_tokens=4096,
            ),
        }

    def load(self) -> LLMConfig:
        """Carrega a configuração aplicando a ordem de precedência: Defaults < TOML < Vault < Env/Runtime."""
        self._load_from_toml()
        self._load_from_vault()
        self._load_from_env()
        return self

    def _load_from_toml(self) -> None:
        """Lê o arquivo config.toml se ele existir."""
        candidates = [self.config_path, Path.home() / ".config/mark/config.toml"]
        target = next((p for p in candidates if p.is_file()), None)
        if not target:
            return

        try:
            content = target.read_text(encoding="utf-8")
            data = tomllib.loads(content)
        except Exception as exc:
            logger.warning("Falha ao interpretar config.toml (%s): %s", target, exc)
            return

        llm_sec = data.get("llm", {})
        if not isinstance(llm_sec, dict):
            return

        if "default_provider" in llm_sec:
            self.default_provider = str(llm_sec["default_provider"])
        if "default_model" in llm_sec:
            self.default_model = str(llm_sec["default_model"])

        # Provedores
        prov_sec = llm_sec.get("providers", {})
        if isinstance(prov_sec, dict):
            for name, conf in prov_sec.items():
                if isinstance(conf, dict):
                    norm_name = name.lower()
                    existing = self.providers.get(norm_name)
                    model = conf.get("model") or (existing.model if existing else "")
                    base_url = conf.get("base_url") or (existing.base_url if existing else None)
                    api_key = conf.get("api_key")
                    timeout = float(conf.get("timeout", existing.timeout if existing else 60.0))

                    if api_key:
                        register_secret_for_redaction(api_key)

                    self.providers[norm_name] = ProviderProfile(
                        name=norm_name,
                        model=model,
                        api_key=api_key or (existing.api_key if existing else None),
                        base_url=base_url,
                        timeout=timeout,
                    )

        # Agentes
        agents_sec = llm_sec.get("agents", {})
        if isinstance(agents_sec, dict):
            for agent_name, conf in agents_sec.items():
                if isinstance(conf, dict):
                    primary_prov = conf.get("provider", "ollama")
                    primary_mod = conf.get("model", self.default_model)
                    raw_fb = conf.get("fallback", [])
                    fallback_chain = [(str(p), str(m)) for p, m in raw_fb if isinstance(p, (str, bytes)) and isinstance(m, (str, bytes))]

                    self.agents[agent_name] = AgentProfileConfig(
                        agent_name=agent_name,
                        primary_provider=primary_prov,
                        primary_model=primary_mod,
                        fallback_chain=fallback_chain,
                        temperature=float(conf.get("temperature", 0.7)),
                        max_tokens=conf.get("max_tokens", 2048),
                    )

    def _load_from_vault(self) -> None:
        """Carrega chaves criptografadas salvas no cofre privado."""
        try:
            stored_providers = self.vault.list_providers()
            for p_name in stored_providers:
                key = self.vault.get_key(p_name)
                if key:
                    register_secret_for_redaction(key)
                    if p_name in self.providers:
                        self.providers[p_name].api_key = key
                    else:
                        self.providers[p_name] = ProviderProfile(
                            name=p_name,
                            model="",
                            api_key=key,
                        )
        except Exception as exc:
            logger.warning("Falha ao ler chaves do cofre privado: %s", exc)

    def _load_from_env(self) -> None:
        """Carrega configurações e chaves de variáveis de ambiente e arquivos .env."""
        # Carrega .env se presente
        try:
            import dotenv

            dotenv.load_dotenv(override=False)
        except Exception:
            pass

        if "MARK_LLM_PROVIDER" in os.environ:
            self.default_provider = os.environ["MARK_LLM_PROVIDER"].strip()
        if "MARK_LLM_MODEL" in os.environ:
            self.default_model = os.environ["MARK_LLM_MODEL"].strip()

        for provider_name, env_var in ENV_KEY_MAP.items():
            key_val = os.environ.get(env_var, "").strip()
            if key_val:
                register_secret_for_redaction(key_val)
                if provider_name in self.providers:
                    self.providers[provider_name].api_key = key_val
                else:
                    self.providers[provider_name] = ProviderProfile(
                        name=provider_name,
                        model="",
                        api_key=key_val,
                    )

        # URLs específicas por ambiente
        if "MARK_OLLAMA_URL" in os.environ:
            self.set_provider_url("ollama", os.environ["MARK_OLLAMA_URL"])
            self.set_provider_url("local", os.environ["MARK_OLLAMA_URL"])
        if "AZURE_OPENAI_ENDPOINT" in os.environ:
            self.set_provider_url("azure", os.environ["AZURE_OPENAI_ENDPOINT"])
            self.set_provider_url("azure-openai", os.environ["AZURE_OPENAI_ENDPOINT"])

    def get_provider(self, name: str) -> ProviderProfile:
        """Obtém o perfil de configuração do provedor ou gera um novo vazio."""
        norm = name.strip().lower()
        if norm not in self.providers:
            self.providers[norm] = ProviderProfile(name=norm, model=self.default_model)
        return self.providers[norm]

    def get_available_providers(self) -> dict[str, ProviderProfile]:
        """Verifica a presenca efetiva e validade de API Keys configuradas nas variaveis de ambiente,

        .env ou no cofre LLMKeyVault para cada provedor em nuvem. Retorna apenas os provedores
        cujas chaves estejam devidamente preenchidas.
        """
        self._load_from_vault()
        self._load_from_env()

        cloud_env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "google": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "azure": "AZURE_OPENAI_API_KEY",
            "azure-openai": "AZURE_OPENAI_API_KEY",
        }

        available: dict[str, ProviderProfile] = {}
        for prov_name, profile in self.providers.items():
            norm_name = prov_name.strip().lower()
            if norm_name in {"ollama", "local", "vllm", "lmstudio"}:
                continue

            key = profile.api_key
            if not key:
                try:
                    key = self.vault.get_key(norm_name)
                except Exception:
                    key = None
            if not key and norm_name in cloud_env_map:
                env_var = cloud_env_map[norm_name]
                env_val = os.environ.get(env_var, "").strip()
                if env_val:
                    key = env_val

            if key and isinstance(key, str) and key.strip():
                clean_key = key.strip()
                register_secret_for_redaction(clean_key)
                profile.api_key = clean_key
                available[norm_name] = profile

        return available

    def set_provider_key(self, name: str, key: str, save_to_vault: bool = True) -> None:
        """Registra chave de API para o provedor, opcionalmente persistindo de forma cifrada no cofre."""
        norm = name.strip().lower()
        cleaned = key.strip()
        register_secret_for_redaction(cleaned)

        profile = self.get_provider(norm)
        profile.api_key = cleaned

        if save_to_vault:
            self.vault.set_key(norm, cleaned)

    def set_provider_url(self, name: str, url: str) -> None:
        """Define o URL base para um provedor."""
        norm = name.strip().lower()
        profile = self.get_provider(norm)
        profile.base_url = url.strip()

    def get_agent_profile(self, agent_name: str) -> AgentProfileConfig:
        """Obtém o perfil configurado para o agente especificado."""
        if agent_name not in self.agents:
            self.agents[agent_name] = AgentProfileConfig(
                agent_name=agent_name,
                primary_provider=self.default_provider,
                primary_model=self.default_model,
                fallback_chain=[(self.default_provider, self.default_model)],
            )
        return self.agents[agent_name]

    def set_agent_profile(
        self,
        agent_name: str,
        primary_provider: str,
        primary_model: str,
        fallback_chain: list[tuple[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = 2048,
    ) -> None:
        """Define ou atualiza a preferência de modelo e cadeia de fallback para um agente."""
        self.agents[agent_name] = AgentProfileConfig(
            agent_name=agent_name,
            primary_provider=primary_provider.strip().lower(),
            primary_model=primary_model.strip(),
            fallback_chain=fallback_chain or [],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def __repr__(self) -> str:
        return f"LLMConfig(default_provider='{self.default_provider}', providers={list(self.providers.keys())}, agents={list(self.agents.keys())})"

    def to_dict(self, safe: bool = True) -> dict[str, Any]:
        """Exporta configuração como dicionário seguro, sanitizando chaves confidenciais."""
        return {
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "providers": {k: v.to_dict(safe=safe) for k, v in self.providers.items()},
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
        }


def get_llm_config() -> LLMConfig:
    """Função de conveniência para obter a instância carregada de LLMConfig."""
    return LLMConfig()
