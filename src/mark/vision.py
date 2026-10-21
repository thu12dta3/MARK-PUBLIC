
import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from .vision_source import camera_input, capture_error
from .vision_quality import inspect_frame, MAX_FRAME_BYTES

log = logging.getLogger('mark.vision')


def capture_frame(cfg: dict) -> Path:
    source, inputs = camera_input(cfg)
    directory = Path(cfg['paths']['tmp'])
    directory.mkdir(parents=True, exist_ok=True)

    fd, name = tempfile.mkstemp(prefix='mark-eye-', suffix='.jpg', dir=directory)
    os.close(fd)
    path = Path(name)
    valid = False
    try:
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *inputs,
                   '-an', '-sn', '-dn']
        if source == 'usb':

            command += ['-vf', "select='gte(n,4)'", '-fps_mode', 'vfr']
        command += ['-frames:v', '1', '-threads', '1', '-q:v', '2', '-y', str(path)]
        subprocess.run(command, check=True, timeout=max(1, min(60, int(cfg['camera'].get('timeout',20)))),
                       capture_output=True, text=True)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_FRAME_BYTES:
            raise RuntimeError('A câmera não forneceu um frame válido; confira o stream e tente novamente.')
        try:
            inspect_frame(path.read_bytes())
        except ValueError:
            raise RuntimeError('A câmera não forneceu uma imagem decodificável; tente novamente.') from None
        valid = True
        return path
    except subprocess.TimeoutExpired:
        raise RuntimeError('Tempo limite ao capturar a câmera; verifique a conexão e tente novamente.') from None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ''
        if isinstance(stderr, bytes): stderr = stderr.decode('utf-8', 'replace')
        raise RuntimeError(capture_error(stderr, exc.returncode, source)) from None
    except OSError as exc:
        log.warning('Não foi possível iniciar a captura da câmera (%s).', type(exc).__name__)
        raise RuntimeError('Não foi possível iniciar o FFmpeg para capturar a câmera; confira a instalação e permissões.') from None
    finally:

        if not valid:
            path.unlink(missing_ok=True)


def image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode('ascii')
