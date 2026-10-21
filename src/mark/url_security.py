"""Módulo de validação de segurança para URLs e chamadas de rede no ecossistema MARK.

Garante validação estrita do esquema (http/https) antes de invocar urllib.request.urlopen,
prevenindo ataques de SSRF local e vazamento de arquivos arbitrários via esquema file:// ou esquemas customizados.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Sequence

ALLOWED_URL_SCHEMES = ("http", "https")


def validate_url_scheme(
    url_or_request: str | urllib.request.Request,
    allowed_schemes: Sequence[str] = ALLOWED_URL_SCHEMES,
) -> str:
    """Valida se o esquema da URL pertence à lista estrita de esquemas permitidos (http, https).

    Levanta ValueError caso o esquema seja vazio, inseguro (ex: file://, ftp://, gopher://)
    ou não esteja contido na lista permitida.
    """
    if isinstance(url_or_request, urllib.request.Request):
        url = url_or_request.full_url
    else:
        url = str(url_or_request)

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if not scheme or scheme not in allowed_schemes:
        raise ValueError(
            f"Esquema de URL inseguro ou não permitido: {parsed.scheme!r}. "
            f"Apenas os esquemas {allowed_schemes} são autorizados."
        )
    return url
