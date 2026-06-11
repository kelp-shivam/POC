"""
DocExtract UI — Clean Refactored Version
=========================================
4 extraction methods with side-by-side comparison
- LlamaCloud (agentic tier)
- MinerU + Azure OpenAI (enriched)
- MinerU Raw (layout only)
- Azure Document Intelligence (optional)
"""
from __future__ import annotations

import os
import base64
import requests
import streamlit as st

st.set_page_config(
    page_title="DocExtract — 4 Extraction Methods",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────
_IS_RENDER = bool(os.getenv("RENDER"))
_EXTERNAL_BACKEND = bool(os.getenv("DOCEXTRACT_BRIDGE_URL"))

if os.getenv("DOCEXTRACT_BRIDGE_URL"):
    DEFAULT_ENDPOINT = os.getenv("DOCEXTRACT_BRIDGE_URL")
elif _IS_RENDER:
    DEFAULT_ENDPOINT = "https://docextract-backend.onrender.com"
else:
    DEFAULT_ENDPOINT = "http://127.0.0.1:8000"

# ─────────────────────────────────────────────────────────────────────────────
#  Theme & Styling
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background: #080b14; }
    [data-testid="stSidebar"] { background: rgba(10,14,26,0.98); border-right: 1px solid rgba(99,102,241,0.2); }
    [data-testid="stSidebar"] * { color: #e4eaf5; }

    .stButton > button {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
        color: white; border: none; border-radius: 8px; font-weight: 600; width: 100%;
    }
    .stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(99,102,241,0.5); }

    .section-title {
        font-size: 12px; font-weight: 700; color: #a5b4fc;
        text-transform: uppercase; letter-spacing: 0.07em;
        margin: 16px 0 8px;
    }

    .method-card {
        background: rgba(15,23,42,0.8); border: 1px solid rgba(99,102,241,0.2);
        border-radius: 8px; padding: 12px; margin: 6px 0;
    }

    .result-container {
        background: rgba(15,23,42,0.8); border: 1px solid rgba(99,102,241,0.2);
        border-radius: 8px; padding: 16px; margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Session State
# ─────────────────────────────────────────────────────────────────────────────
if "endpoint" not in st.session_state:
    st.session_state.endpoint = DEFAULT_ENDPOINT
if "compare_result" not in st.session_state:
    st.session_state.compare_result = None
if "extract_result" not in st.session_state:
    st.session_state.extract_result = None
if "task_id" not in st.session_state:
    st.session_state.task_id = ""

# ─────────────────────────────────────────────────────────────────────────────
#  Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ DocExtract")

    # Backend status
    st.markdown("<div class='section-title'>🔗 Backend</div>", unsafe_allow_html=True)
    cols = st.columns([1, 1])
    with cols[0]:
        st.session_state.endpoint = st.text_input(
            "Endpoint",
            st.session_state.endpoint,
            label_visibility="collapsed"
        )
    with cols[1]:
        try:
            r = requests.get(f"{st.session_state.endpoint.rstrip('/')}/health", timeout=3)
            st.success("✓ Online" if r.status_code == 200 else "⚠ Error")
        except:
            st.error("✗ Offline")

    st.divider()

    # Extraction section
    st.markdown("<div class='section-title'>📤 Extract PDF</div>", unsafe_allow_html=True)
    pdf_file = st.file_uploader("Select PDF", type=["pdf"], label_visibility="collapsed")

    if pdf_file:
        method = st.radio(
            "Method:",
            ["All 4 (Compare)", "LlamaParse", "MinerU + Azure", "MinerU Raw", "Azure DI"],
            index=0
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚀 Extract", type="primary"):
                endpoint_map = {
                    "All 4 (Compare)": "/compare",
                    "LlamaParse": "/extract/llamaparse",
                    "MinerU + Azure": "/extract/mineru-azure",
                    "MinerU Raw": "/extract/mineru-raw",
                    "Azure DI": "/extract/azure-di",
                }
                endpoint = endpoint_map[method]

                with st.spinner(f"Extracting..."):
                    try:
                        resp = requests.post(
                            f"{st.session_state.endpoint.rstrip('/')}{endpoint}",
                            files={"file": pdf_file.getvalue()},
                            timeout=300,
                        )
                        if resp.status_code == 200:
                            if method == "All 4 (Compare)":
                                st.session_state.compare_result = resp.json()
                                st.session_state.extract_result = None
                            else:
                                st.session_state.extract_result = resp.json()
                                st.session_state.compare_result = None
                            st.success("✅ Done")
                        else:
                            st.error(f"HTTP {resp.status_code}")
                    except Exception as e:
                        st.error(f"Error: {str(e)[:100]}")

        with col2:
            if st.button("🗑️ Clear"):
                st.session_state.compare_result = None
                st.session_state.extract_result = None
                st.session_state.task_id = ""
                st.rerun()

    st.divider()

    # Poll section
    st.markdown("<div class='section-title'>🔄 Poll Status</div>", unsafe_allow_html=True)
    task_input = st.text_input("Task ID", st.session_state.task_id, label_visibility="collapsed")
    if task_input != st.session_state.task_id:
        st.session_state.task_id = task_input

    if st.session_state.task_id:
        if st.button("📋 Get Status"):
            try:
                r = requests.get(
                    f"{st.session_state.endpoint.rstrip('/')}/tasks/{st.session_state.task_id}",
                    timeout=30
                )
                status = r.json()
                if status.get("status") == "completed":
                    st.success("✅ Completed")
                    if st.button("📥 Get Result"):
                        r2 = requests.get(
                            f"{st.session_state.endpoint.rstrip('/')}/tasks/{st.session_state.task_id}/result",
                            timeout=30
                        )
                        st.session_state.compare_result = r2.json()
                        st.rerun()
                else:
                    st.info(f"Status: {status.get('status', 'unknown')}")
            except Exception as e:
                st.error(f"Error: {str(e)[:100]}")

# ─────────────────────────────────────────────────────────────────────────────
#  Main Content
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state.compare_result:
    st.markdown("## 🔄 Comparison: All 4 Methods")
    result = st.session_state.compare_result

    # Summary row
    cols = st.columns(4)
    methods = [
        ("🦙 LlamaParse", "llamaparse", "$0.0125/page"),
        ("📋 Azure DI", "azure_di", "Pay-per-page"),
        ("🔷 MinerU+Azure", "mineru_azure", "$0.05 + LLM"),
        ("⚙️ MinerU Raw", "mineru_raw", "$0.05"),
    ]
    for col, (label, key, cost) in zip(cols, methods):
        with col:
            data = result.get(key, {})
            if data and data.get("status") == "success":
                st.metric(label, f"{data.get('pages', 0)} pages", f"Cost: {cost}")
            else:
                st.metric(label, "—", "❌")

    st.divider()

    # Results grid (2x2)
    cols = st.columns(2)

    # Row 1
    with cols[0]:
        st.subheader("🦙 LlamaParse")
        data = result.get("llamaparse", {})
        if data and data.get("status") == "success":
            with st.container(border=True):
                st.markdown(data.get("markdown", ""))
            st.caption(f"📊 {len(data.get('markdown', ''))} chars")
        else:
            st.warning("❌ Failed")

    with cols[1]:
        st.subheader("📋 Azure DI")
        data = result.get("azure_di", {})
        if data and data.get("status") == "success":
            with st.container(border=True):
                st.markdown(data.get("markdown", ""))
            st.caption(f"📊 {len(data.get('markdown', ''))} chars")
        else:
            st.warning("❌ Failed")

    # Row 2
    cols = st.columns(2)
    with cols[0]:
        st.subheader("🔷 MinerU + Azure")
        data = result.get("mineru_azure", {})
        if data and data.get("status") == "success":
            with st.container(border=True):
                st.markdown(data.get("markdown", ""))
            st.caption(f"📊 {len(data.get('markdown', ''))} chars + enrichment")
        else:
            st.warning("❌ Failed")

    with cols[1]:
        st.subheader("⚙️ MinerU Raw")
        data = result.get("mineru_raw", {})
        if data and data.get("status") == "success":
            with st.container(border=True):
                st.markdown(data.get("markdown", ""))
            st.caption(f"📊 {len(data.get('markdown', ''))} chars")
        else:
            st.warning("❌ Failed")

    st.divider()
    st.info(f"📄 {result.get('file_name')} • {result.get('file_size', 0) / 1024:.1f}KB")

elif st.session_state.extract_result:
    st.markdown(f"## {st.session_state.extract_result.get('method', 'Extraction Result')}")

    cols = st.columns(3)
    with cols[0]:
        st.metric("Pages", st.session_state.extract_result.get("pages", 0))
    with cols[1]:
        st.metric("Cost", st.session_state.extract_result.get("cost", "—"))
    with cols[2]:
        chars = len(st.session_state.extract_result.get("markdown", ""))
        st.metric("Characters", f"{chars:,}")

    st.divider()
    st.markdown("### 📄 Markdown Content")
    with st.container(border=True):
        st.markdown(st.session_state.extract_result.get("markdown", ""))

else:
    st.info("👈 Upload a PDF and select extraction method to begin")
