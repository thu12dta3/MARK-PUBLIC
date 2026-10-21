
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mark.brain import answer
from mark.config import load_config
from mark.memory import ShortMemory
from mark.selfcheck import ollama_status
from mark.tts import speak

cfg = load_config()
ready, message = ollama_status(cfg)
if not ready:
    raise SystemExit(f"Ollama indisponível: {message}")
memory = ShortMemory(int(cfg["memory"]["max_messages"]))
prompt = "Responda em português com uma frase curta confirmando que a integração está funcionando."
response = answer(prompt, cfg, memory)
memory.add(prompt, response)
assert response.strip(), "Ollama retornou resposta vazia"
print("Ollama e roteador: OK; memória: OK.")
speak("Integração funcionando.", "pt", cfg)
print("Piper integrado: OK.")
