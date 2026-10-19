"""EVA entry point (Stage 1: UI shell with system tray)."""
from eva.common.logging_setup import get_logger
from eva.ui.app import run_app

log = get_logger("eva")


def main() -> None:
    log.info("Starting EVA (Stage 1: UI shell)")
    code = run_app()
    log.info("EVA stopped")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
