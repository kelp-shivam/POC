#!/usr/bin/env python3
"""
DocExtract - Unified Deployment (FastAPI + Streamlit)
Single file: runs both backend API and frontend UI in one process.

Usage:
    python3 app.py
"""
import sys
import time
import threading
from pathlib import Path

def start_backend():
    """Start FastAPI backend on port 8000"""
    import os
    os.environ['PORT'] = '8000'

    try:
        import mineru_server
        import uvicorn

        print("🚀 Starting FastAPI backend on http://127.0.0.1:8000")
        uvicorn.run(
            mineru_server.app,
            host="127.0.0.1",
            port=8000,
            log_level="warning"
        )
    except Exception as e:
        print(f"❌ Backend failed: {e}")
        sys.exit(1)

def start_frontend():
    """Start Streamlit frontend on port 8501"""
    import subprocess

    time.sleep(3)  # Wait for backend to start
    print("🎨 Starting Streamlit frontend on http://localhost:8501")

    try:
        subprocess.run([
            sys.executable, "-m", "streamlit", "run",
            str(Path(__file__).parent / "streamlit_app.py"),
            "--client.toolbarMode", "minimal",
            "--logger.level", "error",
        ])
    except Exception as e:
        print(f"❌ Frontend failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("=" * 60)
    print("DocExtract - MinerU + Kimi K2.5 Pipeline")
    print("=" * 60)
    print()

    # Start backend in background thread
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()

    # Start frontend in main thread (blocks)
    try:
        start_frontend()
    except KeyboardInterrupt:
        print("\n✓ Shutdown")
        sys.exit(0)
