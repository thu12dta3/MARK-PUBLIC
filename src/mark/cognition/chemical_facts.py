
from .domains import normalize
import re


COMPOUNDS={
 'tnt':{'name':'2,4,6-trinitrotolueno','formula':'C7H5N3O6','cid':8376},
 'agua':{'name':'água','formula':'H2O','cid':962},
 'etanol':{'name':'etanol','formula':'C2H6O','cid':702},
 'dioxido de carbono':{'name':'dióxido de carbono','formula':'CO2','cid':280},
}
ALIASES={'trinitrotolueno':'tnt','2,4,6-trinitrotolueno':'tnt','alcool etilico':'etanol','gas carbonico':'dioxido de carbono'}

def molecular_formula(compound):
 n=normalize(compound).strip(' ?!.');item=COMPOUNDS.get(ALIASES.get(n,n))
 if not item:raise ValueError('Composto ausente do catálogo curado')
 return {'name':item['name'],'formula':item['formula'],'reference':'https://pubchem.ncbi.nlm.nih.gov/compound/'+str(item['cid']),'verified_on':'2026-10-11'}

def molecular_formula_query(text):
 n=normalize(text).strip(' ?!.')
 n=re.sub(r'[?.!]\s*responda (?:so|apenas) (?:a formula|o dado quimico)\s*$','',n).strip(' ?!.')
 match=re.fullmatch(r'(?:(?:qual (?:e )?(?:a )?|(?:me )?(?:diga|informe|mostre) (?:a )?)?formula (?:molecular |quimica )?(?:de |do |da )?)(.+)',n)
 if not match:return None
 name=ALIASES.get(match[1],match[1])
 return name if name in COMPOUNDS else None
