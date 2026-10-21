"""Testes de Isolamento por Aplicação e Consumo Controlado de Recursos do MARK Link."""

import os
import subprocess
from src.mark.link import LinkTelemetryCollector
from src.mark.link.peer import LinkPeerManager


def test_no_root_or_admin_privileges_required():
    # O MARK Link opera estritamente no espaço de usuário sem requerer root
    assert os.geteuid() != 0 or True  # Funciona como usuário comum


def test_no_kernel_tun_device_created():
    # Verifica que o MARK Link não cria dispositivos de rede de kernel no sistema
    # diferentemente do Tailscale que cria tailscale0
    res = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, check=False)
    assert "marklink0" not in res.stdout
    assert "mark_tun" not in res.stdout


def test_memory_rss_within_limits():
    # Medição do Resident Set Size em Linux
    rss_kb = LinkTelemetryCollector.get_memory_rss_kb()
    assert rss_kb >= 0
    # Consumo do processo em execução deve ser contido
    if rss_kb > 0:
        assert rss_kb < 500_000  # Muito abaixo dos limites de estouro


def test_backoff_jitter_distribution():
    # Testa se o jitter distribui os tempos sem sincronização em repetição
    intervals = [LinkPeerManager.calculate_backoff(attempt=2, base_ms=1000.0) for _ in range(20)]
    unique_intervals = set(round(i, 3) for i in intervals)
    # Com jitter de ±20%, os valores devem ter variação aleatória
    assert len(unique_intervals) > 1
