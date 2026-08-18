"""Windows-friendly desktop launcher for the local HireMS Web UI."""
from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn
from app.main import app


def open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:8765/")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
