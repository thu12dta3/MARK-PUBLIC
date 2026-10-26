import html
import re

THEME_PALETTE = {
    "bg_canvas": "#121417",
    "bg_sidebar": "#0D0F11",
    "bg_surface": "#16191D",
    "bg_elevated": "#1A1D22",
    "bg_hover": "#262B33",
    "border_subtle": "#1E2228",
    "border_default": "#1E2228",
    "border_active": "#00D8F6",
    "accent_cobalt": "#00D8F6",
    "accent_cyan": "#00D8F6",
    "accent_cyan_bright": "#00D8F6",
    "accent_blue_glow": "#00D8F6",
    "text_primary": "#EDEDED",
    "text_secondary": "#8E95A0",
    "text_muted": "#5F6672",
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
    def plain(value):return re.sub(r'\*\*([^*\n]+)\*\*',r'<span style="font-weight:500;">\1</span>',html.escape(value)).replace('\n','<br>')
    for match in pattern.finditer(text):
        result.append(plain(text[start:match.start()]));start=match.end()
        if match.group(1):
            result.append('<span style="color:#00D8F6; background-color:#16191D; padding:1px 5px; border-radius:4px; font-family:\'JetBrains Mono\',monospace;">'+html.escape(match.group(1))+'</span>')
            continue
        if match.group(4):
            doc_id = match.group(4)
            excerpt = match.group(5)
            safe_href = f"mark-citation://{quote(doc_id)}?excerpt={quote(excerpt)}"
            cit_badge = (
                f'<a href="{safe_href}" style="color:#00D8F6; text-decoration:none; font-weight:500; '
                f'background:rgba(0,216,246,0.10); padding:1px 6px; border-radius:4px; '
                f'border:1px solid rgba(0,216,246,0.28); font-size:12px;">'
                f'[{html.escape(doc_id)}:{html.escape(excerpt)}]</a>'
            )
            result.append(cit_badge)
            continue
        label=match.group(2);url=match.group(3) or match.group(6);suffix=''
        if not label and url:
            trimmed=url.rstrip('.,;!?')
            while trimmed.endswith(')') and trimmed.count(')')>trimmed.count('('):trimmed=trimmed[:-1]
            suffix=url[len(trimmed):];url=trimmed;label=url
        if url and safe_web_link(url):result.append('<a href="'+html.escape(url,quote=True)+'" style="color:#00D8F6; text-decoration:underline; font-weight:400;">'+html.escape(label)+'</a>'+plain(suffix))
        else:result.append(plain(match.group(0)))
    result.append(plain(text[start:]));return ''.join(result)

def message_html(text: str) -> str:

    lines=text.splitlines(); rendered=[]; paragraph=[]; list_kind=None; list_items=[]; i=0

    def flush_paragraph():
        if paragraph:
            rendered.append('<p style="margin:0 0 6px 0; font-weight:400;">'+inline_html('\n'.join(paragraph))+'</p>')
            paragraph.clear()

    def flush_list():
        nonlocal list_kind, list_items
        if list_kind:
            tag='ol' if list_kind=='ol' else 'ul'
            rendered.append('<'+tag+' style="margin-top:4px; margin-bottom:6px; font-weight:400;">'+''.join('<li>'+inline_html(item)+'</li>' for item in list_items)+'</'+tag+'>')
        list_kind=None;list_items=[]

    def flush_text_blocks():
        flush_paragraph();flush_list()

    def cells(line):
        return [part.strip() for part in line.strip().strip('|').split('|')]

    while i<len(lines):
        line=lines[i]
        if line.startswith('```'):
            flush_text_blocks()
            lang = line[3:].strip() or "código"
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            code_text = '\n'.join(code)
            from urllib.parse import quote
            safe_code_encoded = quote(code_text)
            header_html = (
                f'<div style="background-color:#16191D; border-top-left-radius:8px; border-top-right-radius:8px; '
                f'padding:6px 12px; border:1px solid #1E2228; border-bottom:none; '
                f'font-family:Inter, sans-serif; font-size:11px; font-weight:500; color:#8E95A0; line-height:16px;">'
                f'<table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>'
                f'<td align="left" style="color:#8E95A0; font-family:Inter, sans-serif; font-size:11px; font-weight:500;">{html.escape(lang)}</td>'
                f'<td align="right"><a href="mark-copy://{safe_code_encoded}" style="color:#00D8F6; text-decoration:none; font-family:Inter, sans-serif; font-size:11px; font-weight:500;">Copiar</a></td>'
                f'</tr></table></div>'
            )
            pre_html = (
                f'<pre style="color:#EDEDED; font-family:\'JetBrains Mono\',monospace; font-size:12px; font-weight:400; '
                f'background-color:#0B0D0F; border:1px solid #1E2228; border-bottom-left-radius:8px; '
                f'border-bottom-right-radius:8px; padding:10px 12px; margin:0px; white-space:pre-wrap;">'
                f'{html.escape(code_text)}</pre>'
            )
            rendered.append(f'<div style="margin:8px 0px;">{header_html}{pre_html}</div>')
            continue
        heading=re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$',line)
        if heading:
            flush_text_blocks();level=min(len(heading.group(1)),4)
            rendered.append(f'<h{level} style="margin:12px 0 6px; color:#EDEDED; font-weight:500;">{inline_html(heading.group(2))}</h{level}>');i+=1;continue
        if i+1<len(lines) and '|' in line and re.match(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$',lines[i+1]):
            flush_text_blocks();headers=cells(line);i+=2;rows=[]
            while i<len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]));i+=1
            table=['<table border="1" cellspacing="0" cellpadding="6" style="border-color:#1E2228; border-collapse:collapse; margin:8px 0;"><thead><tr>']
            table.extend('<th style="background-color:#16191D; color:#00D8F6; font-weight:500;">'+inline_html(value)+'</th>' for value in headers)
            table.append('</tr></thead><tbody>')
            for row in rows:
                table.append('<tr>')
                for col in range(len(headers)):
                    table.append('<td style="font-weight:400;">'+inline_html(row[col] if col<len(row) else '')+'</td>')
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
            rendered.append('<blockquote style="margin:6px 0; padding-left:12px; border-left:2px solid #00D8F6; color:#8E95A0; font-weight:400;">'+inline_html('\n'.join(quoted))+'</blockquote>');continue
        flush_list();paragraph.append(line);i+=1
    flush_text_blocks()
    return '<div style="line-height:150%; font-weight:400;">'+''.join(rendered)+'</div>'


def _get_stylesheet() -> str:
    from .ui.theme_manager import ThemeManager
    return ThemeManager.get_stylesheet()


STYLESHEET = _get_stylesheet()
