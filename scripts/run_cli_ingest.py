"""Thin wrapper so the legacy CLI ingest job can still be run as a script.

Usage: python scripts/run_cli_ingest.py
"""

from __future__ import annotations

from app.jobs.cli_ingest import main

if __name__ == "__main__":
    main()
