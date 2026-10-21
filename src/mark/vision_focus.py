
from __future__ import annotations

import io
import math
from PIL import Image, ImageOps
from .vision_quality import inspect_frame


def validate_focus(rect) -> tuple[float, float, float, float] | None:
    if rect is None:
        return None
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise ValueError('Área de interesse inválida.')
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in rect):
        raise ValueError('Área de interesse inválida.')
    try:
        left, top, right, bottom = map(float, rect)
    except OverflowError:
        raise ValueError('Área de interesse inválida.') from None
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise ValueError('Área de interesse inválida.')
    if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
        raise ValueError('Marque uma área dentro da imagem.')
    return left, top, right, bottom


def crop_focus(data: bytes, rect) -> bytes:
    rect = validate_focus(rect)
    if rect is None:
        return data
    inspect_frame(data)
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original)
        w, h = image.size
        left, top, right, bottom = rect
        box = (int(left*w), int(top*h), min(w, math.ceil(right*w)), min(h, math.ceil(bottom*h)))
        if box[2]-box[0] < 16 or box[3]-box[1] < 16:
            raise ValueError('Marque uma área maior para analisar os detalhes.')
        crop = image.crop(box).convert('RGB')
        output = io.BytesIO()
        crop.save(output, 'JPEG', quality=95, subsampling=0)
        return output.getvalue()


def focus_instructions(rect) -> str:
    rect = validate_focus(rect)
    if rect is None:
        return ''
    return (' Imagem 1: cena geral. Imagem 2: recorte da área marcada pelo usuário no mesmo quadro. '
            'O recorte não representa um segundo objeto nem outro instante. '
            'Priorize leitura e reconhecimento dentro do recorte; se pedida uma contagem, '
            'conte apenas nessa área, sem somar o objeto duplicado na cena geral. '
            'Não invente letras que não estejam legíveis no recorte.')
