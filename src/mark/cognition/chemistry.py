
from collections import Counter
import math
import re



ATOMIC_WEIGHTS = {'H':1.0080,'He':4.0026,'Li':6.94,'Be':9.0122,'B':10.81,'C':12.011,
                  'N':14.007,'O':15.999,'F':18.998,'Ne':20.180,'Na':22.990,'Mg':24.305,
                  'Al':26.982,'Si':28.085,'P':30.974,'S':32.06,'Cl':35.45,'Ar':39.95,
                  'K':39.098,'Ca':40.078,'Fe':55.845,'Cu':63.546,'Zn':65.38}
REFERENCE = 'https://ciaaw.org/abridged-atomic-weights.htm'

def formula_counts(formula):
    if not isinstance(formula,str) or not 1<=len(formula)<=256: raise ValueError('Fórmula inválida')
    parts = re.findall(r'[A-Z][a-z]?|[0-9]+|[()]',formula)
    if ''.join(parts) != formula: raise ValueError('Sintaxe não suportada; use fórmula neutra sem carga/isótopo/hidrato')
    pos = 0
    def multiplier():
        nonlocal pos
        if pos < len(parts) and parts[pos].isdigit():
            value = int(parts[pos]); pos += 1
            if not 1 <= value <= 10000: raise ValueError('Coeficiente inválido')
            return value
        return 1
    def group(depth):
        nonlocal pos
        if depth > 8: raise ValueError('Fórmula complexa demais')
        result=Counter()
        while pos < len(parts) and parts[pos] != ')':
            token=parts[pos]; pos+=1
            if token=='(':
                sub=group(depth+1)
                if not sub or pos>=len(parts) or parts[pos]!=')': raise ValueError('Parênteses inválidos')
                pos+=1; count=multiplier()
                result.update({k:v*count for k,v in sub.items()})
            else:
                if token not in ATOMIC_WEIGHTS: raise ValueError('Elemento não coberto nesta versão: '+token)
                result[token]+=multiplier()
            if sum(result.values()) > 10000: raise ValueError('Fórmula excede o limite')
        return result
    result=group(0)
    if pos!=len(parts) or not result: raise ValueError('Fórmula inválida')
    return dict(result)

def molar_mass(formula):
    counts=formula_counts(formula)
    return {'formula':formula, 'value':round(sum(ATOMIC_WEIGHTS[e]*c for e,c in counts.items()),6),
            'unit':'g/mol', 'atoms':counts, 'reference':REFERENCE, 'precision':'approximate_standard_atomic_weights'}

def stoichiometry(known_moles, known_coefficient, target_coefficient):
    return {'value':known_moles*target_coefficient/known_coefficient, 'unit':'mol', 'assumption':'reaction coefficients supplied and already balanced'}

UNITS = {'g':('mass',1.),'kg':('mass',1000.),'mg':('mass',.001),'L':('volume',1.),'mL':('volume',.001),'cm3':('volume',.001),'m3':('volume',1000.),
         'Pa':('pressure',1.),'kPa':('pressure',1000.),'bar':('pressure',100000.),'atm':('pressure',101325.),
         'mol':('amount',1.),'mmol':('amount',.001)}

def unit_conversion(value, from_unit, to_unit):
    if from_unit in {'C','K','F'} and to_unit in {'C','K','F'}:
        kelvin = value if from_unit=='K' else value+273.15 if from_unit=='C' else (value-32)*5/9+273.15
        if kelvin<0: raise ValueError('Temperatura abaixo de zero absoluto')
        result=kelvin if to_unit=='K' else kelvin-273.15 if to_unit=='C' else (kelvin-273.15)*9/5+32
    else:
        if from_unit not in UNITS or to_unit not in UNITS or UNITS[from_unit][0]!=UNITS[to_unit][0]: raise ValueError('Unidades incompatíveis')
        result=value*UNITS[from_unit][1]/UNITS[to_unit][1]
    return {'value':result, 'unit':to_unit}

def density(mass,mass_unit,volume,volume_unit):
    grams=unit_conversion(mass,mass_unit,'g')['value']
    cubic_cm=unit_conversion(volume,volume_unit,'cm3')['value']
    if grams<0 or cubic_cm<=0:raise ValueError('Massa não negativa e volume positivo são necessários')
    return {'value':grams/cubic_cm,'unit':'g/cm³'}

def ideal_gas(moles, temperature_kelvin, pressure_pa):
    return {'value':moles*8.314462618*temperature_kelvin/pressure_pa, 'unit':'m3', 'assumption':'ideal gas; Kelvin and Pascal'}

def ph_calculation(concentration):
    return {'value':-math.log10(concentration), 'unit':'pH', 'assumption':'ideal dilute aqueous strong monoprotic acid; excludes buffers/weak acids/water-dominated limit'}

def periodic_table_lookup(symbol):
    if symbol not in ATOMIC_WEIGHTS: raise ValueError('Elemento não coberto nesta versão')
    return {'symbol':symbol,'value':ATOMIC_WEIGHTS[symbol],'unit':'g/mol','reference':REFERENCE}
