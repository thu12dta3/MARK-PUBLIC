
from __future__ import annotations

import re
import webbrowser
from dataclasses import dataclass
from urllib.parse import urlsplit

from .gods_eye_view import GodsEyeManager, GodseyeError
from .gods_eye_view import _PORTS


@dataclass(frozen=True)
class GodseyeIntent:
    action: str
    place: str | None = None
    layers: tuple[str, ...] = ()
    style: str | None = None


_SUBJECT = re.compile(r"\b(globo|terra|planeta|olho de deus|god['’]?s eye(?: view)?|visualiza(?:dor|ção))\b", re.I)
_VERB = re.compile(r"\b(abre|abra|inicia|inicie|mostra|mostre|exibe|exiba|fecha|feche|encerra|encerre|desliga|desligue)\b", re.I)
_PLACE = re.compile(
    r"\b(?:mostra|mostre|leva|navega|aproxima(?:-se)?|centraliza)\s+"
    r"(?:a cidade de\s+|a região de\s+|o mapa em\s+)?(.+?)\s+"
    r"(?:no globo|no mapa|e os avi[oõ]es|com os avi[oõ]es|na terra)\b", re.I)
_PLACE_PLUS = re.compile(
    r"\b(?:mostra|mostre|leva|navega|aproxima(?:-se)?|centraliza)\s+(.+?)\s+"
    r"(?:e|com)\s+(?:os?\s+)?(?:avi[oõ]es|voos|aeronaves|sat[eé]lites|terremotos)\b", re.I)
_PLACE_PREFIX = re.compile(r"\b(?:mostra|mostre|leva|navega|aproxima(?:-se)?|centraliza)\s+(.+?)\s+(?:no globo|no mapa|na terra)\b", re.I)
_BAD_PLACE = re.compile(r"^(?:o globo|a terra|o mapa|olho de deus|a visualiza(?:ção|dor))$", re.I)


def _safe_visualizer_url(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "http" and parts.hostname == "127.0.0.1" and port in _PORTS
            and not parts.username and not parts.password)


def parse_godseye_intent(text: str) -> GodseyeIntent | None:
    if not isinstance(text, str) or not 0 < len(text) <= 500:
        return None
    if not _SUBJECT.search(text) and not re.search(r"\b(ativa|ative|desativa|desative|aproxima|aproxime|afasta|afaste|gira|gire|rotacione)\s+(?:a\s+)?(?:vis[aã]o|c[aâ]mera|camada|os? avi[oõ]es|voos|sat[eé]lites)\b", text, re.I):
        return None
    verbs = _VERB.findall(text)
    if verbs and verbs[-1].casefold() in {"fecha", "feche", "encerra", "encerre", "desliga", "desligue"}:
        return GodseyeIntent("stop")

    normalized = text.casefold()
    if re.search(r"\b(gir[ae]|rotacion[ae]|gire|rotate)\b", normalized):
        return GodseyeIntent("unsupported")
    if re.search(r"\b(aproxim[ae]|afast[ae]|zoom|aproxima(?:-se)?)\b", normalized) and re.search(r"\b(c[aâ]mera|zoom)\b", normalized):
        return GodseyeIntent("unsupported")
    layers: list[str] = []
    if re.search(r"\b(avi[oõ]es|voos|aeronaves)\b", normalized):
        layers.append("flights")
    if re.search(r"\bterremotos?\b", normalized):
        layers.append("earthquakes")
    if re.search(r"\bsat[eé]lites?\b", normalized):
        layers.append("satellites")

    style = None
    if re.search(r"\b(vis[aã]o )?noturna\b|\bnvg\b", normalized):
        style = "surveillance"
    elif re.search(r"\b(vis[aã]o )?t[eé]rmica\b|\bflir\b", normalized):
        style = "thermal"
    elif re.search(r"\bnormal\b", normalized):
        style = "normal"

    match = _PLACE.search(text) or _PLACE_PLUS.search(text) or _PLACE_PREFIX.search(text)
    place = match.group(1).strip(" ,.!?\"'") if match else None
    if place and (len(place) > 160 or _BAD_PLACE.fullmatch(place)):
        place = None
    if not place and not layers and not style and not _VERB.search(text):
        return None
    return GodseyeIntent("show", place, tuple(layers), style)


def handle_godseye_intent(text: str, manager: GodsEyeManager, *, open_browser: bool = True,
                          browser_open=None) -> str | None:
    intent = parse_godseye_intent(text)
    if intent is None:
        return None
    if intent.action == "stop":
        was_running = manager.running
        manager.stop()
        if was_running:
            return "O serviço local do God's Eye View iniciado pelo MARK foi encerrado. Feche a aba do navegador manualmente; o MARK não fecha abas de terceiros."
        return "O serviço do God's Eye View não estava iniciado por esta sessão do MARK."
    if intent.action == "unsupported":
        return "O God's Eye View atual não oferece controle conversacional confiável de rotação ou zoom da visualização já aberta. Posso abrir uma nova visualização de um lugar, com camadas e estilo suportados."
    try:
        manager.start()
        if not intent.place and not intent.layers and not intent.style:
            args = {"camera": {"lat": 0, "lon": 0, "altitude_m": 15_000_000, "pitch_deg": -90}}
        else:
            args = {"camera": {"lat": 0, "lon": 0, "altitude_m": 15_000_000, "pitch_deg": -90}}
            if intent.place:
                args = {"area": {"place": intent.place}}
        if intent.layers:
            args["layers"] = list(intent.layers)
        if intent.style:
            args["style"] = intent.style
        result = manager.call_tool("show_in_gods_eye_view", args, timeout=30)
    except (GodseyeError, OSError, ValueError, PermissionError) as exc:
        return f"Não consegui abrir essa visualização: {str(exc)[:240]}"
    data = result.get("structuredContent", {})
    url = data.get("url") if isinstance(data, dict) else None
    if not _safe_visualizer_url(url):
        return "O servidor não retornou um endereço local verificável para a visualização."
    opened = False
    if open_browser:
        try:
            browser_open = browser_open or webbrowser.open
            opened = browser_open(url, new=2)
        except Exception:
            opened = False
    place = intent.place or "visão global"
    extras = []
    if intent.layers:
        extras.append("camada(s): " + ", ".join(intent.layers))
    if intent.style:
        extras.append("estilo: " + intent.style)
    details = (" · " + "; ".join(extras)) if extras else ""
    if opened:
        return f"Solicitei ao navegador a abertura do God's Eye View em {place}{details}. Se não aparecer, use o link local: {url}"
    return f"A visualização do God's Eye View está pronta em {place}{details}. Abra o link local: {url}"
