
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
import base64
import copy
import json
import logging
import re
import time
import urllib.request
from ..vision import image_base64
from ..vision_quality import prepare_frame
from ..conversation_context import validated_history
from ..core.context import CoreContext
from ..url_security import validate_url_scheme
from .integration import is_garbage_ref_title

log = logging.getLogger('mark.brain')

_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)

_REF_BLOCK_RE = re.compile(r"\s*\(\s*ref\.:\s*([^)]*)\)", flags=re.IGNORECASE)


def _clean_ref_block(match: re.Match) -> str:
    raw = match.group(1).strip()
    if not raw:
        return ""
    items = [item.strip() for item in raw.split(";") if item.strip()]
    valid_items = []
    for item in items:
        title_part = re.sub(r",\s*p\.\s*\d+\s*$", "", item).strip()
        if not is_garbage_ref_title(title_part):
            valid_items.append(item)
    if not valid_items:
        return ""
    return " (ref.: " + "; ".join(valid_items) + ")"


def sanitize_chat_output(text: str) -> str:
    """Remove emojis e blocos de referências vazias/corrompidas (ex: ref.: TOP80E587...) de respostas do chat."""
    if not isinstance(text, str) or not text:
        return ""
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = _REF_BLOCK_RE.sub(_clean_ref_block, cleaned)
    # Remove referências truncadas ou sem parênteses no final (ex: "(ref.: TOP80E587..." ou "ref.: TOP80E587...")
    cleaned = re.sub(
        r"\s*\(?\s*ref\.:\s*(?:[A-Z0-9_]{6,}|[0-9a-fA-F_]{6,})[^)\n]*\)?$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Limpa espaços duplos deixados pela remoção de emojis na mesma linha, preservando quebras de linha
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    return cleaned.strip()


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    system: str | None = None
    image: object = None
    images: tuple[str, ...] = ()
    num_predict: int | None = None
    output_format: object = None
    thinking_mode: str = 'NORMAL'
    history: tuple[dict, ...] = ()
    context: CoreContext = field(default_factory=CoreContext.create)

@dataclass(frozen=True)
class ModelMetrics:
    model: str
    latency_ms: float
    prompt_tokens: int
    output_tokens: int
    attempts: int
    success: bool
    fallback_from: str | None = None
    fallback_reason: str | None = None

@dataclass(frozen=True)
class ModelResponse:
    text: str
    metrics: ModelMetrics

class ModelProvider(ABC):
    def describe(self): return {}
    def set_keep_alive(self, seconds): raise NotImplementedError("Provider sem lifecycle implementado")
    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelResponse: ...

class OllamaProvider(ModelProvider):

    def __init__(self, cfg): self.cfg = cfg; self.last_metrics = None
    def set_keep_alive(self, seconds):
        payload={'model':self.cfg['ollama']['model'],'messages':[],'stream':False,'keep_alive':seconds}
        req=urllib.request.Request(self.cfg['ollama']['url'].rstrip('/')+'/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        validate_url_scheme(req)
        timeout=min(int(self.cfg['ollama'].get('timeout',180)),180) if seconds<0 else 10
        with urllib.request.urlopen(req,timeout=timeout): pass  # nosec: B310
    def describe(self):
        req=urllib.request.Request(self.cfg['ollama']['url'].rstrip('/')+'/api/show',data=json.dumps({'model':self.cfg['ollama']['model']}).encode(),headers={'Content-Type':'application/json'})
        validate_url_scheme(req)
        try:
            with urllib.request.urlopen(req,timeout=5) as response: data=json.load(response).get('details',{})  # nosec: B310
            return {key:data.get(key,'UNKNOWN') for key in ('parameter_size','quantization_level','family')}
        except (OSError,ValueError): return {'parameter_size':'UNKNOWN','quantization_level':'UNKNOWN'}
    def generate(self, request):
        started = time.perf_counter()
        cfg = self.cfg
        messages = []
        if request.system: messages.append({'role':'system', 'content':request.system})
        messages.extend(validated_history(request.history))
        msg = {'role':'user', 'content':request.prompt}
        images = ([image_base64(request.image)] if request.image else []) + list(request.images)
        if images:
            prepared = []
            for encoded in images:
                try:
                    data = base64.b64decode(encoded, validate=True)
                    prepared.append(base64.b64encode(prepare_frame(data)).decode('ascii'))
                except (ValueError, TypeError):
                    raise ValueError('A imagem está corrompida ou excede os limites de resolução.') from None
            msg['images'] = prepared
        messages.append(msg)
        ollama_cfg = cfg['ollama']
        options = {
            'temperature': 0.2 if request.output_format else float(ollama_cfg.get('temperature', 0.7)),
            'top_p': float(ollama_cfg.get('top_p', 0.8)),
            'top_k': int(ollama_cfg.get('top_k', 20)),
            'repeat_penalty': float(ollama_cfg.get('repeat_penalty', 1.0)),
            'num_ctx': int(ollama_cfg.get('num_ctx', 8192)),
            'num_predict':int(request.num_predict or ollama_cfg.get('num_predict',2048)),
        }
        payload = {'model':ollama_cfg['model'], 'stream':False,
                   'think': bool(cfg['ollama'].get('think',False)),
                   'keep_alive': ollama_cfg.get('keep_alive',0), 'messages':messages,
                   'options':options}
        if request.output_format: payload['format'] = request.output_format
        unfinished = False
        prompt_tokens = output_tokens = 0
        for attempt in range(2):
            if attempt: payload['messages'][-1]['content'] = request.prompt + '\n\nForneça agora a resposta final, sem raciocínio.'
            req = urllib.request.Request(cfg['ollama']['url'].rstrip('/') + '/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
            validate_url_scheme(req)
            with urllib.request.urlopen(req, timeout=int(cfg['ollama']['timeout'])) as response:  # nosec: B310
                result = json.load(response)
            prompt_tokens += int(result.get('prompt_eval_count',0))
            output_tokens += int(result.get('eval_count',0))
            raw_content = result.get('message',{}).get('content')
            content = raw_content.strip() if isinstance(raw_content, str) else raw_content.strip()


            structured = False
            if request.output_format:
                try: structured = isinstance(json.loads(content),(dict,list))
                except (ValueError,TypeError): pass
            if not structured and ('<think>' in content or '</think>' in content):
                unfinished |= '<think>' in content and '</think>' not in content
                content = content.rsplit('</think>',1)[-1].strip() if '</think>' in content else ''
            if not structured and not request.output_format:
                content = sanitize_chat_output(content)
            self.last_metrics = ModelMetrics(payload['model'],(time.perf_counter()-started)*1000,prompt_tokens,output_tokens,attempt+1,bool(content))
            if content: return ModelResponse(content,self.last_metrics)
            log.warning('Ollama retornou resposta vazia; tentativa %d de 2.',attempt+1)
        fallback = cfg.get('models',{}).get('call')
        if (cfg.get('cognition',{}).get('empty_fallback',True)
                and payload['model']==cfg.get('models',{}).get('chat')
                and isinstance(fallback,str) and fallback and fallback!=payload['model']):
            from ..model_state import ModelSession
            local=copy.deepcopy(cfg);local['ollama']['model']=fallback;local['ollama']['think']=False
            local.setdefault('cognition',{})['empty_fallback']=False
            session=ModelSession(local);provider=OllamaProvider(local)
            try:
                session.wake()
                reply=provider.generate(request)
                metrics=replace(reply.metrics,latency_ms=(time.perf_counter()-started)*1000,
                    prompt_tokens=prompt_tokens+reply.metrics.prompt_tokens,
                    output_tokens=output_tokens+reply.metrics.output_tokens,
                    attempts=2+reply.metrics.attempts,fallback_from=payload['model'],fallback_reason='empty_final')
                self.last_metrics=metrics
                return ModelResponse(reply.text,metrics)
            except Exception:
                failed=provider.last_metrics
                self.last_metrics=ModelMetrics(fallback,(time.perf_counter()-started)*1000,
                    prompt_tokens+(failed.prompt_tokens if failed else 0),
                    output_tokens+(failed.output_tokens if failed else 0),
                    2+(failed.attempts if failed else 0),False,
                    fallback_from=payload['model'],fallback_reason='empty_final')
                raise
            finally:
                session.sleep()
        if unfinished: raise RuntimeError('Ollama retornou raciocínio sem resposta final; o texto foi bloqueado por segurança.')
        raise RuntimeError('Ollama não retornou uma resposta final após duas tentativas.')

LocalQwenProvider = OllamaProvider


def _make_adapter_factory(provider_name: str):
    def _factory(cfg: dict) -> ModelProvider:
        from .provider_adapter import create_llm_provider
        from ..config.llm_config import get_llm_config

        llm_cfg = get_llm_config()
        prof = llm_cfg.get_provider(provider_name)
        model = cfg.get("models", {}).get("chat") or prof.model
        return create_llm_provider(
            provider_name,
            model=model,
            api_key=prof.api_key,
            base_url=prof.base_url,
            timeout=float(cfg.get("ollama", {}).get("timeout", prof.timeout)),
        )

    return _factory


PROVIDERS = {
    "ollama": OllamaProvider,
    "openai": _make_adapter_factory("openai"),
    "azure": _make_adapter_factory("azure"),
    "azure-openai": _make_adapter_factory("azure-openai"),
    "anthropic": _make_adapter_factory("anthropic"),
    "gemini": _make_adapter_factory("gemini"),
    "google": _make_adapter_factory("gemini"),
    "groq": _make_adapter_factory("groq"),
    "openrouter": _make_adapter_factory("openrouter"),
    "deepseek": _make_adapter_factory("deepseek"),
    "mistral": _make_adapter_factory("mistral"),
    "vllm": _make_adapter_factory("vllm"),
    "lmstudio": _make_adapter_factory("lmstudio"),
    "local": _make_adapter_factory("local"),
}


def get_provider(cfg) -> ModelProvider:
    name = cfg.get("cognition", {}).get("model_provider", "ollama")
    if name not in PROVIDERS:
        raise ValueError("ModelProvider não registrado")
    return PROVIDERS[name](cfg)
