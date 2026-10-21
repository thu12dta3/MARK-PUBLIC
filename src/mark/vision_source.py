
from pathlib import Path
import re
from .config import camera_url


def camera_input(cfg: dict) -> tuple[str, list[str]]:
    cam = cfg['camera']
    source = cam.get('source', 'auto')
    if source not in {'auto', 'usb', 'rtsp'}:
        raise ValueError('Selecione uma câmera USB ou RTSP/RTSPS.')
    use_usb = source == 'usb' or (source == 'auto' and bool(cam.get('device')))
    if not use_usb:
        url = camera_url(cfg)
        if url:
            return 'rtsp', ['-tls_verify', '1' if cam.get('tls_verify') else '0',
                            '-rtsp_transport', 'tcp', '-fflags', 'nobuffer',
                            '-flags', 'low_delay', '-i', url]
        if source in {'auto', 'rtsp'}:
            raise RuntimeError('Câmera sem endereço RTSP/RTSPS ou dispositivo USB configurado.')
    device = cam.get('device')
    if not device:
        devices = sorted(Path('/dev/v4l/by-id').glob('*-video-index0'))
        device = str(devices[0]) if devices else '/dev/video0'
    device = str(device)

    if not (re.fullmatch(r'/dev/video\d+', device) or
            (re.fullmatch(r'/dev/v4l/by-id/[^/]+', device) and '..' not in Path(device).parts)):
        raise ValueError('A câmera USB deve apontar para um dispositivo V4L2 local.')
    size = str(cam.get('size', '1280x720'))
    match = re.fullmatch(r'(\d{2,4})x(\d{2,4})', size)
    if not match or any(not 16 <= int(v) <= 4096 for v in match.groups()):
        raise ValueError('Resolução da câmera USB inválida.')
    fps = float(cam.get('fps', 10))
    if not 1 <= fps <= 30:
        raise ValueError('A taxa de captura deve ficar entre 1 e 30 quadros/s.')
    return 'usb', ['-f', 'v4l2', '-input_format', 'mjpeg', '-video_size', size,
                   '-framerate', str(fps), '-i', device]


def capture_error(stderr: str, code: int, source: str) -> str:

    if source == 'rtsp':
        for pattern, message in (
            (r'\b401\s+Unauthorized\b', 'A câmera recusou a autenticação RTSP (401); confira o usuário e a senha configurados.'),
            (r'\b403\s+Forbidden\b', 'A câmera negou acesso ao stream RTSP (403); confira as permissões da conta.'),
            (r'\b404\s+Not Found\b', 'A câmera não encontrou esse caminho RTSP (404); confira a URL do stream.'),
            (r'connection refused|connection timed out|i/o timeout', 'A conexão RTSP foi recusada ou expirou; confira o endereço, a porta e a rede da câmera.'),
        ):
            if re.search(pattern, stderr, re.IGNORECASE):
                return message
        return f'FFmpeg não abriu o stream RTSP/RTSPS (código {code}); confira rede, endereço e câmera ativa.'
    if re.search('busy', stderr, re.I):
        return 'A câmera USB está ocupada por outro aplicativo. Feche a outra captura e tente novamente.'
    if re.search('permission denied', stderr, re.I):
        return 'Sem permissão para abrir a câmera USB. Confira as permissões do dispositivo.'
    return 'Não foi possível capturar a câmera USB. Confira a conexão e a resolução configurada.'
