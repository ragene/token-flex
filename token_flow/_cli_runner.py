"""
Thin shim so setup.py can call `python -m token_flow._cli_runner <args>`
before the tf-server entry-point script is on PATH (i.e. mid-install).
"""
from token_flow._cli import main

if __name__ == "__main__":
    main()
