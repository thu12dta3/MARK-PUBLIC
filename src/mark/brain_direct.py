
from __future__ import annotations
import ast, re, unicodedata
from datetime import datetime
from .selfcheck import camera_status, ollama_status

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s+*/().,-]", " ", text)
    text = re.sub(r",(?=\s*[a-z])|(?<=[a-z]),", " ", text)
    return text.strip()


def direct_clock(text: str, now: datetime | None = None) -> str | None:

    query = normalize(text).strip(" ?!.,")
    query = re.sub(r"\s+(por favor|please)$", "", query)
    time_queries = {
        "que horas sao", "que horas sao agora", "que horas sao ai",
        "que horario e", "que horario e agora", "qual o horario", "qual e o horario",
        "qual o horario atual", "qual e o horario atual", "que horas sao agora mesmo",
        "qual a hora", "qual e a hora", "qual a hora agora", "qual e a hora agora",
        "me diga as horas", "me diz as horas", "me fala as horas", "me diga que horas sao",
        "me diz que horas sao", "me fala que horas sao",
        "tell me the time", "what time is it", "what time is it now",
        "what time is it right now", "what s the current time", "what is the current time",
        "what s the time", "what s the time now", "what is the time", "what is the time now",
        "what is the local time", "what s the local time",
    }
    date_queries = {
        "que dia e hoje", "que dia e hoje ai", "qual a data de hoje", "qual e a data de hoje",
        "que data e hoje", "qual e a data", "qual o dia de hoje", "what day is it", "what day is it today",
        "what is today s date", "what s today s date", "what is the date today",
        "what s the date", "what is the date", "what date is it",
    }
    if query not in time_queries and query not in date_queries:
        return None
    current = now if now is not None else datetime.now().astimezone()
    english = query in {
        "tell me the time", "what time is it", "what time is it now",
        "what time is it right now", "what s the current time", "what is the current time",
        "what s the time", "what s the time now", "what is the time", "what is the time now",
        "what is the local time", "what s the local time", "what day is it",
        "what day is it today", "what is today s date", "what s today s date",
        "what is the date today", "what s the date", "what is the date", "what date is it",
    }
    if query in time_queries:
        if english:
            hour = current.hour % 12 or 12
            suffix = "AM" if current.hour < 12 else "PM"
            return f"The local time is {hour}:{current.minute:02d} {suffix}."
        return f"Agora são {current.hour:02d}h{current.minute:02d}."
    if english:
        weekdays_en = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        months_en = ("January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December")
        return f"Today is {weekdays_en[current.weekday()]}, {months_en[current.month - 1]} {current.day}, {current.year}."
    weekdays = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
                "sexta-feira", "sábado", "domingo")
    months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
              "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    return f"Hoje é {weekdays[current.weekday()]}, {current.day} de {months[current.month - 1]} de {current.year}."


def direct_status(text: str, cfg: dict) -> str | None:

    query = normalize(text).strip(" ?!.,")
    status_queries = {
        "como esta o mark", "como esta o mark agora", "status do mark", "status do sistema",
        "como esta o sistema", "o mark esta pronto", "o mark esta funcionando", "o mark esta online",
        "mark esta pronto", "mark esta funcionando", "mark esta online",
        "is mark ready", "is mark online", "is mark working", "mark status", "system status",
    }
    if query not in status_queries:
        return None
    model_ready, _ = ollama_status(cfg)
    cam_ready, _ = camera_status(cfg)
    if query in {"is mark ready", "is mark online", "is mark working", "mark status", "system status"}:
        model = "The local Qwen model is available." if model_ready else "The local Qwen model is unavailable."
        camera = ("The camera stream is configured; physical connectivity has not been tested."
                  if cam_ready else "The camera stream configuration is invalid or missing.")
        return f"{model} {camera}"
    model = "O Qwen local está disponível." if model_ready else "O Qwen local está indisponível."
    camera = ("A câmera está configurada; a conexão física não foi testada."
              if cam_ready else "A configuração do stream da câmera está inválida ou ausente.")
    return f"{model} {camera}"


def direct_math(text: str):

    t = normalize(text).strip(" ?!.,")
    t = re.sub(r"^(quanto (e|da|da quanto)|what is|calculate|calcule|calcula|resolva|resultado de)\s+", "", t)
    opmap = {"vezes": "*", "multiplicado por": "*", "multiplicada por": "*", "x": "*",
             "multiplied by": "*", "times": "*", "divided by": "/", "plus": "+", "minus": "-",
             "dividido por": "/", "dividida por": "/", "mais": "+", "menos": "-"}
    for word, symbol in sorted(opmap.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{re.escape(word)}\b", symbol, t)
    t = t.replace(",", ".")
    if not re.fullmatch(r"\s*[\d.() +*/-]+\s*", t): return None
    try:
        tree = ast.parse(t, mode="eval")
        def calc(node):
            if isinstance(node, ast.Expression): return calc(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float): return node.value
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                x = calc(node.operand); return x if isinstance(node.op, ast.UAdd) else -x
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                a, b = calc(node.left), calc(node.right)
                if isinstance(node.op, ast.Add): return a+b
                if isinstance(node.op, ast.Sub): return a-b
                if isinstance(node.op, ast.Mult): return a*b
                if isinstance(node.op, ast.Div): return a/b
                if abs(b) > 8: raise ValueError("expoente fora do limite")
                return a**b
            raise ValueError("expressão inválida")
        value = calc(tree)
        if abs(value) > 1e100: return None
        if isinstance(value, float) and value.is_integer(): value = int(value)
        return f"{value:,}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(value, int) else str(value).replace(".", ",")
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
