"""`python -m src.api --migrate --host 0.0.0.0`"""

from src.api.serve import main

if __name__ == "__main__":
    raise SystemExit(main())
