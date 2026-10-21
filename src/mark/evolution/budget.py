
import os
from pathlib import Path
import shutil
import stat
import time


def tree_bytes(path):
    path=Path(path)
    if not path.exists():return 0
    if path.is_symlink():raise PermissionError('Symlink fora do orçamento')
    if path.is_file():return path.stat().st_size
    total=0
    for directory,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            item=Path(directory)/name;info=item.lstat()
            if stat.S_ISLNK(info.st_mode):raise PermissionError('Symlink no laboratório')
            if stat.S_ISREG(info.st_mode):total+=info.st_size
    return total


def preflight(root,policy,estimated_temporary=1024*1024,free_bytes=None,gpu_required=False,gpu_free_gib=None):
    free=shutil.disk_usage(root).free if free_bytes is None else free_bytes
    if type(estimated_temporary)is not int or estimated_temporary<0:raise ValueError('Estimativa inválida')
    if estimated_temporary>policy.temporary_bytes:raise RuntimeError('Solicite ampliação do orçamento temporário antes de preparar o experimento')
    if free<policy.reserve_free_bytes+estimated_temporary:raise RuntimeError('Espaço livre insuficiente com reserva de segurança')
    current=tree_bytes(Path(root)/'candidates')
    if current+estimated_temporary>policy.temporary_bytes:raise RuntimeError('Retenção existente excede orçamento; revisão de armazenamento requerida')
    if gpu_required and (gpu_free_gib is None or gpu_free_gib<policy.vram_gib):raise RuntimeError('GPU ausente ou VRAM disponível insuficiente')
    return {'temporary_current_bytes':current,'temporary_estimated_bytes':estimated_temporary,'free_bytes':free,
            'reserve_bytes':policy.reserve_free_bytes,'vram_budget_gib':policy.vram_gib,'gpu_required':gpu_required}
