
from abc import ABC, abstractmethod
import hashlib
import math
import re
from .domains import normalize

class EmbeddingProvider(ABC):
    model: str
    version: str
    dimensions: int
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

def tokens(text): return re.findall(r"[a-z0-9]+", normalize(text))

class HashEmbeddingProvider(EmbeddingProvider):
    model = "mark-hash-lexical"
    version = "1"
    dimensions = 128
    def embed(self, text):
        result = [0.0] * self.dimensions
        for token in tokens(text):
            key = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            result[key % self.dimensions] += 1 if key & (1 << 63) else -1
        norm = math.sqrt(sum(x*x for x in result)) or 1
        return [x/norm for x in result]

def cosine(a, b):
    if len(a) != len(b): raise ValueError("Dimensões de embedding incompatíveis")
    return sum(x*y for x,y in zip(a,b)) / ((math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(x*x for x in b))) or 1)
