"""
Module entry point — allows running CLI commands via:
    python -m app.main <command>

This simply delegates to cli.py so you can use either:
    python cli.py start-48h-test
    python -m app.main start-48h-test
"""
import os
import sys

# Ensure the project root is on PYTHONPATH when running as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import app  # noqa: E402

if __name__ == "__main__":
    app()
