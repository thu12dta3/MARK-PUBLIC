
import re

SENSITIVE_KEYS = {'password','senha','token','private_key','api_key','credential','credentials','imei','serial','gps','latitude','longitude','health_raw','precise_location','chain_of_thought','hidden_reasoning'}
PATTERNS = [
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', re.S),
    re.compile(r'(?i)\b(?:bearer\s+)[A-Za-z0-9._~+/=-]+'),
    re.compile(r'(?i)\b(?:senha|password|token|api[_ -]?key|secret|imei|serial|latitude|longitude)\s*[:=]\s*[^\s,;]+'),
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,})\b'),
    re.compile(r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    re.compile(r'\b[0-9]{15,17}\b'),
    re.compile(r'(?<!\w)-?\d{1,3}\.\d{5,}\s*[,;]\s*-?\d{1,3}\.\d{5,}(?!\w)'),
    re.compile(r'\b(?=[A-Za-z0-9_-]{32,}\b)(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b'),
]

def sanitize(value):
    if isinstance(value,dict):
        return {key: '[REDACTED]' if key.lower() in SENSITIVE_KEYS else sanitize(item) for key,item in value.items()}
    if isinstance(value,list): return [sanitize(item) for item in value]
    if isinstance(value,str):
        for pattern in PATTERNS: value=pattern.sub('[REDACTED]',value)
    return value

def sanitized_candidate(input_text, context, mark_output, failure_type, correct_behavior=''):
    return {'schema_version':1, 'input':sanitize(input_text), 'context':sanitize(context), 'mark_output':sanitize(mark_output),
            'failure_type':failure_type, 'correct_behavior':sanitize(correct_behavior), 'review_status':'PENDING',
            'training_eligible':False, 'warning':'Sanitização heurística: revisão humana obrigatória; não garante anonimização'}
