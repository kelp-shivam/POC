#!/usr/bin/env python3
"""
DocExtract - Streamlit Cloud Deployment
Single app.py for Streamlit Cloud (https://streamlit.app)
Backend deployed separately (Railway/Heroku/AWS)

Deploy: git push to your Streamlit Cloud repo
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & STATE
# ─────────────────────────────────────────────────────────────────────────────

# Backend endpoint - change this to your deployed backend URL
BACKEND_URL = os.getenv(
    "DOCEXTRACT_BACKEND_URL",
    "http://127.0.0.1:8000"  # Local default, override in Streamlit secrets
)

st.set_page_config(
    page_title="DocExtract · MinerU + Kimi K2.5",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global CSS
st.markdown("""
<style>
  #MainMenu {visibility: hidden;}
  footer {visibility: hidden;}
  header {visibility: hidden;}
  .block-container {
    padding-top: 0.5rem; padding-bottom: 0rem;
    padding-left: 0rem; padding-right: 0rem;
    max-width: 100%;
  }
  .stApp {background: #080b14;}

  /* Sidebar */
  [data-testid="stSidebar"] {
    background: rgba(10,14,26,0.98);
    border-right: 1px solid rgba(80,100,200,0.14);
  }
  [data-testid="stSidebar"] * {color: #e4eaf5;}
  [data-testid="stSidebar"] .stButton > button {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: white; border: none; border-radius: 8px;
    font-weight: 600; width: 100%;
  }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if "task_id" not in st.session_state:
    st.session_state.task_id = ""
if "status" not in st.session_state:
    st.session_state.status = {}
if "blocks" not in st.session_state:
    st.session_state.blocks = []
if "enrichment_md" not in st.session_state:
    st.session_state.enrichment_md = ""
if "source_pdf_b64" not in st.session_state:
    st.session_state.source_pdf_b64 = None
if "endpoint" not in st.session_state:
    st.session_state.endpoint = BACKEND_URL
if "auto_poll" not in st.session_state:
    st.session_state.auto_poll = False
if "poll_interval" not in st.session_state:
    st.session_state.poll_interval = 10
if "last_poll_time" not in st.session_state:
    st.session_state.last_poll_time = 0

# ─────────────────────────────────────────────────────────────────────────────
# BACKEND API FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def check_backend() -> bool:
    """Verify backend is online"""
    try:
        resp = requests.get(f"{st.session_state.endpoint}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False

def submit_task(
    file_bytes: bytes,
    filename: str,
    enable_formula: bool,
    enable_table: bool,
    enable_enrichment: bool,
    language: str,
    page_ranges: str,
    is_ocr: bool,
    enable_caption: bool,
    drop_icons: bool,
) -> str:
    """Submit PDF to backend for processing"""
    files = {"file": (filename, io.BytesIO(file_bytes), "application/pdf")}
    data = {
        "model_version": "vlm",
        "enable_formula": str(enable_formula).lower(),
        "enable_table": str(enable_table).lower(),
        "enable_enrichment": str(enable_enrichment).lower(),
        "language": language,
        "page_ranges": page_ranges,
        "is_ocr": str(is_ocr).lower(),
    }

    resp = requests.post(
        f"{st.session_state.endpoint}/tasks",
        files=files,
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["task_id"]

def poll_task(task_id: str) -> dict[str, Any]:
    """Get task status"""
    resp = requests.get(
        f"{st.session_state.endpoint}/tasks/{task_id}",
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def download_result(task_id: str) -> bytes:
    """Download result ZIP"""
    resp = requests.get(
        f"{st.session_state.endpoint}/tasks/{task_id}/result",
        timeout=900,
    )
    resp.raise_for_status()
    return resp.content

# ─────────────────────────────────────────────────────────────────────────────
# MAIN UI
# ─────────────────────────────────────────────────────────────────────────────

st.title("⚡ DocExtract")
st.subheader("MinerU + Kimi K2.5 Document Intelligence Pipeline")

# Check backend
with st.spinner("Checking backend..."):
    backend_ok = check_backend()

if not backend_ok:
    st.error(f"❌ Backend unavailable at {st.session_state.endpoint}")
    st.info("**Local setup:** Run `PORT=8000 python3 -m mineru_server` in another terminal")
    st.info("**Streamlit Cloud:** Set `DOCEXTRACT_BACKEND_URL` in Streamlit secrets to deployed backend URL")
    st.stop()

# Sidebar
with st.sidebar:
    st.markdown("## 📤 Submit Document")

    uploaded = st.file_uploader("PDF file", type=["pdf"], label_visibility="collapsed")

    if uploaded:
        model = st.selectbox("MinerU backend", ["vlm", "pipeline"], index=0)

        col1, col2 = st.columns(2)
        with col1:
            enable_formula = st.checkbox("∑ Formula", True)
            enable_table = st.checkbox("⊞ Tables", True)
            enable_enrichment = st.checkbox("🤖 Enrich (Vision LLM)", True)
        with col2:
            enable_caption = st.checkbox("✨ Captions", True)
            drop_icons = st.checkbox("🚫 Drop Icons", True)

        language = st.text_input("Language", "en")
        page_ranges = st.text_input("Page ranges", placeholder="e.g. 1-3,5")
        is_ocr = st.checkbox("Force OCR", False)

        if st.button("⚡ Submit to Pipeline", type="primary", use_container_width=True):
            try:
                with st.spinner("Submitting…"):
                    task_id = submit_task(
                        uploaded.getvalue(),
                        uploaded.name,
                        enable_formula,
                        enable_table,
                        enable_enrichment,
                        language,
                        page_ranges,
                        is_ocr,
                        enable_caption,
                        drop_icons,
                    )
                st.session_state.task_id = task_id
                st.success(f"✅ Task submitted: `{task_id}`")
                st.info("📋 Task ID copied. Use in **Poll/Resume** section to check status.")
            except Exception as exc:
                st.error(f"Submit failed: {exc}")

    st.divider()

    st.markdown("## 🔄 Poll / Resume")
    task_id_input = st.text_input(
        "Task ID", st.session_state.task_id, label_visibility="collapsed",
        placeholder="Paste task ID…"
    )
    if task_id_input:
        st.session_state.task_id = task_id_input

    col_poll, col_auto = st.columns([1, 1])
    with col_poll:
        manual_poll = st.button("📡 Poll", disabled=not st.session_state.task_id, use_container_width=True)
    with col_auto:
        st.session_state.auto_poll = st.checkbox("Auto", st.session_state.auto_poll)

    if st.session_state.auto_poll:
        interval = st.slider("Interval (s)", 5, 60, st.session_state.poll_interval, 5)
        st.session_state.poll_interval = interval

    if st.session_state.task_id:
        if manual_poll or (st.session_state.auto_poll and (time.time() - st.session_state.last_poll_time) >= st.session_state.poll_interval):
            st.session_state.last_poll_time = time.time()
            try:
                status = poll_task(st.session_state.task_id)
                st.session_state.status = status

                s = (status.get("status") or "").lower()
                if s in ("completed", "done"):
                    with st.spinner("Downloading result ZIP…"):
                        data = download_result(st.session_state.task_id)
                    # Parse ZIP
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        for member in zf.namelist():
                            if member.endswith("enrichment.md"):
                                st.session_state.enrichment_md = zf.read(member).decode('utf-8', errors='ignore')
                            if member.endswith("content_list_enriched.json"):
                                st.session_state.blocks = json.loads(zf.read(member))
                    st.session_state.auto_poll = False
                    st.success("✅ Result loaded!")
                    st.rerun()
                elif s in ("failed", "error"):
                    st.error(status.get("error", "Task failed."))
                    st.session_state.auto_poll = False
                else:
                    st.info(f"Status: **{s}**")
            except Exception as exc:
                st.error(f"Poll failed: {exc}")

# Main content area
if st.session_state.status:
    st.markdown("## 📊 Results")

    # Display timing & cost
    if st.session_state.status.get("timings"):
        t = st.session_state.status.get("timings", {})
        v = st.session_state.status.get("visual_stats", {})

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("⏱️ Timing")
            st.metric("MinerU", f"{(t.get('mineru_ms', 0))/1000:.1f}s")
            st.metric("Enrichment", f"{(t.get('enrich_ms', 0))/1000:.1f}s")
            st.metric("Total", f"{(t.get('total_ms', 0))/1000:.1f}s")

        with col2:
            st.subheader("💰 Cost")
            cost_llm = (v.get('input_tokens', 0) * 0.30 + v.get('output_tokens', 0) * 1.50) / 1e6
            cost_total = 0.05 + cost_llm
            st.metric("LLM", f"${cost_llm:.4f}")
            st.metric("Total", f"${cost_total:.4f}")

    st.divider()

    # Display enrichment report
    if st.session_state.enrichment_md:
        st.subheader("📋 Enrichment Report")
        st.markdown(st.session_state.enrichment_md)
    else:
        st.info("Processing or result not available yet.")

# Footer
st.divider()
st.caption("🚀 Powered by MinerU (extraction) + Kimi K2.5 (enrichment)")
st.caption(f"Backend: {st.session_state.endpoint}")
