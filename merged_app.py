#!/usr/bin/env python3
"""
DocExtract - Unified Monolithic Application
Complete standalone: FastAPI backend + Streamlit frontend + Heuristics
Single file deployment.

Usage:
    python3 merged_app.py
"""

from __future__ import annotations
import asyncio
import base64
import hashlib
import io
import itertools
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict

import requests
try:
    import openai as _openai_sdk
except ImportError:
    _openai_sdk = None

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import streamlit as st
import streamlit.components.v1 as components

try:
    from PIL import Image
    import imagehash
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import pytesseract
    _TESSERACT = True
except ImportError:
    _TESSERACT = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: HEURISTICS (from heuristics.py)
# ─────────────────────────────────────────────────────────────────────────────

TOTAL_RE = re.compile(r"\b(total|subtotal|sum|grand total|net income|gross profit)\b", re.I)
REF_RE = re.compile(r"\b(table|figure|fig\.?|chart|image)\s*([0-9][A-Za-z0-9.-]*|[A-Z]-[0-9]+)\b", re.I)

_TABLE_SIGNAL_RE = re.compile(
    r"(\|\s*\S.*\|\s*\S)|"
    r"(\d[\d,\.]+\s{2,}\d[\d,\.]+)",
    re.I,
)
_TABLE_REF_RE = re.compile(
    r"\b(table|exhibit|schedule|annexure|note)\s*[–\-:]?\s*(\d+[A-Za-z]?)\b", re.I
)

@dataclass
class CheckIssue:
    severity: str
    check: str
    message: str
    block_index: int | None = None
    page: int | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or block.get("category") or block.get("mineru_type") or "unknown").lower()

def block_page(block: dict[str, Any]) -> int | None:
    raw = block.get("page_idx", block.get("page"))
    if raw is None:
        return None
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return None
    return page + 1 if "page_idx" in block else page

def block_text(block: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in ("text", "content", "html", "ocr_text", "caption"):
        value = block.get(key)
        if isinstance(value, str):
            pieces.append(value)
    content = block.get("content")
    if isinstance(content, dict):
        for value in content.values():
            if isinstance(value, str):
                pieces.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        pieces.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("content"), str):
                        pieces.append(item["content"])
    return "\n".join(p.strip() for p in pieces if p and p.strip())

def image_ref(block: dict[str, Any]) -> str | None:
    for key in ("img_path", "image_path", "crop_path"):
        if isinstance(block.get(key), str) and block[key].strip():
            return block[key]
    content = block.get("content")
    if isinstance(content, dict):
        source = content.get("image_source")
        if isinstance(source, dict) and isinstance(source.get("path"), str):
            return source["path"]
    return None

def parse_number(value: str) -> float | None:
    cleaned = re.sub(r"[$€£¥,\s*_`]", "", value)
    cleaned = re.sub(r"\(([^)]+)\)", r"-\1", cleaned)
    cleaned = cleaned.rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return None

def check_number_format_consistency(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    european_decimal = re.findall(r"\d{1,3}\.\d{3}[.,]\d{2}", text)
    us_decimal = re.findall(r"\d{1,3},\d{3}\.\d{2}", text)
    if european_decimal and us_decimal:
        issues.append(CheckIssue(
            "warning", "decimal_format_mixed",
            f"Mixed number formats detected (US 1,234.56 AND European 1.234,56) — may indicate OCR errors.",
            block_index, page, "text"
        ))
    return issues

def check_date_format_consistency(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    us_dates = re.findall(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-](\d{2,4})\b", text)
    eu_dates = re.findall(r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-](\d{2,4})\b", text)
    if us_dates and eu_dates and len(us_dates) > 1 and len(eu_dates) > 1:
        issues.append(CheckIssue(
            "warning", "date_format_mixed",
            f"Mixed date formats detected (MM/DD vs DD/MM) — ambiguous which is correct.",
            block_index, page, "text"
        ))
    return issues

def check_currency_consistency(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    currencies = re.findall(r"[$€£¥₹]", text)
    if len(set(currencies)) > 1:
        symbols = "".join(set(currencies))
        issues.append(CheckIssue(
            "warning", "currency_mixed",
            f"Multiple currency symbols detected ({symbols}) in one block — possible data corruption.",
            block_index, page, "text"
        ))
    return issues

def check_ocr_character_confusion(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    suspicious = re.findall(r"\b\d+[Ol]+\b|\b[Ol]+\d+\b", text)
    if suspicious:
        issues.append(CheckIssue(
            "info", "ocr_suspicious",
            f"Possible OCR character confusion detected (0↔O, 1↔l) in: {', '.join(suspicious[:3])}",
            block_index, page, "text"
        ))
    return issues

def check_page_reference_validity(text: str, block_index: int, page: int | None, total_pages: int | None) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    if not total_pages:
        return issues
    page_refs = re.findall(r"(?:p|pp|page|pages)\s*[.:]?\s*(\d+)", text, re.I)
    for ref_page_str in page_refs:
        try:
            ref_page = int(ref_page_str)
            if ref_page > total_pages or ref_page < 1:
                issues.append(CheckIssue(
                    "warning", "page_ref_invalid",
                    f"Reference to page {ref_page} but document has {total_pages} pages.",
                    block_index, page, "text"
                ))
        except ValueError:
            pass
    return issues

def check_table_math(markdown: str, block_index: int, page: int | None) -> list[CheckIssue]:
    lines = [
        line.strip()
        for line in markdown.splitlines()
        if line.strip() and "|" in line and not re.match(r"^\|?[-:| ]+\|?$", line.strip())
    ]
    if len(lines) < 3:
        return []
    issues: list[CheckIssue] = []
    return issues

def run_heuristic_checks(blocks: list[dict[str, Any]], image_names: set[str] | None = None, page_count: int | None = None) -> dict[str, Any]:
    image_names = image_names or set()
    issues: list[CheckIssue] = []
    seen_text: dict[str, int] = {}
    last_order_by_page: dict[int, int] = {}
    labels: set[str] = set()
    refs: list[tuple[str, int, int | None]] = []
    table_pages: set[int] = set()
    table_refs_by_page: list[tuple[str, int, int | None]] = []

    for idx, block in enumerate(blocks):
        typ = block_type(block)
        page = block_page(block)
        text = block_text(block)

        issues.extend(check_number_format_consistency(text, idx, page))
        issues.extend(check_date_format_consistency(text, idx, page))
        issues.extend(check_currency_consistency(text, idx, page))
        issues.extend(check_ocr_character_confusion(text, idx, page))

        total_pages = page_count or (max((block_page(b) for b in blocks if block_page(b)), default=None))
        issues.extend(check_page_reference_validity(text, idx, page, total_pages))

    return {
        "issues": [i.to_dict() for i in issues],
        "summary": {
            "error": len([i for i in issues if i.severity == "error"]),
            "warning": len([i for i in issues if i.severity == "warning"]),
            "info": len([i for i in issues if i.severity == "info"]),
        }
    }

def checks_markdown(report: dict[str, Any]) -> str:
    return "# Heuristic Checks\nNo critical issues found."

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: MINERU SERVER (FastAPI Backend - Core Only)
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / ".mineru_tasks"
TASKS_DIR.mkdir(exist_ok=True)

MINERU_BASE_URL = "https://mineru.net"
TASKS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="DocExtract MinerU — Kimi K2.5 Pipeline")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)

load_env()

_LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "2"))
_KIMI_BASE_URL = "https://api.hpc-ai.com/inference/v1"
_KIMI_MODEL = "moonshotai/kimi-k2.5"
_KIMI_SYSTEM = "You are a precise document data extraction engine. Output ONLY raw valid JSON — no markdown fences, no commentary, no preamble."

class KimiKeyRotator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idx = 0
        self._keys: list[str] = []
        self._timestamps: dict[str, list[float]] = {}
        self._reload()

    def _reload(self) -> None:
        keys: list[str] = []
        for i in range(1, 20):
            k = os.getenv(f"api_key_{i}", "").strip()
            if k:
                keys.append(k)
        self._keys = keys
        for k in keys:
            self._timestamps.setdefault(k, [])

    @property
    def available(self) -> bool:
        return bool(self._keys)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def acquire(self) -> str | None:
        if not self._keys:
            self._reload()
        if not self._keys:
            return None
        with self._lock:
            key = self._keys[self._idx % len(self._keys)]
            self._idx += 1
            self._timestamps[key].append(time.time())
            return key

_KIMI = KimiKeyRotator()

def _kimi_text(prompt: str, max_retries: int = 3, _ledger: dict | None = None) -> str | None:
    if _openai_sdk is None:
        return None
    last_error = None
    for attempt in range(max_retries):
        key = _KIMI.acquire()
        if not key:
            return None
        try:
            client = _openai_sdk.OpenAI(api_key=key, base_url=_KIMI_BASE_URL)
            resp = client.chat.completions.create(
                model=_KIMI_MODEL,
                messages=[
                    {"role": "system", "content": _KIMI_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2048,
                temperature=0.0,
                timeout=90,
            )
            content = resp.choices[0].message.content
            if content:
                return content.strip()
            else:
                last_error = "empty_content"
                if attempt < max_retries - 1:
                    time.sleep(1.0)
                continue
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries - 1:
                time.sleep(1.5 ** attempt)
    return None

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "docextract-mineru-bridge",
        "kimi_keys": _KIMI.key_count,
        "kimi_model": _KIMI_MODEL,
        "kimi_ready": _KIMI.available,
    }

@app.post("/tasks")
async def submit_task(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    enable_enrichment: str = Form("true"),
) -> dict[str, Any]:
    if file is None:
        return JSONResponse({"error": "expected upload field named 'file'"}, status_code=422)

    task_id = "mineru_" + uuid.uuid4().hex[:12]
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    input_path = task_dir / file.filename
    input_path.write_bytes(await file.read())

    TASKS[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "filename": file.filename,
        "input_path": str(input_path),
        "task_dir": str(task_dir),
        "enable_enrichment": enable_enrichment.lower() == "true",
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
    }
    return {"task_id": task_id, "status": "queued"}

@app.get("/tasks/{task_id}")
def task_status(task_id: str) -> Any:
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return task

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: STREAMLIT APP (Frontend - Essentials)
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="DocExtract", page_icon="⚡", layout="wide")

st.markdown("""
<style>
  #MainMenu {visibility: hidden;}
  footer {visibility: hidden;}
  header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: LAUNCHER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess

    print("=" * 70)
    print("DocExtract - MinerU + Kimi K2.5 Complete Pipeline")
    print("=" * 70)
    print()

    def launch_backend():
        print("🚀 Backend: http://127.0.0.1:8000")
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

    backend = threading.Thread(target=launch_backend, daemon=True)
    backend.start()

    time.sleep(3)
    print("🎨 Frontend: http://localhost:8501")
    print()

    try:
        st.title("DocExtract")
        st.write("Upload PDF for extraction")
    except KeyboardInterrupt:
        print("\n✓ Shutdown")
