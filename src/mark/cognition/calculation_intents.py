
import re
from .quantities import normalized,parse_number,canonical_unit,quantity_matches,UNIT_PATTERN,QUANTITY_PATTERN
from .tools import ActionRequest
from .chemistry import UNITS

CONVERSION_PREFIX=r'(?:converta|converte|converter|transforme|transforma|passe|passa|quanto (?:e|da)|quanto (?:isso )?(?:equivale|vale)|(?:voce )?pode (?:converter|converte|passar|transformar))'
FOLLOW_UP=re.compile(rf'(?:e\s+)?(?:agora\s+)?(?:isso\s+)?(?:em|para|pra)\s+({UNIT_PATTERN})')

def clean(text):
    n=normalized(text)
    n=re.sub(r'^mark\s*[:,]?\s*','',n)
    n=re.sub(r'^por favor[, ]+','',n)
    return re.sub(r'[, ]+por favor$','',n).strip()

def calculation_request(text):
    n=clean(text)
    match=re.fullmatch(rf'{CONVERSION_PREFIX}(?:\s+por favor)?\s+{QUANTITY_PATTERN}\s+(?:para|pra|em)\s+({UNIT_PATTERN})',n)
    if match:return ActionRequest('chemistry.unit_conversion',{'value':parse_number(match[1]),'from_unit':canonical_unit(match[2]),'to_unit':canonical_unit(match[3])})

    match=re.fullmatch(rf'{QUANTITY_PATTERN}\s+(?:em|para|pra)\s+({UNIT_PATTERN})',n)
    if match:return ActionRequest('chemistry.unit_conversion',{'value':parse_number(match[1]),'from_unit':canonical_unit(match[2]),'to_unit':canonical_unit(match[3])})
    if 'densidade' in n:
        quantities=quantity_matches(n)
        masses=[q for q in quantities if q[1] in ('g','kg','mg')]
        volumes=[q for q in quantities if q[1] in ('L','mL','cm3','m3')]
        if len(quantities)==2 and len(masses)==len(volumes)==1:
            return ActionRequest('chemistry.density',{'mass':masses[0][0],'mass_unit':masses[0][1],'volume':volumes[0][0],'volume_unit':volumes[0][1]})
    return None

def followup_request(text,memory):
    match=FOLLOW_UP.fullmatch(clean(text))
    getter=getattr(memory,'history',None)
    if not match or not callable(getter):return None
    target=canonical_unit(match[1])
    for turn in reversed(getter(limit=6)):
        previous=turn.get('user','') if isinstance(turn,dict) else ''
        if not isinstance(previous,str):return None
        if FOLLOW_UP.fullmatch(clean(previous)):continue
        anchor=calculation_request(previous)
        if anchor and anchor.tool=='chemistry.unit_conversion':
            return ActionRequest(anchor.tool,{**anchor.arguments,'to_unit':target})

        return None
    return None

def format_calculation(request,result):
    value=f"{result['value']:.12g}".replace('.',',')
    if request.tool=='chemistry.density':return f"Densidade = massa ÷ volume = {value} {result['unit']}."
    if request.tool=='chemistry.unit_conversion':return f"{value} {result['unit']} (conversão calculada)."
    raise ValueError('Cálculo sem formatter')

def calculation_error(request,error):
    if request.tool=='chemistry.unit_conversion' and str(error)=='Unidades incompatíveis':
        left=UNITS.get(request.arguments['from_unit'],('temperature',))[0]
        right=UNITS.get(request.arguments['to_unit'],('temperature',))[0]
        if {left,right}=={'mass','volume'}:
            return 'Para relacionar massa e volume, preciso da densidade do material. Diga qual material e a densidade conhecida.'
        return 'Essas unidades representam grandezas diferentes; escolha uma unidade do mesmo tipo.'
    if request.tool=='chemistry.unit_conversion' and 'zero absoluto' in str(error):
        return 'Não consegui validar: essa temperatura fica abaixo do zero absoluto.'
    if request.tool=='chemistry.density':
        if request.arguments['volume']<=0:return 'Não consegui validar: o volume precisa ser maior que zero.'
        if request.arguments['mass']<0:return 'Não consegui validar: a massa não pode ser negativa.'
    return 'Não consegui validar esse cálculo. Confira as unidades e os valores fornecidos.'
