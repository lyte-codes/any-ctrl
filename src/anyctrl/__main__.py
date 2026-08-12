"""Allow ``python -m anyctrl``."""

from anyctrl.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
