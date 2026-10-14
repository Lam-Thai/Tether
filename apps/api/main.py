"""Entrypoint. Vercel's Python runtime auto-detects a FastAPI instance named
`app` in `main.py` — keep this exact filename/variable.

Local dev: `uv run uvicorn main:app --reload` (run from inside apps/api/).
"""

from core.app import create_app

app = create_app()
