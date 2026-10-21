import mark_core
from mark.cognition.brain_routing import route_request


def test_route_request_local():
    result = route_request("Qual a previsão do tempo?")
    assert result == "LOCAL_EXECUTION"


def test_route_request_cloud_fallback():
    result = route_request("Pesquise na web as últimas notícias e analise os dados financeiros")
    assert result == "CLOUD_FALLBACK_TRIGGERED"


def test_rust_analyze_routing_keywords():
    assert mark_core.analyze_routing("Analise o código deste módulo") == "CLOUD"
    assert mark_core.analyze_routing("Resuma o documento em PDF") == "CLOUD"
    assert mark_core.analyze_routing("Este é um problema complexo") == "CLOUD"
    assert mark_core.analyze_routing("Olá, tudo bem?") == "LOCAL"
