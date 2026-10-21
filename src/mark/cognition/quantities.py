
import math,re
from .domains import normalize

ALIASES={
 'g':'g','grama':'g','gramas':'g','kg':'kg','quilo':'kg','quilos':'kg','quilograma':'kg','quilogramas':'kg',
 'mg':'mg','miligrama':'mg','miligramas':'mg',
 'l':'L','litro':'L','litros':'L','ml':'mL','mililitro':'mL','mililitros':'mL',
 'm3':'m3','metro cubico':'m3','metros cubicos':'m3','cm3':'cm3','centimetro cubico':'cm3','centimetros cubicos':'cm3',
 'mol':'mol','mols':'mol','mmol':'mmol','milimol':'mmol','milimols':'mmol',
 'pa':'Pa','pascal':'Pa','pascals':'Pa','kpa':'kPa','quilopascal':'kPa','quilopascals':'kPa','bar':'bar','atm':'atm','atmosfera':'atm','atmosferas':'atm',
 'c':'C','°c':'C','celsius':'C','grau celsius':'C','graus celsius':'C','f':'F','°f':'F','fahrenheit':'F','grau fahrenheit':'F','graus fahrenheit':'F',
 'k':'K','kelvin':'K','kelvins':'K',
}
UNIT_PATTERN='(?:'+'|'.join(re.escape(k) for k in sorted(ALIASES,key=len,reverse=True))+')'
NUMBER_PATTERN=r'[-+]?(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,10})?|\d{1,12}(?:[.,]\d{1,10})?)(?:e[-+]?\d{1,3})?'
QUANTITY_PATTERN=rf'({NUMBER_PATTERN})\s*({UNIT_PATTERN})(?!\w)'

class QuantityError(ValueError):pass

def normalized(text):return normalize(text).replace('³','3').replace('−','-').strip(' ?!.')
def canonical_unit(text):return ALIASES[normalized(text)]

def parse_number(text):
    value=text.lower()
    if not re.fullmatch(NUMBER_PATTERN,value):raise QuantityError('Número não reconhecido nesta conversão.')
    if 'e' in value:
        mantissa,exponent=value.split('e')
        if abs(int(exponent))>12:raise QuantityError('Esse expoente excede o limite da calculadora local.')
        if ',' in mantissa and '.' in mantissa:mantissa=mantissa.replace('.','')
        elif mantissa.count('.')>1:raise QuantityError('Use uma mantissa sem separadores de milhar.')
        value=mantissa.replace(',','.')+'e'+exponent
    elif ',' in value:
        value=value.replace('.','').replace(',','.')
    elif value.count('.')>1:
        value=value.replace('.','')
    elif re.fullmatch(r'[-+]?[1-9]\d{0,2}\.\d{3}',value):
        raise QuantityError('Esse ponto pode indicar decimal ou milhar. Use vírgula para decimal ou escreva o inteiro sem separador.')
    number=float(value)
    if not math.isfinite(number) or abs(number)>1e12:raise QuantityError('O valor excede o limite da calculadora local.')
    return number

def quantity_matches(text):
    result=[]
    for match in re.finditer(QUANTITY_PATTERN,normalized(text)):

        if match.start() and (text[match.start()-1].isalnum() or text[match.start()-1] in '.,'):continue
        result.append((parse_number(match[1]),canonical_unit(match[2])))
    return result
