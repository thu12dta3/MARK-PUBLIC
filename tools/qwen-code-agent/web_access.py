

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGE_CHARS = 24000
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"


def validate_public_url(url: str) -> str:

    if not isinstance(url, str) or len(url) > 3000:
        raise ValueError("URL ausente ou longa demais.")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Use uma URL pública começando com http:// ou https://.")
    if parsed.username or parsed.password:
        raise ValueError("URLs com credenciais embutidas são recusadas.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Porta inválida na URL.") from exc
    if port not in {None, 80, 443}:
        raise ValueError("Por segurança, a navegação web aceita somente as portas públicas 80 e 443.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Endereços locais não são acessíveis pela navegação web.")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [literal]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ValueError(f"Não foi possível resolver o domínio: {exc}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("A URL aponta para um endereço que não é público; acesso bloqueado.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    checked = validate_public_url(url)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    request = urllib.request.Request(checked, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.2"})
    response = opener.open(request, timeout=18)
    final_url = validate_public_url(response.geturl())
    content_type = response.headers.get_content_type().lower()
    if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
        response.close()
        raise ValueError(f"Conteúdo não textual ({content_type}); não foi baixado para o contexto.")
    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
        response.close()
        raise ValueError("Página excede o limite de 2 MiB.")
    data = response.read(MAX_RESPONSE_BYTES + 1)
    response.close()
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("Página excede o limite de 2 MiB.")
    charset = response.headers.get_content_charset() or "utf-8"
    return final_url, content_type, data.decode(charset, "replace")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self.links = []
        self._title = False
        self._skip_depth = 0
        self._link_href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._title = True
        if tag == "a" and attrs.get("href"):
            self._link_href = attrs["href"]
            self._link_text = []
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._title = False
        if tag == "a" and self._link_href is not None:
            text = " ".join("".join(self._link_text).split())
            if text:
                self.links.append((text, self._link_href))
            self._link_href = None
            self._link_text = []
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._title:
            self.title_parts.append(data)
        self.parts.append(data)
        if self._link_href is not None:
            self._link_text.append(data)


class _SearchResults(HTMLParser):
    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.items = []
        self._current = None
        self._capture = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._current = {"url": attrs.get("href", ""), "title": ""}
            self._capture = "title"
            self._text = []
        elif self._current is not None and tag in {"a", "div"} and "result__snippet" in classes:
            self._capture = "snippet"
            self._text = []

    def handle_endtag(self, tag):
        if self._current is not None and self._capture:
            text = " ".join("".join(self._text).split())
            if self._capture == "title":
                self._current["title"] = text
            elif self._capture == "snippet":
                self._current["snippet"] = text
                self.items.append(self._current)
                self._current = None
            self._capture = None
            self._text = []
        if len(self.items) >= self.limit:
            self._current = None

    def handle_data(self, data):
        if self._current is not None and self._capture:
            self._text.append(data)


def search_web(query: str, limit: int = 6) -> str:
    query = " ".join(str(query).split())
    if not 2 <= len(query) <= 300:
        raise ValueError("A busca precisa ter de 2 a 300 caracteres.")
    try:
        limit = max(1, min(8, int(limit)))
    except (TypeError, ValueError):
        limit = 6
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    final_url, _content_type, html = _open(url)
    parser = _SearchResults(limit)
    parser.feed(html)
    if not parser.items:
        raise RuntimeError("O buscador não retornou resultados legíveis; tente outra formulação.")
    output = [f"Busca pública via DuckDuckGo: {query}", f"Página de resultados: {final_url}"]
    for index, item in enumerate(parser.items, 1):
        result_url = urllib.parse.urljoin(final_url, item["url"])
        parsed_result = urllib.parse.urlsplit(result_url)
        if parsed_result.hostname == "duckduckgo.com" and parsed_result.path == "/l/":
            destination = urllib.parse.parse_qs(parsed_result.query).get("uddg", [])
            if destination:
                result_url = destination[0]
        try:
            result_url = validate_public_url(result_url)
        except ValueError:
            continue
        output.append(f"[{index}] {item['title']}\nURL: {result_url}\n{item.get('snippet', '')}")
    if len(output) == 2:
        raise RuntimeError("O buscador retornou links que não são destinos públicos acessíveis.")
    return "\n\n".join(output)


def fetch_page(url: str) -> str:
    final_url, content_type, body = _open(url)
    if content_type == "text/plain":
        title, text, links = "", body, []
    else:
        parser = _TextExtractor()
        parser.feed(body)
        title = " ".join("".join(parser.title_parts).split())
        text = " ".join("".join(parser.parts).split())
        links = parser.links
    text = text[:MAX_PAGE_CHARS]
    output = [f"URL final: {final_url}", f"Tipo: {content_type}"]
    if title:
        output.append(f"Título: {title}")
    output.append(f"Conteúdo extraído ({len(text)} caracteres):\n{text or '(sem texto legível)'}")
    public_links = []
    for label, href in links:
        absolute = urllib.parse.urljoin(final_url, href)
        try:
            public_links.append((label, validate_public_url(absolute)))
        except ValueError:
            continue
        if len(public_links) >= 20:
            break
    if public_links:
        output.append("Links públicos da página:\n" + "\n".join(f"- {label[:100]}: {href}" for label, href in public_links))
    return "\n\n".join(output)
