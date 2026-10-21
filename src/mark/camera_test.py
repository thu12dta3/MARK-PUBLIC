from .config import load_config
from .vision import capture_frame


def main() -> int:
    image = None
    try:
        image = capture_frame(load_config())
        print("Captura da câmera: OK.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Falha no teste da câmera: {exc}")
        return 1
    finally:
        if image is not None:
            image.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
