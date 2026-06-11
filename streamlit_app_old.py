"""
DocExtract — Streamlit UI  (Final Production Version)
======================================================
Features:
  - Full HTML/PDF viewer (ext/index.html embedded)
  - Real-time pipeline stage progress panel
  - Final merged document (content + AI corrections inline)
  - Block type distribution chart in sidebar
  - Auto-poll with configurable interval
  - Page range selector (default 20, custom on demand)
  - Heuristic quality check panel with severity colours
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components

from heuristics import block_page, block_text, block_type, checks_markdown, image_ref, run_heuristic_checks

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# ─────────────────────────────────────────────────────────────────────────────
#  Auto-start FastAPI backend (runs once per Streamlit Cloud process)
# ─────────────────────────────────────────────────────────────────────────────
import threading as _threading

_BACKEND_STARTED = False

def _launch_backend() -> None:
    """Only launch backend if no external URL configured (local dev mode)."""
    global _BACKEND_STARTED
    _BACKEND_STARTED = True
    try:
        import nest_asyncio as _nest; _nest.apply()
        import uvicorn as _uvi
        import mineru_server as _ms
        _uvi.run(_ms.app, host="127.0.0.1", port=8000, log_level="error")
    except Exception:
        pass

# Detect environment: local dev vs Render deployment
_IS_RENDER = bool(os.getenv("RENDER"))
_EXTERNAL_BACKEND = bool(os.getenv("DOCEXTRACT_BRIDGE_URL"))

# Auto-start backend only in local dev mode
if not _BACKEND_STARTED and not _EXTERNAL_BACKEND and not _IS_RENDER:
    _threading.Thread(target=_launch_backend, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────
# Default endpoint: Render in production, local in dev
if os.getenv("DOCEXTRACT_BRIDGE_URL"):
    DEFAULT_ENDPOINT = os.getenv("DOCEXTRACT_BRIDGE_URL")
elif _IS_RENDER:
    DEFAULT_ENDPOINT = "https://docextract-backend.onrender.com"
else:
    DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

st.set_page_config(
    page_title="DocExtract · MinerU + Azure GPT-4o-mini",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
#  Global CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  #MainMenu {visibility: hidden;}
  footer     {visibility: hidden;}
  header     {visibility: hidden;}
  .block-container {
    padding-top: 0.5rem; padding-bottom: 0rem;
    padding-left: 0rem; padding-right: 0rem;
    max-width: 100%;
  }
  .stApp { background: #080b14; }

  /* ── Sidebar ── */
  [data-testid="stSidebar"] {
    background: rgba(10,14,26,0.98);
    border-right: 1px solid rgba(80,100,200,0.14);
  }
  [data-testid="stSidebar"] * { color: #e4eaf5; }
  [data-testid="stSidebar"] .stButton > button {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: white; border: none; border-radius: 8px;
    font-weight: 600; width: 100%;
  }
  [data-testid="stSidebar"] .stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(99,102,241,0.5);
  }
  [data-testid="stSidebar"] input,
  [data-testid="stSidebar"] textarea,
  [data-testid="stSidebar"] select {
    background: rgba(0,0,0,0.3) !important;
    border: 1px solid rgba(80,100,200,0.25) !important;
    color: #e4eaf5 !important;
    border-radius: 7px !important;
  }
  [data-testid="stSidebar"] label {
    color: #8fa3c0 !important; font-size: 12px !important;
    font-weight: 500 !important;
  }

  /* ── Sidebar section labels ── */
  .sidebar-title {
    font-size: 11px; font-weight: 700; color: #a5b4fc;
    text-transform: uppercase; letter-spacing: 0.07em;
    margin: 10px 0 4px;
  }

  /* ── Status pills ── */
  .status-pill {
    display: inline-block; padding: 3px 10px;
    border-radius: 20px; font-size: 11px; font-weight: 600;
  }
  .status-ready { background: rgba(16,185,129,0.15); color: #10b981; border: 1px solid rgba(16,185,129,0.35); }
  .status-busy  { background: rgba(99,102,241,0.15); color: #818cf8;  border: 1px solid rgba(99,102,241,0.35); }
  .status-error { background: rgba(239,68,68,0.15);  color: #f87171;  border: 1px solid rgba(239,68,68,0.35); }
  .status-warn  { background: rgba(245,158,11,0.15); color: #fbbf24;  border: 1px solid rgba(245,158,11,0.35); }

  /* ── Pipeline stage cards ── */
  .stage-card {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 14px; border-radius: 8px;
    background: rgba(14,19,38,0.80);
    border: 1px solid rgba(80,100,200,0.14);
    margin-bottom: 5px; font-size: 12px;
  }
  .stage-done  { border-color: rgba(16,185,129,0.4); }
  .stage-active{ border-color: rgba(99,102,241,0.7); animation: pulseBorder 1.4s infinite; }
  .stage-fail  { border-color: rgba(239,68,68,0.5);  }
  @keyframes pulseBorder {
    0%,100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); }
    50%      { box-shadow: 0 0 0 3px rgba(99,102,241,0.25); }
  }

  /* ── Heuristic severity ── */
  .sev-error   { color: #f87171; font-weight: 600; }
  .sev-warning { color: #fbbf24; font-weight: 600; }
  .sev-info    { color: #60a5fa; font-weight: 600; }

  /* ── Block type badge ── */
  .block-badge {
    display: inline-block; padding: 1px 7px;
    border-radius: 10px; font-size: 11px; font-weight: 600;
    border: 1px solid;
  }

  /* ── enrichment callout ── */
  .enrichment-box {
    background: rgba(99,102,241,0.07); border-left: 3px solid #6366f1;
    border-radius: 0 8px 8px 0; padding: 10px 14px; margin: 8px 0;
    font-size: 12.5px; color: #c7d2fe;
  }
  .correction-box {
    background: rgba(245,158,11,0.07); border-left: 3px solid #f59e0b;
    border-radius: 0 8px 8px 0; padding: 10px 14px; margin: 8px 0;
    font-size: 12.5px; color: #fde68a;
  }
  .summary-box {
    background: rgba(16,185,129,0.07); border-left: 3px solid #10b981;
    border-radius: 0 8px 8px 0; padding: 10px 14px; margin: 8px 0;
    font-size: 12.5px; color: #6ee7b7;
  }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Session State
# ─────────────────────────────────────────────────────────────────────────────
def init_state() -> None:
    defaults = {
        "endpoint":         DEFAULT_ENDPOINT,
        "task_id":          "",
        "status":           None,
        "zip_bytes":        None,
        "zip_name":         "mineru_result.zip",
        "extract_dir":      None,
        "blocks":           [],
        "markdown":         "",
        "final_merged_md":  "",
        "visual_summaries": [],
        "checks":           None,
        "source_pdf_bytes": None,
        "layout_pdf_bytes": None,
        "type_counts":      {},
        "word_count":       0,
        "page_count":       0,
        "lab_results":      {},
        "lab_models":       [],
        "image_map_b64":    {},
        "content_list_raw": None,
        "auto_poll":        False,
        "poll_interval":    10,
        "last_poll_time":   0.0,
        "compare_result":   None,
        "extract_result":   None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


# ─────────────────────────────────────────────────────────────────────────────
#  ZIP / Result Helpers
# ─────────────────────────────────────────────────────────────────────────────
def zip_extract_dir() -> Path:
    base = Path(tempfile.gettempdir()) / "docextract_streamlit"
    base.mkdir(exist_ok=True)
    return base


def safe_extract_zip(data: bytes, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                continue
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))


def find_first(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.rglob(pattern))
        if matches:
            return matches[0]
    return None


def load_json_file(path: Path | None, fallback: Any) -> Any:
    if not path or not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return fallback


def flatten_content(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        out = []
        for page_idx, page in enumerate(raw):
            for order, block in enumerate(page):
                if isinstance(block, dict):
                    item = dict(block)
                    item.setdefault("page_idx", page_idx)
                    item.setdefault("order", order)
                    out.append(item)
        return out
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    return []


def load_result_zip(data: bytes, name: str = "mineru_result.zip") -> None:
    target = zip_extract_dir() / f"result_{int(time.time() * 1000)}"
    safe_extract_zip(data, target)

    md_path = (
        find_first(target, ["final_merged.md"])  # new merged markdown (content + all enrichments)
        or find_first(target, ["full_enriched.md"])
        or find_first(target, ["full.md"])
        or find_first(target, ["output.md"])
        or next((p for p in sorted(target.rglob("*.md"))
                 if p.name not in {"visual_summaries.md", "heuristic_checks.md", "enrichment.md", "final_merged.md"}), None)
    )
    enrichment_md_path = find_first(target, ["enrichment.md"])  # legacy enrichment summary
    content_path = (
        find_first(target, ["content_list_enriched.json"])
        or find_first(target, ["*content_list*.json"])
    )
    visual_path = find_first(target, ["visual_summaries.json"])
    checks_path = find_first(target, ["heuristic_checks.json"])

    raw_blocks = load_json_file(content_path, [])
    blocks     = flatten_content(raw_blocks)
    images     = {p.name for p in target.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS}
    checks     = load_json_file(checks_path, None) or run_heuristic_checks(blocks, images)

    # Stats
    type_counts: dict[str, int] = {}
    for b in blocks:
        t = str(b.get("type") or "unknown").lower()
        type_counts[t] = type_counts.get(t, 0) + 1
    word_count = sum(
        len((b.get("text") or b.get("content") or "").split())
        for b in blocks if isinstance(b.get("text") or b.get("content"), str)
    )
    page_count = len({b.get("page_idx", 0) for b in blocks})

    # Build base64 image map
    image_map_b64: dict[str, str] = {}
    for p in target.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            try:
                ext  = p.suffix.lower().lstrip(".")
                mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                        "gif": "gif", "webp": "webp", "bmp": "bmp"}.get(ext, "png")
                b64  = base64.b64encode(p.read_bytes()).decode("ascii")
                url  = f"data:image/{mime};base64,{b64}"
                rel  = str(p.relative_to(target)).replace("\\", "/")
                image_map_b64[rel]               = url
                image_map_b64[p.name]            = url
                image_map_b64["images/" + p.name] = url
            except Exception:
                pass

    layout_pdf_path = find_first(target, ["*layout*.pdf"])

    st.session_state.zip_bytes        = data
    st.session_state.zip_name         = name
    st.session_state.extract_dir      = str(target)
    st.session_state.blocks           = blocks
    st.session_state.markdown         = md_path.read_text(encoding="utf-8", errors="ignore") if md_path else ""
    st.session_state.final_merged_md    = (
        md_path.read_text(encoding="utf-8", errors="ignore") if md_path else ""
    )
    st.session_state.enrichment_md    = (
        enrichment_md_path.read_text(encoding="utf-8", errors="ignore") if enrichment_md_path else ""
    )
    st.session_state.visual_summaries = load_json_file(visual_path, [])
    st.session_state.checks           = checks
    st.session_state.layout_pdf_bytes = layout_pdf_path.read_bytes() if layout_pdf_path else None
    st.session_state.type_counts      = type_counts
    st.session_state.word_count       = word_count
    st.session_state.page_count       = page_count
    st.session_state.image_map_b64    = image_map_b64
    st.session_state.content_list_raw = raw_blocks


# ─────────────────────────────────────────────────────────────────────────────
#  API Helpers
# ─────────────────────────────────────────────────────────────────────────────
def submit_to_bridge(file_bytes: bytes, filename: str, options: dict[str, Any]) -> str:
    files = {"files": (filename, io.BytesIO(file_bytes), "application/pdf")}
    resp  = requests.post(
        f"{st.session_state.endpoint.rstrip('/')}/tasks",
        files=files, data=options, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["task_id"]


def poll_task_status(task_id: str) -> dict[str, Any]:
    resp = requests.get(
        f"{st.session_state.endpoint.rstrip('/')}/tasks/{task_id}", timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def download_result(task_id: str) -> bytes:
    resp = requests.get(
        f"{st.session_state.endpoint.rstrip('/')}/tasks/{task_id}/result", timeout=900
    )
    resp.raise_for_status()
    return resp.content


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline Stage Display
# ─────────────────────────────────────────────────────────────────────────────
_STAGE_DEFS = [
    ("queued",          "📋", "Queued"),
    ("submitting",      "📤", "Submitting to MinerU"),
    ("waiting_mineru",  "⚙️", "MinerU Processing (OCR + Layout)"),
    ("downloading",     "⬇️", "Downloading Result ZIP"),
    ("fixing_tables",   "🔧", "Fixing Misclassified Tables"),
    ("enriching_tables","📊", "Enriching Tables (GPT-4o-mini)"),
    ("enriching_visuals","🖼️","Enriching Visuals (GPT-4o-mini)"),
    ("enriching",       "✨", "Post-Processing"),
    ("completed",       "✅", "Completed"),
    ("failed",          "❌", "Failed"),
]
_STAGE_ORDER = [s[0] for s in _STAGE_DEFS]


def render_pipeline_stages(status: dict[str, Any]) -> None:
    current = (status.get("status") or "").lower()
    st.markdown("##### Pipeline Progress")
    for stage_key, icon, label in _STAGE_DEFS:
        if stage_key in ("queued", "failed", "completed"):
            show = stage_key == current or (stage_key == "completed" and current == "completed")
            if not show and stage_key not in (current,):
                continue
        # Determine card state
        curr_idx  = _STAGE_ORDER.index(current) if current in _STAGE_ORDER else -1
        stage_idx = _STAGE_ORDER.index(stage_key)
        if current == "failed" and stage_key == "failed":
            css = "stage-fail"
            icon = "❌"
        elif stage_idx < curr_idx:
            css = "stage-done"
        elif stage_idx == curr_idx:
            css = "stage-active"
        else:
            continue  # future stage — don't show

        extra = ""
        if stage_key == "enriching_visuals":
            done  = status.get("visuals_done", 0)
            total = status.get("visuals_sent_to_llm", 0)
            if total:
                extra = f" ({done}/{total})"
        if stage_key == "fixing_tables":
            fixed = status.get("table_fixes_applied", "")
            if fixed != "":
                extra = f" ({fixed} fixed)"
        if stage_key == "failed":
            extra = f": {str(status.get('error', ''))[:80]}"

        st.markdown(
            f'<div class="stage-card {css}">{icon} <b>{label}</b>{extra}</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Sidebar
# ─────────────────────────────────────────────────────────────────────────────
def render_sidebar() -> None:
    with st.sidebar:
        # Brand
        st.markdown("""
        <div style="display:flex;align-items:center;gap:10px;padding:12px 0 16px;">
            <div style="width:36px;height:36px;background:linear-gradient(135deg,#6366f1,#8b5cf6,#06b6d4);
                        border-radius:10px;display:flex;align-items:center;justify-content:center;
                        font-size:18px;box-shadow:0 0 20px rgba(99,102,241,0.4);">⚡</div>
            <div>
                <div style="font-weight:800;font-size:15px;background:linear-gradient(90deg,#a5b4fc,#c4b5fd,#67e8f9);
                            -webkit-background-clip:text;-webkit-text-fill-color:transparent;">DocExtract</div>
                <div style="font-size:9.5px;color:#48597a;text-transform:uppercase;letter-spacing:0.06em;">
                    MinerU · Azure GPT-4o-mini
                </div>
            </div>
        </div>
        <hr style="border:none;border-top:1px solid rgba(80,100,200,0.14);margin:0 0 12px;">
        """, unsafe_allow_html=True)

        # ── Pricing Info ──
        with st.expander("💰 Pricing: Azure GPT-4o-mini vs Alternatives", expanded=True):
            pcol1, pcol2 = st.columns(2)

            with pcol1:
                st.markdown("""**LlamaParse**
- Extraction only
- **$0.0125**/page
- Fastest
- No enrichment""")

            with pcol2:
                st.markdown("""**MinerU + Azure GPT-4o-mini** ⭐ (Current)
- Full enrichment
- **$0.05** doc(20 pages)
- **$0.15**/1M input
- **$0.60**/1M output
- ~$0.06–$0.12/page""")

            st.info("✓ All processing at backend. Live cost breakdown in 'Summary & Cost' tab.")

        # ── Endpoint ──
        st.markdown('<div class="sidebar-title">🔗 Bridge Endpoint</div>', unsafe_allow_html=True)
        st.session_state.endpoint = st.text_input(
            "Bridge URL", st.session_state.endpoint, label_visibility="collapsed"
        )
        if st.button("🔌 Test Connection", use_container_width=True):
                try:
                    r = requests.get(f"{st.session_state.endpoint.rstrip('/')}/health", timeout=5)
                    if r.ok:
                        data = r.json()
                        provider = data.get("llm_provider", "?")
                        model = data.get("llm_model", "?")
                        st.markdown(
                            f'<span class="status-pill status-ready">✓ {provider.upper()} · {model}</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f'<span class="status-pill status-error">HTTP {r.status_code}</span>', unsafe_allow_html=True)
                except Exception:
                    st.markdown('<span class="status-pill status-error">✗ Offline</span>', unsafe_allow_html=True)

        st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)

        # ── Submit PDF ──
        st.markdown('<div class="sidebar-title">📤 Submit Document</div>', unsafe_allow_html=True)
        uploaded = st.file_uploader("PDF file", type=["pdf"], label_visibility="collapsed")

        model = st.selectbox("MinerU backend", ["vlm", "pipeline"], index=0)
        col1, col2 = st.columns(2)
        with col1:
            enable_formula = st.checkbox("∑ Formula", True)
            enable_table   = st.checkbox("⊞ Tables", True)
            enable_enrichment = st.checkbox("🤖 Enrich (Vision LLM)", True, help="Enable Azure GPT-4o-mini enrichment. Disabling speeds up processing.")
        with col2:
            enable_caption = st.checkbox("✨ Captions", True)
            drop_icons     = st.checkbox("🚫 Drop Icons", True)

        language = st.text_input("Language", "en")
        is_ocr   = st.checkbox("🔤 OCR", True, help="Always enabled for best text extraction")

        # Page range selector
        st.markdown('<div class="sidebar-title">📄 Page Range</div>', unsafe_allow_html=True)
        page_mode = st.radio("Process:", ["First 20 pages", "Custom range"], index=0, horizontal=True)
        if page_mode == "First 20 pages":
            page_ranges = "1-20"
        else:
            page_ranges = st.text_input("Custom range", placeholder="e.g. 1-50 or 1-5,10-15", value="")

        if st.button("⚡ Submit to Pipeline", type="primary", disabled=uploaded is None):
            options = {
                "model_version":        model,
                "language":             language,
                "page_ranges":          page_ranges,
                "enable_formula":       str(enable_formula).lower(),
                "enable_table":         str(enable_table).lower(),
                "is_ocr":               str(is_ocr).lower(),
                "enable_image_caption": str(enable_caption).lower(),
                "drop_small_images":    str(drop_icons).lower(),
                "enable_enrichment":    str(enable_enrichment).lower(),
                "return_content_list":  "true",
                "return_md":            "true",
                "response_format_zip":  "true",
            }
            try:
                st.session_state.source_pdf_bytes = uploaded.getvalue()
                with st.spinner("Submitting…"):
                    task_id = submit_to_bridge(uploaded.getvalue(), uploaded.name, options)
                st.session_state.task_id = task_id
                st.session_state.status  = {"status": "queued", "task_id": task_id}

                # Display task ID prominently
                st.success(f"✅ Task submitted: `{task_id}`")
                st.info(f"📋 Task ID copied. Use in **Poll/Resume** section to check status.", icon="ℹ️")
            except Exception as exc:
                st.error(f"Submit failed: {exc}")

        st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)

        # ── Extract by Method ──
        st.markdown('<div class="sidebar-title">🎯 Extract by Method</div>', unsafe_allow_html=True)
        method_pdf = st.file_uploader("PDF for extraction", type=["pdf"], key="method_uploader", label_visibility="collapsed")

        method = st.radio(
            "Choose method:",
            ["All 4 (Compare)", "LlamaParse Only", "Azure DI Only", "MinerU + Azure", "MinerU Raw"],
            index=0,
            horizontal=False
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📤 Extract", type="secondary", disabled=method_pdf is None):
                if method_pdf:
                    endpoint_map = {
                        "All 4 (Compare)": "/compare",
                        "LlamaParse Only": "/extract/llamaparse",
                        "Azure DI Only": "/extract/azure-di",
                        "MinerU + Azure": "/extract/mineru-azure",
                        "MinerU Raw": "/extract/mineru-raw",
                    }
                    endpoint = endpoint_map[method]
                    with st.spinner(f"Extracting with {method}..."):
                        try:
                            resp = requests.post(
                                f"{st.session_state.endpoint.rstrip('/')}{endpoint}",
                                files={"file": method_pdf.getvalue()},
                                timeout=300,
                            )
                            if resp.status_code == 200:
                                if method == "All 4 (Compare)":
                                    st.session_state.compare_result = resp.json()
                                else:
                                    st.session_state.extract_result = resp.json()
                                st.success(f"✅ {method} complete!")
                            else:
                                st.error(f"Extraction failed: HTTP {resp.status_code}")
                        except Exception as e:
                            st.error(f"Extraction error: {e}")

        with col2:
            if st.button("🗑️ Clear", type="tertiary"):
                # Clear all extraction state
                for key in list(st.session_state.keys()):
                    if key not in ["endpoint"]:
                        st.session_state[key] = None
                st.rerun()

        st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)

        # ── Current Task ID ──
        if st.session_state.task_id:
            st.markdown(f'<div style="background: rgba(99,102,241,0.15); padding: 12px; border-radius: 8px; border-left: 4px solid #6366f1; margin-bottom: 16px;"><strong>📋 Current Task:</strong><br><code>{st.session_state.task_id}</code></div>', unsafe_allow_html=True)

        # ── Poll / Resume ──
        st.markdown('<div class="sidebar-title">🔄 Poll / Resume</div>', unsafe_allow_html=True)
        task_id_input = st.text_input(
            "Task ID", st.session_state.task_id, label_visibility="collapsed",
            placeholder="Paste task ID…"
        )
        if task_id_input and task_id_input != st.session_state.task_id:
            st.session_state.task_id = task_id_input

        col_poll, col_auto = st.columns([1, 1])
        with col_poll:
            manual_poll = st.button("📡 Poll", disabled=not st.session_state.task_id,
                                    use_container_width=True)
        with col_auto:
            st.session_state.auto_poll = st.checkbox("Auto", st.session_state.auto_poll)

        if st.session_state.auto_poll and st.session_state.task_id:
            interval = st.slider("Interval (s)", 5, 60, st.session_state.poll_interval, 5)
            st.session_state.poll_interval = interval

        def _do_poll():
            try:
                status = poll_task_status(st.session_state.task_id)
                st.session_state.status = status
                s = (status.get("status") or "").lower()
                if s in ("completed", "done", "success", "finished"):
                    with st.spinner("Downloading result ZIP…"):
                        data = download_result(st.session_state.task_id)
                    load_result_zip(data, f"{st.session_state.task_id}_result.zip")
                    st.session_state.auto_poll = False
                    st.success("✅ Result loaded!")
                    st.rerun()
                elif s in ("failed", "error"):
                    st.error(status.get("error", "Task failed."))
                    st.session_state.auto_poll = False
                else:
                    st.info(f"Status: **{s}** — keep polling.")
            except Exception as exc:
                st.error(f"Poll failed: {exc}")

        if manual_poll:
            _do_poll()

        # Auto-poll on a timer
        if st.session_state.auto_poll and st.session_state.task_id:
            now = time.time()
            if now - st.session_state.last_poll_time >= st.session_state.poll_interval:
                st.session_state.last_poll_time = now
                _do_poll()

        st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)

        # ── Pipeline Progress ──
        if st.session_state.status:
            render_pipeline_stages(st.session_state.status)
            st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)

        # ── Load local ZIP ──
        st.markdown('<div class="sidebar-title">📂 Open Local ZIP</div>', unsafe_allow_html=True)
        local_zip = st.file_uploader("MinerU result ZIP", type=["zip"], label_visibility="collapsed")
        if local_zip:
            if st.button("📦 Open ZIP", use_container_width=True):
                with st.spinner("Loading ZIP…"):
                    load_result_zip(local_zip.getvalue(), local_zip.name)
                st.success("✅ ZIP loaded!")
                st.rerun()

        # ── Download ──
        if st.session_state.zip_bytes:
            st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)
            st.download_button(
                "⬇ Download Enriched ZIP",
                st.session_state.zip_bytes,
                st.session_state.zip_name,
                "application/zip",
                use_container_width=True,
            )

        # ── Block Distribution Chart ──
        if st.session_state.type_counts:
            st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)
            st.markdown('<div class="sidebar-title">📈 Block Distribution</div>', unsafe_allow_html=True)
            # Sort by count
            counts = dict(sorted(st.session_state.type_counts.items(), key=lambda x: -x[1]))
            import pandas as pd
            df = pd.DataFrame({"type": list(counts.keys()), "count": list(counts.values())})
            st.bar_chart(df.set_index("type"), height=160)
            total = sum(counts.values())
            pg    = st.session_state.page_count
            wc    = st.session_state.word_count
            st.caption(f"📄 {pg} pages · 🔲 {total} blocks · 📝 {wc:,} words")

        # ── LLM Cost Panel ──
        vs = (st.session_state.status or {}).get("visual_stats") if st.session_state.status else None
        if vs and vs.get("llm_calls", 0) > 0:
            st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)
            st.markdown('<div class="sidebar-title">💰 LLM Cost (Kimi K2.5)</div>', unsafe_allow_html=True)
            tok_in  = vs.get("input_tokens", 0)
            tok_out = vs.get("output_tokens", 0)
            cached  = vs.get("cached_tokens", 0)
            cost    = vs.get("cost_usd", 0.0)
            calls   = vs.get("llm_calls", 0)
            hits    = vs.get("cache_hits", 0)
            _cached_part = (
                f' 🔒 cached: <b style="color:#34d399">{cached:,}</b>' if cached else ""
            )
            st.markdown(
                f'<div style="font-size:11px;color:#8fa3c0;line-height:1.7;">'
                f'📥 In: <b style="color:#a5b4fc">{tok_in:,}</b> tok'
                f'{_cached_part}<br>'
                f'📤 Out: <b style="color:#f9a8d4">{tok_out:,}</b> tok<br>'
                f'🔁 Calls: <b style="color:#e4eaf5">{calls}</b>  ⚡ Cache hits: <b style="color:#34d399">{hits}</b><br>'
                f'💵 Est. cost: <b style="color:#fbbf24;font-size:13px">${cost:.4f}</b> USD'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Heuristic Checks ──
        if st.session_state.checks:
            st.markdown('<hr style="border:none;border-top:1px solid rgba(80,100,200,0.10);margin:10px 0;">', unsafe_allow_html=True)
            st.markdown('<div class="sidebar-title">🔍 Quality Checks</div>', unsafe_allow_html=True)
            checks = st.session_state.checks
            summary = checks.get("summary", {})
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("🔴 Errors",  summary.get("error",   0))
            cc2.metric("🟡 Warns",   summary.get("warning", 0))
            cc3.metric("🔵 Info",    summary.get("info",    0))
            with st.expander("View details"):
                _render_heuristic_issues(checks)
            st.download_button(
                "heuristic_checks.json",
                json.dumps(checks, indent=2),
                "heuristic_checks.json",
                use_container_width=True,
            )


def _render_heuristic_issues(checks: dict) -> None:
    issues = checks.get("issues", [])
    if not issues:
        st.success("No heuristic issues found.")
        return
    sev_order = {"error": 0, "warning": 1, "info": 2}
    for issue in sorted(issues, key=lambda i: sev_order.get(i.get("severity","info"), 2)):
        sev  = issue.get("severity", "info")
        css  = f"sev-{sev}"
        pg   = f"p{issue['page']}" if issue.get("page") else ""
        blk  = f"b{issue['block_index']}" if issue.get("block_index") is not None else ""
        loc  = f" ({', '.join(x for x in [pg, blk] if x)})" if pg or blk else ""
        icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "•")
        st.markdown(
            f'{icon} <span class="{css}">{issue["check"]}</span>{loc}: {issue["message"]}',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  HTML Viewer (ext/index.html with data injected)
# ─────────────────────────────────────────────────────────────────────────────
def build_viewer_html(
    markdown_text: str,
    content_list_raw: Any,
    image_map_b64: dict[str, str],
    source_pdf_b64: str | None,
    layout_pdf_b64: str | None,
    endpoint: str,
) -> str:
    image_map_json   = json.dumps(image_map_b64)
    content_list_json = json.dumps(content_list_raw) if content_list_raw is not None else "null"
    markdown_json    = json.dumps(markdown_text)
    source_pdf_data  = f'data:application/pdf;base64,{source_pdf_b64}' if source_pdf_b64 else 'null'
    layout_pdf_data  = f'data:application/pdf;base64,{layout_pdf_b64}' if layout_pdf_b64 else 'null'

    html_path = Path(__file__).parent / "ext" / "index.html"
    if not html_path.exists():
        return "<p style='color:red'>ext/index.html not found. Start the server to load the viewer.</p>"

    base_html = html_path.read_text(encoding="utf-8")
    bootstrap = f"""
<script>
(async function streamlitBoot() {{
  await new Promise(r => setTimeout(r, 200));
  const ENDPOINT      = {json.dumps(endpoint)};
  const MARKDOWN      = {markdown_json};
  const CONTENT_LIST  = {content_list_json};
  const IMAGE_MAP_B64 = {image_map_json};
  const SOURCE_PDF    = {json.dumps(source_pdf_data)};
  const LAYOUT_PDF    = {json.dumps(layout_pdf_data)};

  const urlEl = document.getElementById('endpoint-input');
  if (urlEl && ENDPOINT) urlEl.value = ENDPOINT;

  if (!MARKDOWN && !CONTENT_LIST) {{
    console.log('[Boot] No data — waiting for upload.');
    return;
  }}
  if (IMAGE_MAP_B64 && typeof IMAGE_MAP_B64 === 'object')
    Object.assign(S.imageMap, IMAGE_MAP_B64);

  if (MARKDOWN) {{ renderMarkdown(MARKDOWN); S.markdown = MARKDOWN; }}

  if (CONTENT_LIST) {{
    try {{
      const {{ flat, pageBlocks, pageDims, globalMaxX, globalMaxY }} = normalizeContentList(CONTENT_LIST);
      S.contentList = flat; S.pageBlocks = pageBlocks;
      S.pageDims = pageDims; S.globalMaxX = globalMaxX; S.globalMaxY = globalMaxY;
      renderJSONExplorer();
      if (flat.length > 0) document.getElementById('legend-strip')?.classList.add('visible');
      console.log('[Boot] Loaded', flat.length, 'blocks');
    }} catch(e) {{ console.error('[Boot] Content list error', e); }}
  }}

  const pdfUrl = SOURCE_PDF !== 'null' ? SOURCE_PDF : (LAYOUT_PDF !== 'null' ? LAYOUT_PDF : null);
  const layUrl = LAYOUT_PDF !== 'null' ? LAYOUT_PDF : null;
  if (pdfUrl) {{
    S.pdfUrlOriginal = SOURCE_PDF !== 'null' ? SOURCE_PDF : null;
    S.pdfUrlLayout   = layUrl;
    try {{ await loadPDF(pdfUrl); }} catch(e) {{ console.error('[Boot] PDF error', e); }}
  }}
  updateStats();
  setStatus('ready', 'Data loaded');
  if (layUrl) {{ const b = document.getElementById('btn-toggle-pdf'); if (b) b.style.opacity='1'; }}
  console.log('[Boot] ✅ Done');
}})();
</script>
"""
    if "</body>" in base_html:
        base_html = base_html.replace("</body>", bootstrap + "\n</body>")
    else:
        base_html += bootstrap
    return base_html


# ─────────────────────────────────────────────────────────────────────────────
#  Enrichment Tab
# ─────────────────────────────────────────────────────────────────────────────
def render_enrichment_tab() -> None:
    """Display final merged markdown (content + all enrichments inline)."""
    blocks = st.session_state.blocks
    merged_md = st.session_state.final_merged_md
    enrich_md = st.session_state.enrichment_md
    status = st.session_state.status

    tab0, tab1, tab2 = st.tabs(["⏱ Summary & Cost", "📋 Final Document (with AI Enrichments)", "📊 Table Details"])

    with tab0:
        # Timing & Cost Summary
        t = status.get('timings', {})
        v = status.get('visual_stats', {})

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("⏱️ Timing Breakdown")
            st.metric("Submit & MinerU", f"{(t.get('submit_ms', 0) + t.get('mineru_ms', 0))/1000:.1f}s", help="Queue wait + GPU extraction")
            st.metric("Download", f"{t.get('download_ms', 0)/1000:.1f}s")
            st.metric("Kimi Enrichment", f"{t.get('enrich_ms', 0)/1000:.1f}s", help="LLM table + visual analysis")
            st.metric("Total Time", f"{t.get('total_ms', 0)/1000:.1f}s", delta=f"{t.get('total_ms', 0)/1000/60:.2f}m")

        with col2:
            st.subheader("💰 Cost Breakdown")
            cost_in = v.get('input_tokens', 0) * 0.30 / 1e6
            cost_out = v.get('output_tokens', 0) * 1.50 / 1e6
            cost_llm = cost_in + cost_out
            cost_mineru = 0.05
            cost_total = cost_mineru + cost_llm
            page_count = max(status.get('mineru_progress', {}).get('pages', 1), 1)

            st.metric("MinerU", f"${cost_mineru:.4f}")
            st.metric("Kimi LLM", f"${cost_llm:.4f}", help=f"{v.get('input_tokens', 0):,} in + {v.get('output_tokens', 0):,} out")
            st.metric("Total Cost", f"${cost_total:.4f}")
            st.metric("Per Page", f"${cost_total / page_count:.4f}")

        st.divider()

        # Cost comparison
        with st.expander("📊 Cost Comparison: Azure vs Alternatives", expanded=True):
            st.markdown("**Document: {0} pages | Input: {1:,} tokens | Output: {2:,} tokens**".format(
                page_count, v.get('input_tokens', 0), v.get('output_tokens', 0)
            ))

            comp_cols = st.columns(2)

            # Alternative: LlamaParse (text only)
            llamaparse_cost_page = 0.0125
            llamaparse_total = llamaparse_cost_page * page_count
            with comp_cols[0]:
                st.markdown("### Alternative: LlamaParse")
                st.markdown(f"""
**Cost:**
- Per page: **${llamaparse_cost_page:.4f}**
- Total: **${llamaparse_total:.4f}**

**Capabilities:**
- ✓ Text extraction
- ✗ No tables/visuals
- ✗ No enrichment
""")

            # Current: Azure GPT-4o-mini
            with comp_cols[1]:
                st.markdown("### Current: Azure GPT-4o-mini ⭐")
                st.markdown(f"""
**Cost:**
- MinerU: $0.05
- Azure LLM: **${cost_llm:.4f}**
- **Total: ${cost_total:.4f}**
- Per page: **${cost_total/page_count:.4f}**

**Capabilities:**
- ✓ Tables + visuals
- ✓ Full enrichment
- ✓ Enterprise LLM
""")

            savings = llamaparse_total - cost_total
            direction = "more" if savings < 0 else "less"
            st.info(f"💡 Azure costs **${abs(savings):.4f}** {direction} but adds full table + visual enrichment.")

        st.divider()

        st.subheader("📊 Document Stats")
        doc_cols = st.columns(4)
        with doc_cols[0]:
            st.metric("Pages", status.get('mineru_progress', {}).get('pages', 'N/A'))
        with doc_cols[1]:
            st.metric("Tables", status.get('tables_enriched', 0))
        with doc_cols[2]:
            st.metric("Visuals", f"{v.get('successful', 0)}/{v.get('llm_sent', 0)}")
        with doc_cols[3]:
            st.metric("LLM Calls", v.get('llm_calls', 0))

    with tab1:
        if merged_md:
            st.markdown(merged_md)
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "⬇ Download final_merged.md",
                    merged_md,
                    "final_merged.md",
                    "text/markdown",
                )
            with col2:
                if enrich_md:
                    st.download_button(
                        "⬇ Download enrichment_summary.md",
                        enrich_md,
                        "enrichment_summary.md",
                        "text/markdown",
                    )
        else:
            st.info("Final merged document not found. Process a document first.")

    with tab2:
        table_blocks = [
            (idx, b) for idx, b in enumerate(blocks)
            if str(b.get("type") or "").lower() == "table"
        ]
        if not table_blocks:
            st.info("No table blocks found.")
        else:
            for idx, blk in table_blocks:
                e     = blk.get("llm_enrichment", {})
                page  = (blk.get("page_idx") or 0) + 1
                valid = e.get("valid", None)

                with st.expander(
                    f"Block {idx} — Page {page} — "
                    f"{'✅ Valid' if valid else ('❌ Invalid' if valid is False else '⚠ Unprocessed')}",
                    expanded=valid is False,
                ):
                    # Original table text
                    orig = blk.get("original_table_text") or blk.get("content") or blk.get("text") or ""
                    if orig:
                        st.markdown("**Original Extraction:**")
                        st.code(str(orig)[:1500], language="markdown")

                    # Corrections
                    corrections = e.get("corrections", [])
                    if corrections:
                        st.markdown(
                            f'<div class="correction-box">⚠️ <b>{len(corrections)} correction(s) applied</b></div>',
                            unsafe_allow_html=True,
                        )
                        for c in corrections:
                            st.markdown(
                                f"- `{c.get('location','?')}`: "
                                f"~~`{c.get('original','?')}`~~ → **`{c.get('corrected','?')}`**"
                            )

                    # Enrichment notes
                    if e.get("enrichment_notes"):
                        st.markdown(
                            f'<div class="enrichment-box">🗒 <b>Context notes:</b> {e["enrichment_notes"]}</div>',
                            unsafe_allow_html=True,
                        )

                    # Summary
                    if e.get("summary"):
                        st.markdown(
                            f'<div class="summary-box">📋 <b>Summary:</b> {e["summary"]}</div>',
                            unsafe_allow_html=True,
                        )

                    # RAG text
                    if blk.get("rag_text"):
                        with st.expander("RAG-ready text"):
                            st.code(blk["rag_text"][:1000], language="text")

                    if e.get("needs_review"):
                        st.warning("⚠️ Flagged for manual review")

    with tab3:
        visual_summaries = st.session_state.visual_summaries
        if not visual_summaries:
            st.info("No visual summaries found in result ZIP.")
        else:
            root = Path(st.session_state.extract_dir) if st.session_state.extract_dir else None
            for item in visual_summaries:
                e    = item.get("summary") or item.get("enrichment", {})
                page = item.get("page", "?")
                idx  = item.get("block_index", "?")
                vtyp = e.get("visual_type", "?") if isinstance(e, dict) else "?"
                route = item.get("route", "?")

                with st.expander(f"Block {idx} — Page {page} — {vtyp} [{route}]"):
                    if not isinstance(e, dict):
                        st.text(str(e))
                        continue

                    # Show image if available
                    if root:
                        img_ref_val = item.get("image_path", "")
                        if img_ref_val:
                            candidates = [root / img_ref_val, root / Path(img_ref_val).name]
                            candidates += list(root.rglob(Path(img_ref_val).name))
                            for c in candidates:
                                if c.exists():
                                    st.image(str(c), width=320)
                                    break

                    col_a, col_b = st.columns(2)
                    with col_a:
                        if e.get("title"):
                            st.markdown(f"**Title:** {e['title']}")
                        if e.get("visual_type"):
                            st.markdown(f"**Type:** {e['visual_type']}")
                        if e.get("latency_ms"):
                            st.caption(f"⏱ {e['latency_ms']}ms")
                    with col_b:
                        if e.get("needs_review"):
                            st.warning("Needs review")
                        if e.get("source_quality") == "degraded":
                            st.warning("Source quality degraded")
                        if e.get("retried"):
                            st.info("Used retry call")

                    if e.get("summary"):
                        st.markdown(
                            f'<div class="summary-box">📋 {e["summary"]}</div>',
                            unsafe_allow_html=True,
                        )
                    if e.get("enrichment_notes"):
                        st.markdown(
                            f'<div class="enrichment-box">🗒 {e["enrichment_notes"]}</div>',
                            unsafe_allow_html=True,
                        )
                    if e.get("extracted_text"):
                        st.text_area("Extracted Text", e["extracted_text"][:800], height=100, disabled=True,
                                     key=f"vis_text_{idx}")
                    if e.get("data_values"):
                        st.markdown("**Data Values:**")
                        st.json(e["data_values"][:20])


# ─────────────────────────────────────────────────────────────────────────────
#  Model Lab
# ─────────────────────────────────────────────────────────────────────────────
def _type_badge(typ: str) -> str:
    _COLORS = {
        "table": "#f97316", "paragraph": "#22c55e", "text": "#22c55e",
        "title": "#8b5cf6", "image": "#ec4899", "figure": "#ec4899",
        "chart": "#ef4444", "list": "#3b82f6",
        "page_header": "#6b7280", "page_footer": "#6b7280",
        "page_number": "#6b7280", "page_aside_text": "#eab308",
        "unknown": "#94a3b8",
    }
    color = _COLORS.get(typ, "#94a3b8")
    return (
        f'<span class="block-badge" style="background:{color}22;color:{color};border-color:{color}55">'
        f"{typ}</span>"
    )


def resolve_image_file(root: Path, ref: str | None) -> Path | None:
    if not ref:
        return None
    name = Path(ref.replace("\\", "/")).name
    for c in [root / ref, root / name, *root.rglob(name)]:
        if c.exists() and c.is_file():
            return c
    return None


def render_model_lab() -> None:
    blocks   = st.session_state.blocks
    endpoint = st.session_state.endpoint.rstrip("/")
    task_id  = st.session_state.task_id

    if not blocks or not task_id:
        return

    # Load model list from server
    if not st.session_state.lab_models:
        try:
            r = requests.get(f"{endpoint}/models", timeout=10)
            st.session_state.lab_models = r.json().get("models", [])
        except Exception:
            st.session_state.lab_models = []
    all_models = st.session_state.lab_models

    with st.expander("🔬 Model Lab — Block Re-Extraction", expanded=False):
        st.caption("Pick any block, choose models, run Kimi K2.5 extraction side-by-side.")

        PROBEABLE_TYPES = {"image", "figure", "chart", "graph", "fig", "table", "diagram"}
        probeable = [
            (idx, b) for idx, b in enumerate(blocks)
            if any(k in str(b.get("type") or "").lower() for k in PROBEABLE_TYPES)
        ]
        if not probeable:
            st.info("No image, chart, or table blocks found.")
            return

        block_labels = [
            f"[{idx:04d}] p{block_page(b) or '?'} | {block_type(b)} | {block_text(b).replace(chr(10),' ')[:65]}"
            for idx, b in probeable
        ]
        selected_label = st.selectbox("Block", block_labels, key="lab_block_sel")
        sel_pos = block_labels.index(selected_label)
        real_idx, sel_block = probeable[sel_pos]
        sel_type  = block_type(sel_block)
        is_visual = any(k in sel_type for k in ("image", "figure", "chart", "graph", "fig", "diagram"))

        col_img, col_ctrl = st.columns([1.1, 0.9], gap="large")
        with col_img:
            root = Path(st.session_state.extract_dir) if st.session_state.extract_dir else None
            if root:
                img_file = resolve_image_file(root, image_ref(sel_block))
                if img_file:
                    st.image(str(img_file), caption=f"Block {real_idx} — {sel_type}", use_container_width=True)
            if sel_type == "table":
                st.text_area("Table Content", block_text(sel_block)[:2000], height=150, disabled=True)

        with col_ctrl:
            st.markdown(
                f"**Block {real_idx}** · {_type_badge(sel_type)} · {'🖼 Vision' if is_visual else '📋 Text'}",
                unsafe_allow_html=True,
            )
            if all_models:
                def _mlbl(m: dict) -> str:
                    tags = []
                    if m.get("recommended"): tags.append("⭐")
                    tags.append("👁" if m.get("vision") else "📝")
                    return f"{' '.join(tags)} {m['label']} — {m['note']}"
                model_label_map = {_mlbl(m): m["id"] for m in all_models}
                default_ids     = [
                    m["id"] for m in all_models
                    if (m.get("vision") if is_visual else not m.get("vision") or m.get("recommended"))
                ]
                default_labels  = [lbl for lbl, mid in model_label_map.items() if mid in default_ids]
                chosen_labels   = st.multiselect("Models", list(model_label_map.keys()),
                                                  default=default_labels[:2],
                                                  help="⭐=Recommended · 👁=Vision · 📝=Text")
                chosen_ids      = [model_label_map[lbl] for lbl in chosen_labels]
            else:
                st.warning("Could not load model list from server.")
                chosen_ids = []

            custom_prompt = st.text_area("Custom Prompt (optional)", height=75, placeholder="Leave blank for default extraction…")
            run_btn = st.button(
                f"▶ Run ({len(chosen_ids)} model{'s' if len(chosen_ids)!=1 else ''})",
                type="primary", disabled=not chosen_ids, key="lab_run_btn"
            )

        if run_btn and chosen_ids:
            results: dict[str, Any] = {}
            prog = st.progress(0, text="Probing models…")
            for i, mid in enumerate(chosen_ids):
                prog.progress(i / len(chosen_ids), text=f"Calling {mid.split('/')[-1]}…")
                try:
                    resp = requests.post(
                        f"{endpoint}/probe/run",
                        json={"task_id": task_id, "block_index": real_idx,
                              "model": mid, "custom_prompt": custom_prompt},
                        timeout=180,
                    )
                    results[mid] = resp.json()
                except Exception as exc:
                    results[mid] = {"ok": False, "error": str(exc)}
            prog.progress(1.0, text="Done.")
            st.session_state.lab_results = results

        lab_results = st.session_state.lab_results
        if lab_results:
            st.divider()
            cols = st.columns(max(1, len(lab_results)))
            for col, (mid, result) in zip(cols, lab_results.items()):
                minfo = next((m for m in all_models if m["id"] == mid), {})
                short = minfo.get("label") or mid.split("/")[-1]
                ok    = result.get("ok", False)
                with col:
                    st.markdown(
                        f"**{'✅' if ok else '❌'} {short}**"
                        f"{'⭐' if minfo.get('recommended') else ''}{'👁' if minfo.get('vision') else '📝'}",
                        unsafe_allow_html=True,
                    )
                    if ok:
                        c1, c2 = st.columns(2)
                        c1.metric("Latency", f"{result.get('latency_ms',0)}ms")
                        c2.metric("Tokens", f"{result.get('tokens_in',0)}↑ {result.get('tokens_out',0)}↓")
                        parsed = result.get("parsed", {})
                        if isinstance(parsed, dict):
                            if parsed.get("summary"):
                                st.markdown(
                                    f'<div class="summary-box">📋 {parsed["summary"]}</div>',
                                    unsafe_allow_html=True,
                                )
                            if parsed.get("extracted_text"):
                                st.text_area("Extracted", parsed["extracted_text"], height=130,
                                             disabled=True, key=f"lab_{mid}_txt")
                            if parsed.get("corrections"):
                                st.warning(f"⚠ {len(parsed['corrections'])} corrections")
                            if parsed.get("needs_review"):
                                st.warning("⚠️ Needs review")
                        with st.expander("Raw JSON"):
                            st.code(result.get("raw_response", ""), language="json")
                    else:
                        st.error(result.get("error") or "Unknown error")


# ─────────────────────────────────────────────────────────────────────────────
#  Main App
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    init_state()
    render_sidebar()

    # Base64 encode PDFs for injection into HTML viewer
    source_pdf_b64: str | None = None
    if st.session_state.source_pdf_bytes:
        source_pdf_b64 = base64.b64encode(st.session_state.source_pdf_bytes).decode("ascii")

    layout_pdf_b64: str | None = None
    if st.session_state.layout_pdf_bytes:
        layout_pdf_b64 = base64.b64encode(st.session_state.layout_pdf_bytes).decode("ascii")

    # Show single extraction result
    if st.session_state.get("extract_result"):
        st.divider()
        result = st.session_state.extract_result
        if result.get("status") == "success":
            st.markdown(f"## {result.get('method', 'Extraction')} Results")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Pages", result.get('pages', 'N/A'))
            with col2:
                st.metric("Cost", result.get('cost', 'N/A'))
            with col3:
                st.download_button(
                    "📥 Download Markdown",
                    result.get('markdown', ''),
                    f"{result.get('method', 'extraction')}.md",
                    "text/markdown"
                )
            with st.expander("📄 View Markdown"):
                st.markdown(result.get('markdown', ''))
        else:
            st.error(f"❌ Error: {result.get('message', 'Unknown error')}")
        st.divider()

    # Show comparison results if available
    if st.session_state.get("compare_result"):
        st.divider()
        st.markdown("## 🔄 Extraction Comparison: All 4 Methods")
        result = st.session_state.compare_result

        # Method info cards in summary row
        summary_cols = st.columns(4)
        methods_info = [
            ("🦙 LlamaParse", "llamaparse", "$0.0125/page"),
            ("📋 Azure DI", "azure_di", "Pay-per-page"),
            ("🔷 MinerU + OpenAI", "mineru_azure", "$0.05 + LLM"),
            ("⚙️ MinerU Raw", "mineru_raw", "$0.05"),
        ]

        for col, (label, key, cost) in zip(summary_cols, methods_info):
            with col:
                method_data = result.get(key, {})
                if method_data and method_data.get("status") == "success":
                    st.metric(label, f"{method_data.get('pages', 0)} pages", f"Cost: {cost}")
                else:
                    st.metric(label, "—", "❌ Failed")

        st.divider()

        # Scrollable markdown comparison (2x2 grid with full content & proper rendering)
        comp_cols = st.columns(2)

        # Column 1 (Left): LlamaParse & MinerU+Azure
        with comp_cols[0]:
            # LlamaParse
            st.subheader("🦙 LlamaParse (Text-only)")
            llama = result.get("llamaparse", {})
            if llama and llama.get("status") == "success":
                markdown_content = llama.get("markdown", "")
                with st.container(border=True):
                    st.markdown(markdown_content)
                st.caption(f"📊 {len(markdown_content)} chars extracted")
            else:
                st.warning("❌ Extraction failed or not available")

            st.divider()

            # MinerU + Azure
            st.subheader("🔷 MinerU + Azure OpenAI (Enriched)")
            azure = result.get("mineru_azure", {})
            if azure and azure.get("status") == "success":
                markdown_content = azure.get("markdown", "")
                with st.container(border=True):
                    st.markdown(markdown_content)
                st.caption(f"📊 {len(markdown_content)} chars + enrichment applied")
            else:
                st.warning("❌ Extraction failed or not available")

        # Column 2 (Right): Azure DI & MinerU Raw
        with comp_cols[1]:
            # Azure Document Intelligence
            st.subheader("📋 Azure Document Intelligence")
            di = result.get("azure_di", {})
            if di and di.get("status") == "success":
                markdown_content = di.get("markdown", "")
                with st.container(border=True):
                    st.markdown(markdown_content)
                st.caption(f"📊 {len(markdown_content)} chars extracted")
            else:
                st.warning("❌ Extraction failed or not available")

            st.divider()

            # MinerU Raw
            st.subheader("⚙️ MinerU Raw (Layout Only)")
            raw = result.get("mineru_raw", {})
            if raw and raw.get("status") == "success":
                markdown_content = raw.get("markdown", "")
                with st.container(border=True):
                    st.markdown(markdown_content)
                st.caption(f"📊 {len(markdown_content)} chars extracted")
            else:
                st.warning("❌ Extraction failed or not available")

        st.divider()
        st.info(f"📊 File: {result.get('file_name')} | Size: {result.get('file_size', 0) / 1024:.1f}KB")
        st.divider()

    if st.session_state.blocks:
        # Show viewer + enrichment tabs
        viewer_tab, enrichment_tab = st.tabs(["🗂 Document Viewer", "✨ Enrichment Report"])

        with viewer_tab:
            viewer_html = build_viewer_html(
                markdown_text    = st.session_state.markdown,
                content_list_raw = st.session_state.content_list_raw,
                image_map_b64    = st.session_state.image_map_b64,
                source_pdf_b64   = source_pdf_b64,
                layout_pdf_b64   = layout_pdf_b64,
                endpoint         = st.session_state.endpoint,
            )
            components.html(viewer_html, height=720, scrolling=False)

        with enrichment_tab:
            render_enrichment_tab()

        # Model Lab below
        st.divider()
        render_model_lab()

    else:
        # No data yet — show viewer with empty state
        viewer_html = build_viewer_html(
            markdown_text    = "",
            content_list_raw = None,
            image_map_b64    = {},
            source_pdf_b64   = None,
            layout_pdf_b64   = None,
            endpoint         = st.session_state.endpoint,
        )
        components.html(viewer_html, height=720, scrolling=False)


if __name__ == "__main__":
    main()
