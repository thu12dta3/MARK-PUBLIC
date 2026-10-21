import os

import pytest

ISOLATION_SUITES = (
    "test_missions",
    "test_library",
    "test_library_formats",
    "test_coding_verify",
)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CI") != "true":
        return
    skip_isolation = pytest.mark.skip(
        reason="bubblewrap/systemd-run não disponível no GitHub Actions"
    )
    for item in items:
        if any(suite in item.nodeid for suite in ISOLATION_SUITES):
            item.add_marker(skip_isolation)


# Suítes de UI que mockam `_request` e esperam rota local (Ollama). Em runners
# de CI não há Ollama em 127.0.0.1:11434, então o circuit breaker abriria e o
# roteador cairia na rota de nuvem (exigindo confirmação do usuário), abortando
# o envio antes de qualquer requisição. Aqui o provedor local é tratado como
# disponível e um modelo local instalado, tornando o resultado independente do ambiente.
LOCAL_ROUTE_SUITES = ("test_desktop_security", "test_e2e_user_flows")


@pytest.fixture(autouse=True)
def _assume_local_provider_available(request, monkeypatch):
    if not any(suite in request.node.nodeid for suite in LOCAL_ROUTE_SUITES):
        yield
        return
    from mark.cognition.circuit_breaker import CircuitBreaker
    from mark.cognition.provider_adapter import OllamaProviderAdapter

    monkeypatch.setattr(CircuitBreaker, "can_execute", lambda self: True)
    # O seletor de modelos descobre modelos instalados no Ollama; sem daemon ele
    # fica sem perfil "mark" (data=None). Fixa um modelo local determinístico.
    monkeypatch.setattr(
        OllamaProviderAdapter,
        "get_installed_models",
        classmethod(lambda cls, *a, **k: ["qwen2.5-coder:32b"]),
    )
    yield
