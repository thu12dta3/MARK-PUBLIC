
from __future__ import annotations
import json, logging, re
from .brain_direct import normalize

log = logging.getLogger("mark.brain")

def route(text: str, cfg: dict, model_call) -> dict:

    n = normalize(text)
    vision_terms = (
        "o que estou vendo", "o que estou olhando", "o que e isso", "o que e isto",
        "estou olhando", "que objeto", "que carro", "le o exercicio", "leia isso",
        "que cor e isso", "que cor e essa", "que cor e esta", "qual a cor disso",
        "o que esta acontecendo aqui", "o que esta acontecendo ai", "o que acontece aqui",
        "o que tem aqui", "o que tem nessa imagem", "o que tem nesta imagem",
        "voce consegue ver isso", "consegue ver isso", "da uma olhada nisso",
        "resolve essa questao", "na minha frente", "ao meu redor", "na tela",
        "estou segurando", "pela camera", "olhe isso", "observe isso", "descreva isso",
        "produto que estou vendo", "produto que estou olhando", "item que estou vendo",
        "item que estou olhando", "objeto que estou vendo", "objeto que estou olhando",
        "essa imagem", "esta imagem", "essa foto", "esta foto", "what am i looking at",
        "what am i seeing", "what do you see", "what is this", "what s this",
        "what color is this", "what colour is this", "what color is that",
        "what is happening here", "what s happening here", "what s going on here",
        "what is here", "can you see this", "can you see that",
        "what is in front of me", "around me", "look at this", "read this",
        "read the text", "describe this", "describe the image", "in this picture",
        "in this image", "through the camera", "product i m looking at", "item i m looking at",
        "object i m looking at", "product in front of me", "item in front of me",
    )
    web_terms = (
        "pesquise", "pesquisa", "pesquisar", "pesquisem", "procure na internet", "busque na internet",
        "pesquise na web", "procure na web", "noticias de hoje", "cotacao atual",
        "preco atual", "mais recente", "clima", "previsao do tempo", "previsao em",
        "previsao para", "tempo em", "tempo para", "como esta o tempo", "como fica o tempo",
        "vai chover", "chance de chuva", "temperatura em", "temperatura para",
        "documentacao oficial", "manual atualizado", "versao atual", "versao mais recente",
        "fonte oficial", "artigos recentes", "estudos recentes", "publicacoes recentes",
        "fontes confiaveis", "fontes oficiais", "quais as fontes", "quais sao as fontes",
        "me de fontes", "cite fontes", "referencias atuais", "busque referencias", "encontre fontes",
        "search online", "search the web", "look up online",
        "on the internet", "latest", "weather", "forecast", "will it rain",
        "news", "stock price", "exchange rate",
    )
    vision = any(x in n for x in vision_terms)
    web = cfg["web"].get("enabled", True) and any(x in n for x in web_terms)
    if (not web and 'sensor' in n
            and any(term in n for term in ('desconectad', 'nenhuma leitura', 'sem leitura'))):


        return {"vision": False, "web": False}
    current_markers = (" hoje", " atual", " atualmente", " mais recente", " recentes", " latest", " today", " current", " agora")
    information_terms = (" qual ", " quais ", " quanto ", " quantos ", " quantas ",
                         " quando ", " onde ", " como ", " existe ", " funciona ", " preco ", " versao ",
                         " population ", " version ", " price ")
    opinion_request = any(term in f" {n} " for term in (" opiniao ", " opinião ", " sua opiniao ", " seu palpite ", " your opinion ", " your take "))
    if (not web and not opinion_request and cfg["web"].get("enabled", True)
            and any(marker in f" {n} " for marker in current_markers)):
        web = any(term in f" {n} " for term in information_terms)
    if cfg["web"].get("enabled", True) and re.search(r"https?://[^\s<>]+", text, re.I):
        web = True
    if vision or web:
        return {"vision": vision, "web": web}
    if 'sensor' in n and any(term in n for term in ('desconectad','nenhuma leitura','sem leitura')):


        return {"vision":False,"web":False}



    social_checkins = (
        r"posso falar com voce(?: agora)?(?: por favor)?",
        r"posso conversar(?: com voce)?(?: agora)?(?: por favor)?",
        r"podemos falar(?: com voce)?(?: agora)?(?: por favor)?",
        r"podemos conversar(?: agora)?(?: por favor)?",
        r"can i talk to you(?: now)?(?: please)?",
        r"can we talk(?: now)?(?: please)?",
        r"are you there(?: now)?",
        r"are you available(?: now)?",
    )
    if any(re.fullmatch(pattern, n) for pattern in social_checkins):
        return {"vision": False, "web": False}


    ambiguous_tool_hints = (
        " camera", " camera ", " imagem", " foto", " picture", " image", " visual",
        " internet", " web", " look", " see", " current", " recent", " price", " news",
        " weather", " hoje", " atual", " agora", " today", " current", " right now",
        " noticia", " preco", " cotacao", " clima",
        " fonte", " fontes ", " referencia ", " referencias ", " citation", " evidence ",
        " tela", " olhando", " olha", " olhe", " observe", " ver ", " what is happening",
        " que cor", " qual a cor", " color is this", " colour is this", " happening here",
        " going on here", " o que tem aqui", " consegue ver", " can you see", " s this",
    )
    if not any(hint in f" {n} " for hint in ambiguous_tool_hints):
        return {"vision": False, "web": False}

    prompt = ("Classifique a intenção. Retorne JSON com booleans vision e web. "
              "vision: pede câmera/cena visível. web: pesquisa ou informação atual. Não responda ao pedido.\n" + text)
    retry_prompt = ("Classifique a solicitação sem responder ao usuário. Responda somente JSON válido com chaves booleanas vision e web. "
                    "vision=true apenas para observar algo visível ou resolver item que o usuário está olhando; "
                    "web=true para pesquisa explícita ou informação atual.\nSolicitação: " + text)
    try:
        result = model_call(prompt, cfg, "Classificador. Apenas JSON.", num_predict=512, output_format="json")
        data = _tool_flags(result)
        if data is not None:
            return data
    except RuntimeError as exc:


        if "não retornou uma resposta final" not in str(exc):
            log.debug("Roteador remoto indisponível (%s); usando fallback local.", type(exc).__name__)
            return _local_tool_fallback(n, cfg["web"].get("enabled", True))
    except Exception as exc:
        log.debug("Roteador remoto indisponível (%s); usando fallback local.", type(exc).__name__)
        return _local_tool_fallback(n, cfg["web"].get("enabled", True))

    try:
        result = model_call(retry_prompt, cfg, "Classificador. Não responda ao usuário, apenas classifique.", output_format="json")
        data = _tool_flags(result)
        if data is not None:
            return data
    except Exception as exc:
        log.debug("Roteador remoto indisponível após nova tentativa (%s); usando fallback local.", type(exc).__name__)
    return _local_tool_fallback(n, cfg["web"].get("enabled", True))


def _tool_flags(result: str) -> dict | None:
    from .cognition.resilience import tolerant_json_parse

    data = tolerant_json_parse(result)
    if isinstance(data, dict):
        v = data.get("vision")
        w = data.get("web")
        if isinstance(v, bool) and isinstance(w, bool):
            return {"vision": v, "web": w}
        if "vision" in data or "web" in data:
            return {"vision": bool(v), "web": bool(w)}
    return None


def _local_tool_fallback(n: str, web_enabled: bool = True) -> dict:



    visual_fallback_terms = (
        "camera", "imagem", "foto", "picture", "image", "what do you see",
        "what am i looking at", "what am i seeing", "what s this", "what color is this",
        "what colour is this", "what is happening here", "what s happening here",
        "what s going on here", "what is here", "can you see", "que cor",
        "qual a cor", "o que esta acontecendo aqui", "o que esta acontecendo ai",
        "o que acontece aqui", "o que tem aqui", "voce consegue ver", "consegue ver",
        "olha isso", "olha ai", "olhe isso", "da uma olhada", "observe isso",
        "estou olhando", "estou vendo", "na minha frente", "ao meu redor",
    )
    web_fallback_terms = (
        "pesquise", "pesquisa", "procure na internet", "busque na internet",
        "pesquise na web", "procure na web", "na internet", "na web",
        "cotacao", "preco", "clima", "previsao", "tempo em", "tempo para",
        "como esta o tempo", "como fica o tempo", "vai chover", "chance de chuva",
        "temperatura atual", "temperatura em", "temperatura para", "umidade em",
        "weather", "forecast", "will it rain", "news", "noticia", "latest",
        "stock price", "exchange rate",
    )
    return {
        "vision": any(term in n for term in visual_fallback_terms),
        "web": web_enabled and any(term in n for term in web_fallback_terms),
    }


from .cognition.brain_routing import (
    DynamicBrainRouter,
    EmptyResponseError,
    FallbackChainExhaustedError,
    LocalInferenceUnavailableError,
    PrivacyLevel,
    PrivacyPolicyViolationError,
    ProviderType,
    RoutingDecision,
    detect_sensitive_payload,
    get_brain_router,
    route_request,
    route_with_gods_eye_mcp,
)
from .cognition.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from .mcp_gods_eye import GodsEyeMCPBridge
