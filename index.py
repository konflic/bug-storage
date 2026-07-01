"""Local dev entrypoint.

Run the API without Docker:

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python index.py            # serves on http://localhost:8000

Equivalent to: uvicorn app.main:app --reload
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
