"""Run the SKULLPT CLI via the old package name (v1.2 compat shim)."""
from skullpt.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
