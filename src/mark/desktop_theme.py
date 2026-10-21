import html
import re

THEME_PALETTE = {
    "bg_canvas": "#1A1D21",
    "bg_sidebar": "#1A1D21",
    "bg_surface": "#22262B",
    "bg_elevated": "#22262B",
    "bg_hover": "#2D333B",
    "border_subtle": "#30363D",
    "border_default": "#30363D",
    "border_active": "#58A6FF",
    "accent_cobalt": "#58A6FF",
    "accent_cyan": "#58A6FF",
    "accent_cyan_bright": "#58A6FF",
    "accent_blue_glow": "#58A6FF",
    "text_primary": "#EAEAEA",
    "text_secondary": "#8B949E",
    "text_muted": "#6E7681",
}



def safe_web_link(url):
    from urllib.parse import urlsplit
    if not isinstance(url,str) or len(url)>4096 or any(ord(c)<33 for c in url) or '\\' in url:return False
    try:
        parts=urlsplit(url)
        return parts.scheme in ('http','https') and bool(parts.hostname) and not parts.username and not parts.password
    except ValueError:return False

def inline_html(text):
    from urllib.parse import quote
    pattern=re.compile(r'`([^`\n]+)`|\[([^\]\n]+)\]\((https?://[^\s<>]+)\)|\[([a-zA-Z0-9_\-\.]{2,64}):\s*([^\]\r\n]{1,160})\]|(https?://[^\s<>]+)')
    result=[];start=0
    def plain(value):return re.sub(r'\*\*([^*\n]+)\*\*',r'<b>\1</b>',html.escape(value)).replace('\n','<br>')
    for match in pattern.finditer(text):
        result.append(plain(text[start:match.start()]));start=match.end()
        if match.group(1):
            result.append('<span style="color:#4fd1c5; background-color:#101826; padding:1px 4px; border-radius:4px; font-family:monospace;">'+html.escape(match.group(1))+'</span>')
            continue
        if match.group(4):
            doc_id = match.group(4)
            excerpt = match.group(5)
            safe_href = f"mark-citation://{quote(doc_id)}?excerpt={quote(excerpt)}"
            cit_badge = (
                f'<a href="{safe_href}" style="color:#58A6FF; text-decoration:none; font-weight:500; '
                f'background:rgba(88,166,255,0.12); padding:1px 6px; border-radius:4px; '
                f'border:1px solid rgba(88,166,255,0.3); font-size:12px;">'
                f'[{html.escape(doc_id)}:{html.escape(excerpt)}]</a>'
            )
            result.append(cit_badge)
            continue
        label=match.group(2);url=match.group(3) or match.group(6);suffix=''
        if not label and url:
            trimmed=url.rstrip('.,;!?')
            while trimmed.endswith(')') and trimmed.count(')')>trimmed.count('('):trimmed=trimmed[:-1]
            suffix=url[len(trimmed):];url=trimmed;label=url
        if url and safe_web_link(url):result.append('<a href="'+html.escape(url,quote=True)+'" style="color:#63b3ed; text-decoration:underline;">'+html.escape(label)+'</a>'+plain(suffix))
        else:result.append(plain(match.group(0)))
    result.append(plain(text[start:]));return ''.join(result)

def message_html(text: str) -> str:

    lines=text.splitlines(); rendered=[]; paragraph=[]; list_kind=None; list_items=[]; i=0

    def flush_paragraph():
        if paragraph:
            rendered.append('<p style="margin:5px 0;">'+inline_html('\n'.join(paragraph))+'</p>')
            paragraph.clear()

    def flush_list():
        nonlocal list_kind, list_items
        if list_kind:
            tag='ol' if list_kind=='ol' else 'ul'
            rendered.append('<'+tag+' style="margin-top:4px; margin-bottom:4px;">'+''.join('<li>'+inline_html(item)+'</li>' for item in list_items)+'</'+tag+'>')
        list_kind=None;list_items=[]

    def flush_text_blocks():
        flush_paragraph();flush_list()

    def cells(line):
        return [part.strip() for part in line.strip().strip('|').split('|')]

    while i<len(lines):
        line=lines[i]
        if line.startswith('```'):
            flush_text_blocks();code=[];i+=1
            while i<len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]);i+=1
            if i<len(lines):i+=1
            rendered.append('<pre style="color:#d8e3f2; font-family:\'JetBrains Mono\',monospace; font-size:12px; background-color:#0d131f; border:1px solid #30363D; border-radius:8px; padding:10px; white-space:pre-wrap;">'+html.escape('\n'.join(code))+'</pre>')
            continue
        heading=re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$',line)
        if heading:
            flush_text_blocks();level=min(len(heading.group(1)),4)
            rendered.append(f'<h{level} style="margin:12px 0 5px; color:#f4f7fb;">{inline_html(heading.group(2))}</h{level}>');i+=1;continue
        if i+1<len(lines) and '|' in line and re.match(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$',lines[i+1]):
            flush_text_blocks();headers=cells(line);i+=2;rows=[]
            while i<len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]));i+=1
            table=['<table border="1" cellspacing="0" cellpadding="6" style="border-color:#263856; border-collapse:collapse; margin:8px 0;"><thead><tr>']
            table.extend('<th style="background-color:#162033; color:#4fd1c5;">'+inline_html(value)+'</th>' for value in headers)
            table.append('</tr></thead><tbody>')
            for row in rows:
                table.append('<tr>')
                for col in range(len(headers)):
                    table.append('<td>'+inline_html(row[col] if col<len(row) else '')+'</td>')
                table.append('</tr>')
            table.append('</tbody></table>');rendered.append(''.join(table));continue
        if not line.strip():
            flush_text_blocks();i+=1;continue
        if re.match(r'^\s{0,3}([-*_]\s*){3,}$',line):
            flush_text_blocks();rendered.append('<hr/>');i+=1;continue
        item=re.match(r'^\s{0,3}([-+*])\s+(.+)$',line)
        numbered=re.match(r'^\s{0,3}\d+[.)]\s+(.+)$',line)
        if item or numbered:
            flush_paragraph();kind='ul' if item else 'ol'
            if list_kind and list_kind!=kind:flush_list()
            list_kind=kind;list_items.append((item or numbered).group(2 if item else 1));i+=1;continue
        quote=re.match(r'^\s{0,3}>\s?(.*)$',line)
        if quote:
            flush_text_blocks();quoted=[]
            while i<len(lines):
                match=re.match(r'^\s{0,3}>\s?(.*)$',lines[i])
                if not match:break
                quoted.append(match.group(1));i+=1
            rendered.append('<blockquote style="margin:6px 0; padding-left:12px; border-left:3px solid #38b2ac; color:#9fb0c7;">'+inline_html('\n'.join(quoted))+'</blockquote>');continue
        flush_list();paragraph.append(line);i+=1
    flush_text_blocks()
    return '<div style="line-height:150%;">'+''.join(rendered)+'</div>'


def _get_stylesheet() -> str:
    from .ui.theme_manager import ThemeManager
    return ThemeManager.get_stylesheet()


STYLESHEET = _get_stylesheet()
