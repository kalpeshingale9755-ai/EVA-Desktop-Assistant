"""Quick voice test:   python -m eva.response.demo "Hello, I am EVA"
Run it from the EVA folder (the one that contains the eva folder)."""
import sys

from .speaker import build_default_speaker


def main() -> None:
    text = " ".join(sys.argv[1:]) or "Hello Kalpesh. EVA voice output is working."
    sp = build_default_speaker(on_error=lambda m: print("VOICE ERROR:", m))
    sp.speak(text)
    sp.wait_idle(60)
    print("voice used:", sp.last_backend_used)
    sp.shutdown()


if __name__ == "__main__":
    main()
