"""
DocExtract — MinerU + Azure GPT-4o-mini Pipeline
==================================================
Complete pipeline:
  1. Submit PDF to MinerU cloud (OCR + layout extraction)
  2. Download result ZIP (content_list, full.md, images)
  3. Post-process with Azure OpenAI Foundry (GPT-4o-mini):
       - Table validation + correction + enrichment + summary
       - Image/chart extraction: type, title, data values, summary, enrichment notes
       - Tables with embedded images: send BOTH text + vision
       - Garbage detection before every LLM call
       - Perceptual hash cache (skip duplicate images)
       - Importance scoring (skip trivial visuals)
       - Surrounding context + footnotes sent with every visual
  4. Heuristic quality checks
  5. Produce enriched ZIP + final_merged.md

Requires env vars:
  MINERU_API_KEY / miner_api_key
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_DEPLOYMENT (default: gpt-4o-mini)
  AZURE_OPENAI_API_VERSION (default: 2024-02-15)
"""

from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import asyncio
import base64
import hashlib
import itertools
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
try:
    import openai as _openai_sdk
except ImportError:
    _openai_sdk = None  # type: ignore
from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from heuristics import checks_markdown, run_heuristic_checks

# ── Optional: PIL + imagehash for perceptual dedup ──────────────────────────
try:
    from PIL import Image
    import imagehash
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ── Optional: Tesseract OCR fallback ────────────────────────────────────────
try:
    import pytesseract
    _TESSERACT = True
except ImportError:
    _TESSERACT = False


# ─────────────────────────────────────────────────────────────────────────────
#  Bootstrap
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / ".mineru_tasks"
TASKS_DIR.mkdir(exist_ok=True)

MINERU_BASE_URL = "https://mineru.net"
TASKS: dict[str, dict[str, Any]] = {}


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


# ─────────────────────────────────────────────────────────────────────────────
#  Constants & Thresholds
# ─────────────────────────────────────────────────────────────────────────────
_PAGE_AREA_PT2        = 501_290.0   # A4 in pts²
_MIN_AREA_RATIO       = 0.02        # blocks < 2% page area → SMALL_VISUAL (OCR only)
_REPEAT_PAGE_THRESH   = 3           # same image on 3+ pages → DECORATIVE
_LLM_CONCURRENCY      = int(os.getenv("LLM_CONCURRENCY", "2"))
_IMPORTANCE_THRESHOLD = float(os.getenv("IMPORTANCE_THRESHOLD", "0.25"))
_CONTEXT_WINDOW       = 3           # blocks before/after for context

# Azure OpenAI Foundry GPT-4o-mini pricing (per 1M tokens)
_COST_PER_1M_IN     = 0.15   # $0.15 / 1M input tokens
_COST_PER_1M_OUT    = 0.60   # $0.60 / 1M output tokens

_DATA_WORDS = re.compile(
    r"\b(figure|fig|chart|graph|plot|trend|distribution|comparison|"
    r"revenue|cost|sales|growth|percent|ratio|index|score|"
    r"quarter|monthly|annual|year|budget|forecast|performance|"
    r"market|profit|loss|margin|return|table|exhibit)\b",
    re.I,
)

# ─── Garbage detection patterns ─────────────────────────────────────────────
_GARBAGE_REPEAT  = re.compile(r"(\b\w+\b)(?:\s+\1){3,}", re.I)   # word repeated 4+ times
_CAPTION_ONLY    = re.compile(r"^(fig(?:ure)?|table|chart|exhibit)\.?\s*\d*\.?\s*$", re.I)
_WHITESPACE_ONLY = re.compile(r"^\s*$")


# ─────────────────────────────────────────────────────────────────────────────
#  LLM Provider: Azure OpenAI Foundry (GPT-4o-mini only)
# ─────────────────────────────────────────────────────────────────────────────
_AZURE_API_KEY      = os.getenv("AZURE_OPENAI_API_KEY", "")
_AZURE_ENDPOINT     = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_DEPLOYMENT   = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
_AZURE_API_VERSION  = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15")
_AZURE_MODEL        = "gpt-4o-mini"

if not (_AZURE_API_KEY and _AZURE_ENDPOINT):
    raise RuntimeError("Missing Azure credentials: AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT required")

# ─────────────────────────────────────────────────────────────────────────────
#  LLM Callers (Azure OpenAI Foundry only)
# ─────────────────────────────────────────────────────────────────────────────
_LLM_SYSTEM = (
    "You are a precise document data extraction engine. "
    "Output ONLY raw valid JSON — no markdown fences, no commentary, no preamble. "
    "Every response is a single valid JSON object and nothing else."
)


def _get_llm_client() -> tuple[Any, str]:
    """Return Azure OpenAI Foundry client (GPT-4o-mini only)."""
    if _openai_sdk is None:
        raise RuntimeError("openai SDK not installed")
    client = _openai_sdk.AzureOpenAI(
        api_key=_AZURE_API_KEY,
        azure_endpoint=_AZURE_ENDPOINT,
        api_version=_AZURE_API_VERSION,
    )
    return client, _AZURE_DEPLOYMENT


def _llm_text(prompt: str, max_retries: int = 3, _ledger: dict | None = None) -> str | None:
    """Text-only LLM call. Provider selected by LLM_PROVIDER env var."""
    if _openai_sdk is None:
        return None

    for attempt in range(max_retries):
        try:
            client, model = _get_llm_client()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _LLM_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=2048,
                temperature=0.0,
                timeout=90,
            )
            if _ledger is not None and resp.usage:
                _charge_ledger(_ledger,
                    in_tok=resp.usage.prompt_tokens or 0,
                    out_tok=resp.usage.completion_tokens or 0,
                )
            content = resp.choices[0].message.content
            if content:
                return content.strip()
            if attempt < max_retries - 1:
                time.sleep(1.0)
        except Exception as exc:
            err = str(exc)
            if "429" in err or "rate" in err.lower():
                time.sleep(2 ** attempt + 1)
            elif attempt < max_retries - 1:
                time.sleep(1.5 ** attempt)
    return None


def _llm_vision(image_path: Path, prompt: str, max_retries: int = 3, _ledger: dict | None = None) -> str | None:
    """Vision LLM call. Provider selected by LLM_PROVIDER env var."""
    if _openai_sdk is None:
        return None
    mime      = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded   = base64.b64encode(image_path.read_bytes()).decode("ascii")
    image_url = f"data:{mime};base64,{encoded}"

    for attempt in range(max_retries):
        try:
            client, model = _get_llm_client()
            resp = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "system", "content": _LLM_SYSTEM,
                }, {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                max_tokens=2048,
                temperature=0.0,
                timeout=120,
            )
            if _ledger is not None and resp.usage:
                _charge_ledger(_ledger,
                    in_tok=resp.usage.prompt_tokens or 0,
                    out_tok=resp.usage.completion_tokens or 0,
                )
            content = resp.choices[0].message.content
            return content.strip() if content else None
        except Exception as exc:
            err = str(exc)
            if "429" in err or "rate" in err.lower():
                time.sleep(2 ** attempt + 1)
            elif attempt < max_retries - 1:
                time.sleep(1.5 ** attempt)
    return None


# Aliases — existing call-sites unchanged


def _parse_llm_json(raw: str | None) -> dict[str, Any]:
    """Strip markdown fences and parse JSON; fallback to raw_text on failure."""
    if not raw:
        return {"ok": False, "needs_review": True, "error": "empty response"}
    clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw.strip(), flags=re.I | re.M).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"raw_text": raw, "needs_review": True, "ok": False}


# ─────────────────────────────────────────────────────────────────────────────
#  Garbage Detection
# ─────────────────────────────────────────────────────────────────────────────
def _is_garbage(text: str) -> bool:
    """Return True if the text is too degraded to be useful as LLM input."""
    if not text or _WHITESPACE_ONLY.match(text):
        return True
    stripped = text.strip()
    if len(stripped) < 4:
        return True
    if _CAPTION_ONLY.match(stripped):
        return True
    if _GARBAGE_REPEAT.search(stripped):
        return True
    # Single-char repeat (e.g. "........." or "--------")
    unique_chars = set(stripped.replace(" ", ""))
    if len(unique_chars) <= 2 and len(stripped) > 10:
        return True
    return False


def _clean_text_for_llm(text: str, label: str = "text") -> tuple[str, bool]:
    """
    Return (cleaned_text, was_degraded).
    Strips known-garbage segments; flags degradation.
    """
    if _is_garbage(text):
        return "", True
    # Remove excessive whitespace / null bytes
    cleaned = re.sub(r"\x00", "", text)
    cleaned = re.sub(r"\r\n|\r", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{3,}", "  ", cleaned)
    return cleaned.strip(), False


def _check_table_columns_garbage(markdown_table: str) -> bool:
    """Return True if a table's data columns are all identical values (garbage)."""
    lines = [
        l.strip() for l in markdown_table.splitlines()
        if l.strip() and "|" in l and not re.match(r"^\|[-:| ]+\|$", l.strip())
    ]
    if len(lines) < 2:
        return False
    data_lines = lines[1:]  # skip header
    for col_idx in range(10):  # check up to 10 columns
        vals = []
        for line in data_lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if col_idx < len(cells):
                vals.append(cells[col_idx])
        if len(vals) >= 3 and len(set(vals)) == 1 and vals[0] not in ("", "—", "-", "N/A"):
            return True  # entire column is identical — suspicious
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Utility: token/cost ledger
# ─────────────────────────────────────────────────────────────────────────────
def _make_ledger() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "llm_calls": 0,
        "cache_hits": 0,
    }


def _charge_ledger(ledger: dict, in_tok: int, out_tok: int, cached_tok: int = 0) -> None:
    """Accumulate token counts and USD cost using Kimi K2.5 HPC-AI pricing."""
    ledger["input_tokens"]  += in_tok
    ledger["output_tokens"] += out_tok
    ledger["cached_tokens"] = ledger.get("cached_tokens", 0) + cached_tok
    billable_in = max(0, in_tok - cached_tok)
    ledger["cost_usd"] += (
        billable_in * _COST_PER_1M_IN / 1_000_000
        + cached_tok * _COST_PER_1M_CACHED / 1_000_000
        + out_tok   * _COST_PER_1M_OUT    / 1_000_000
    )
    ledger["llm_calls"] += 1


# ─────────────────────────────────────────────────────────────────────────────
#  Perceptual Hash Cache
# ─────────────────────────────────────────────────────────────────────────────
_IMAGE_HASH_CACHE: dict[str, dict] = {}
_HASH_LOCK = threading.Lock()


def _perceptual_hash(image_path: Path) -> str:
    if _PIL_AVAILABLE:
        try:
            img = Image.open(image_path)
            return str(imagehash.phash(img))
        except Exception:
            pass
    return hashlib.md5(image_path.read_bytes()).hexdigest()


def _cache_get(image_path: Path, role: str) -> dict | None:
    key = f"{_perceptual_hash(image_path)}_{role}"
    with _HASH_LOCK:
        return _IMAGE_HASH_CACHE.get(key)


def _cache_set(image_path: Path, role: str, result: dict) -> None:
    key = f"{_perceptual_hash(image_path)}_{role}"
    with _HASH_LOCK:
        _IMAGE_HASH_CACHE[key] = result
        if len(_IMAGE_HASH_CACHE) > 5000:
            # Prune oldest 10% (simple FIFO approximation)
            keys = list(_IMAGE_HASH_CACHE.keys())
            for k in keys[:500]:
                _IMAGE_HASH_CACHE.pop(k, None)


# ─────────────────────────────────────────────────────────────────────────────
#  MinerU helpers  (unchanged from base)
# ─────────────────────────────────────────────────────────────────────────────
def now_ms() -> int:
    return int(time.time() * 1000)


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return cleaned or "document.pdf"


def mineru_token() -> str:
    token = (
        os.getenv("MINERU_API_KEY")
        or os.getenv("MINERU_TOKEN")
        or os.getenv("MINER_U_API_KEY")
        or os.getenv("MINER_U_TOKEN")
        or os.getenv("miner_api_key")
    )
    if not token:
        raise RuntimeError("Missing MinerU token. Set MINERU_API_KEY in .env.")
    return token.removeprefix("Bearer ").strip()


def mineru_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mineru_token()}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


def checked_mineru_response(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"MinerU non-JSON HTTP {resp.status_code}: {resp.text[:500]}") from exc
    if resp.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"MinerU error HTTP {resp.status_code}: {data}")
    return data


def submit_local_file_to_mineru(
    file_path: Path,
    *,
    model_version: str,
    enable_formula: bool,
    enable_table: bool,
    language: str,
    is_ocr: bool,
    page_ranges: str | None,
) -> str:
    payload: dict[str, Any] = {
        "files": [{"name": file_path.name, "data_id": f"docextract_{uuid.uuid4().hex[:12]}"}],
        "model_version": model_version,
        "enable_formula": enable_formula,
        "enable_table": enable_table,
        "language": language,
    }
    if is_ocr:
        payload["files"][0]["is_ocr"] = True
    if page_ranges:
        payload["files"][0]["page_ranges"] = page_ranges

    resp = requests.post(
        f"{MINERU_BASE_URL}/api/v4/file-urls/batch",
        headers=mineru_headers(),
        json=payload,
        timeout=30,
    )
    data = checked_mineru_response(resp)
    batch_id  = data["data"]["batch_id"]
    upload_url = data["data"]["file_urls"][0]

    with file_path.open("rb") as handle:
        put_resp = requests.put(upload_url, data=handle, timeout=900)
    if put_resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"MinerU upload failed HTTP {put_resp.status_code}: {put_resp.text[:500]}")
    return batch_id


def poll_mineru_batch(batch_id: str, task: dict[str, Any]) -> str:
    deadline = time.time() + int(os.getenv("MINERU_TIMEOUT_SECONDS", "3600"))
    while time.time() < deadline:
        resp = requests.get(
            f"{MINERU_BASE_URL}/api/v4/extract-results/batch/{batch_id}",
            headers=mineru_headers(),
            timeout=30,
        )
        data = checked_mineru_response(resp)
        results = data.get("data", {}).get("extract_result") or []
        result  = results[0] if results else {}
        state   = str(result.get("state") or "pending").lower()
        progress = result.get("extract_progress") or {}
        task.update({"status": state, "mineru_state": state, "mineru_progress": progress, "updated_at": now_ms()})
        if state == "done":
            zip_url = result.get("full_zip_url")
            if not zip_url:
                raise RuntimeError(f"MinerU done but no full_zip_url: {result}")
            return zip_url
        if state == "failed":
            raise RuntimeError(result.get("err_msg") or f"MinerU failed: {result}")
        time.sleep(int(os.getenv("MINERU_POLL_SECONDS", "5")))
    raise TimeoutError(f"MinerU batch {batch_id} timed out.")


def download_zip(zip_url: str, dest: Path) -> None:
    with requests.get(zip_url, stream=True, timeout=(30, 900)) as resp:
        resp.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


# ─────────────────────────────────────────────────────────────────────────────
#  Block / Content-List Utilities
# ─────────────────────────────────────────────────────────────────────────────
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _extract_v2_text(block: dict[str, Any]) -> str:
    c = block.get("content")
    if not isinstance(c, dict):
        return block.get("text", "") or block.get("table_body", "")
    for key in ("paragraph_content", "title_content", "page_header_content",
                "page_footer_content", "page_number_content", "page_aside_text_content"):
        spans = c.get(key)
        if isinstance(spans, list):
            return " ".join(
                s.get("content", "") if isinstance(s, dict) else str(s) for s in spans
            ).strip()
    if isinstance(c.get("list_items"), list):
        return "\n".join(
            " ".join(
                sp.get("content", "") if isinstance(sp, dict) else str(sp)
                for sp in (item.get("item_content") or [])
            )
            for item in c["list_items"]
        ).strip()
    if c.get("table_body"):
        return c["table_body"]
    if c.get("html"):
        return c["html"]
    return block.get("text", "")


def extract_block_text(block: dict[str, Any]) -> str:
    bits: list[str] = []
    for key in ("text", "content", "ocr_text", "caption", "html", "table_body"):
        value = block.get(key)
        if isinstance(value, str):
            bits.append(value)
    content = block.get("content")
    if isinstance(content, dict):
        for value in content.values():
            if isinstance(value, str):
                bits.append(value)
            elif isinstance(value, list):
                bits.append(" ".join(
                    str(x.get("content", x)) for x in value if isinstance(x, (str, dict))
                ))
    return "\n".join(x.strip() for x in bits if x and x.strip())[:6000]


def flatten_content_list(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        for page_idx, page in enumerate(raw):
            for order, block in enumerate(page):
                if isinstance(block, dict):
                    item = dict(block)
                    item.setdefault("page_idx", page_idx)
                    item.setdefault("order", order)
                    if not item.get("text"):
                        item["text"] = _extract_v2_text(item)
                    out.append(item)
    elif isinstance(raw, list):
        out = [dict(x) for x in raw if isinstance(x, dict)]
    return out


def image_path_from_block(block: dict[str, Any]) -> str | None:
    for key in ("img_path", "image_path", "crop_path"):
        if isinstance(block.get(key), str):
            return block[key]
    content = block.get("content")
    if isinstance(content, dict):
        src = content.get("image_source")
        if isinstance(src, dict) and isinstance(src.get("path"), str):
            return src["path"]
    return None


def resolve_zip_image(extract_dir: Path, image_ref: str | None) -> Path | None:
    if not image_ref:
        return None
    ref = image_ref.replace("\\", "/").lstrip("/")
    candidates = [extract_dir / ref, extract_dir / Path(ref).name]
    candidates.extend(extract_dir.rglob(Path(ref).name))
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _bbox_area(bbox: list) -> float:
    if not isinstance(bbox, list) or len(bbox) < 4:
        return 0.0
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


# ─────────────────────────────────────────────────────────────────────────────
#  Context Window Builder
# ─────────────────────────────────────────────────────────────────────────────
def _get_context_window(
    blocks: list[dict[str, Any]],
    idx: int,
    window: int = _CONTEXT_WINDOW,
) -> tuple[str, str]:
    """
    Return (surrounding_context, footnote_context).
    surrounding = window blocks before + after on the same page.
    footnotes   = all page_footer / page_aside_text blocks on the same page.
    """
    page = blocks[idx].get("page_idx")
    surrounding: list[str] = []
    footnotes: list[str] = []

    for j, block in enumerate(blocks):
        if j == idx:
            continue
        blk_page = block.get("page_idx")
        if blk_page != page:
            continue
        typ  = str(block.get("type") or "").lower()
        text = extract_block_text(block)
        if not text or _is_garbage(text):
            continue

        if typ in ("page_footer", "page_aside_text"):
            footnotes.append(text.strip())
        elif abs(j - idx) <= window:
            surrounding.append(text.strip())

    ctx   = "\n".join(surrounding)[:3000]
    notes = "\n".join(footnotes)[:1500]
    return ctx, notes


# ─────────────────────────────────────────────────────────────────────────────
#  Importance Scoring
# ─────────────────────────────────────────────────────────────────────────────
def block_importance_score(block: dict[str, Any]) -> float:
    """Score 0–1.  LLM skipped if score < _IMPORTANCE_THRESHOLD."""
    typ = str(block.get("type") or "").lower()
    if typ in ("table", "chart", "graph", "figure", "diagram"):
        return 0.9
    text = extract_block_text(block)
    if not text:
        return 0.0
    score = 0.0
    if re.search(r"\d+(?:[.,]\d+)?", text):
        score += 0.4
    if _DATA_WORDS.search(text):
        score += 0.3
    if len(text) > 200:
        score += 0.2
    return min(score, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
#  Visual Block Classification
# ─────────────────────────────────────────────────────────────────────────────
def classify_visual_block(block: dict[str, Any], page_image_counts: dict[str, int]) -> str:
    """Returns: DECORATIVE | SMALL_VISUAL | CHART | TABLE_IMAGE | UNKNOWN"""
    bbox  = block.get("bbox") or block.get("bounding_box") or []
    area  = _bbox_area(bbox)
    ref   = image_path_from_block(block)
    typ   = str(block.get("type") or block.get("category") or "").lower()

    # Repeating across 3+ pages → decorative (logo/watermark)
    if ref and page_image_counts.get(ref, 0) >= _REPEAT_PAGE_THRESH:
        return "DECORATIVE"

    # Too small → OCR already done by MinerU, skip vision LLM
    if 0 < area < _PAGE_AREA_PT2 * _MIN_AREA_RATIO:
        return "SMALL_VISUAL"

    if typ in ("chart", "graph", "plot", "diagram"):
        return "CHART"

    # Large figures next to table keywords → may be a table screenshot
    if typ in ("image", "figure", "fig") and area > 80_000:
        return "TABLE_IMAGE"

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
#  TABLE Enrichment  (validate + correct + enrich + summarize — one Kimi call)
# ─────────────────────────────────────────────────────────────────────────────
_TABLE_JSON_SCHEMA = """\
{
  "valid": <true|false>,
  "corrections": [{"location": "<row N col M>", "original": "<bad value>", "corrected": "<fixed value>"}],
  "corrected_table": "<complete corrected markdown table or empty if no corrections needed>",
  "enrichment_notes": "<extra info from footnotes/context not in table itself, or empty string>",
  "summary": "<1-2 sentence description of what this table shows and its key insight>",
  "needs_review": <true|false>
}"""


def enrich_table_block(
    block: dict[str, Any],
    blocks: list[dict[str, Any]],
    idx: int,
    image_file: Path | None,
    ledger: dict,
) -> dict[str, Any]:
    """
    One Kimi call: validate + correct + enrich + summarize a table block.
    If the block also has an embedded image, include vision.
    """
    table_src = (
        block.get("content") or block.get("table_body")
        or block.get("text") or block.get("html") or ""
    )
    if isinstance(table_src, dict):
        table_src = table_src.get("html") or table_src.get("table_body") or str(table_src)

    # Garbage check
    table_clean, was_degraded = _clean_text_for_llm(str(table_src))
    col_garbage = _check_table_columns_garbage(table_clean) if table_clean else False

    if was_degraded or col_garbage:
        # Fall back to vision if image is available
        if image_file:
            return _enrich_table_vision_only(block, image_file, blocks, idx, ledger)
        return {
            "ok": False, "valid": False, "corrections": [],
            "summary": "", "enrichment_notes": "",
            "needs_review": True, "source_quality": "degraded",
            "skipped_reason": "garbage_text",
        }

    ctx, footnotes = _get_context_window(blocks, idx)

    prompt = (
        f"CRITICAL: Fix OCR/extraction errors in this financial table. Output corrected version.\n\n"
        f"EXTRACTED TABLE:\n{table_clean[:4000]}\n\n"
        f"CONTEXT:\n{ctx[:1500]}\n\n"
        f"FOOTNOTES:\n{footnotes[:800]}\n\n"
        "MANDATORY TASKS:\n"
        "1. VALIDATE: Check every number, date, alignment for OCR errors (misread 0→O, 1→l, etc).\n"
        "2. CORRECT: If invalid → output complete corrected markdown table in 'corrected_table' field.\n"
        "   IMPORTANT: corrected_table must be valid GFM markdown table format:\n"
        "   | Header1 | Header2 |\n"
        "   |---------|----------|\n"
        "   | Value1  | Value2  |\n"
        "3. REPORT: List each correction as {location, original, corrected}.\n"
        "4. ENRICH: Add missing info from context/footnotes.\n"
        "5. SUMMARIZE: 1-2 sentences on what this table shows.\n\n"
        f"Return ONLY valid JSON matching this schema:\n{_TABLE_JSON_SCHEMA}"
    )

    t0 = time.time()
    # Use text-only for tables (faster, HTML is source of truth)
    # Only use vision if explicitly needed or if text extraction is degraded
    if was_degraded and image_file:
        raw = _gemini_vision(image_file, prompt, _ledger=ledger)
    else:
        # Reduce prompt size if table HTML is very large
        if len(table_clean) > 3000:
            table_clean_short = table_clean[:2500]
        else:
            table_clean_short = table_clean
        prompt_short = prompt.replace(f"TABLE TEXT (from PDF extraction):\n{table_clean[:4000]}",
                                      f"TABLE TEXT (from PDF extraction):\n{table_clean_short}")
        raw = _llm_text(prompt_short, _ledger=ledger)
    latency_ms = int((time.time() - t0) * 1000)

    result = _parse_llm_json(raw)

    # Graceful degradation: if LLM fails, skip enrichment instead of marking "error"
    if raw is None:
        result = {
            "ok": False,
            "needs_review": False,
            "error": "llm_unavailable",
            "summary": "(Table enrichment skipped — LLM unavailable)",
            "enrichment_notes": "",
            "corrections": [],
        }
    result.update({
        "ok": True,
        "latency_ms": latency_ms,
        "source_quality": "degraded" if was_degraded else "ok",
        "had_embedded_image": image_file is not None,
        "raw_response": raw,
    })

    # Apply corrections patch-in-place (prefer corrected_table if available)
    corrected_table = result.get("corrected_table", "").strip() if result.get("corrected_table") else None
    if result.get("corrections") or corrected_table:
        _apply_table_corrections(block, result.get("corrections", []), corrected_table)

    return result


def _enrich_table_vision_only(
    block: dict, image_file: Path, blocks: list, idx: int, ledger: dict
) -> dict[str, Any]:
    """Vision-only table enrichment when text is garbage."""
    ctx, footnotes = _get_context_window(blocks, idx)
    prompt = (
        "Extract the complete table from this image. "
        "Validate, correct any OCR errors, and summarize it.\n"
        f"SURROUNDING CONTEXT:\n{ctx[:800]}\nFOOTNOTES:\n{footnotes[:400]}\n\n"
        f"Return ONLY this JSON:\n{_TABLE_JSON_SCHEMA}"
    )
    t0  = time.time()
    raw = _gemini_vision(image_file, prompt, _ledger=ledger)
    result = _parse_llm_json(raw)
    result.update({
        "ok": True, "latency_ms": int((time.time() - t0) * 1000),
        "source_quality": "vision_recovery", "had_embedded_image": True,
        "raw_response": raw,
    })
    return result


def _apply_table_corrections(block: dict, corrections: list[dict], corrected_table: str | None = None) -> None:
    """Patch the block's table text with corrections; keep originals for audit."""
    original = block.get("content") or block.get("table_body") or block.get("text") or ""
    if isinstance(original, dict):
        original = original.get("html") or original.get("table_body") or str(original)
    original = str(original)
    block.setdefault("original_table_text", original)

    # Prefer LLM-generated corrected table if available
    if corrected_table and corrected_table.strip():
        block["content"] = corrected_table
        return

    # Fall back to correction-by-correction string replacement
    if not corrections:
        return
    for corr in corrections:
        orig_val = corr.get("original", "")
        new_val  = corr.get("corrected", "")
        if orig_val and new_val:
            original = original.replace(orig_val, new_val, 1)
    block["content"] = original


# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE / CHART Enrichment  (one combined Kimi vision call)
# ─────────────────────────────────────────────────────────────────────────────
_VISUAL_JSON_SCHEMA = """\
{
  "visual_type": "<bar_chart|line_chart|pie_chart|scatter_plot|table_in_image|diagram|photo|logo|watermark|other>",
  "title": "<exact title text visible in image, or empty string>",
  "extracted_text": "<ALL text visible in image, verbatim, newline-separated>",
  "data_values": [{"label": "<series or row label>", "value": "<numeric value with units>"}],
  "summary": "<1-2 sentence description of what this visual shows and its key insight>",
  "enrichment_notes": "<extra info from footnotes/context that clarifies this visual>",
  "corrected": null,
  "needs_review": false
}"""


def enrich_visual_block(
    block: dict[str, Any],
    image_file: Path,
    blocks: list[dict[str, Any]],
    idx: int,
    route: str,
    ledger: dict,
) -> dict[str, Any]:
    """
    One Kimi vision call: extract all visual info + enrich with context.
    Returns structured JSON matching _VISUAL_JSON_SCHEMA.
    """
    # 1. Check perceptual hash cache
    cached = _cache_get(image_file, route)
    if cached:
        ledger["cache_hits"] += 1
        return cached

    # 2. Gather context
    ctx, footnotes = _get_context_window(blocks, idx)
    page = (block.get("page_idx") or 0) + 1
    ocr_text = block.get("ocr_text") or block.get("caption") or ""
    ocr_clean, ocr_degraded = _clean_text_for_llm(str(ocr_text)) if ocr_text else ("", True)
    blk_type = str(block.get("type") or "image").lower()

    # 3. Build prompt
    prompt_parts = [
        f"Document visual extracted from page {page}.",
        f"Block classification: {route} | Block type: {blk_type}",
    ]
    if not ocr_degraded and ocr_clean:
        prompt_parts.append(f"\nMinerU OCR/caption text:\n{ocr_clean[:800]}")
    if ctx:
        prompt_parts.append(f"\nSurrounding page context:\n{ctx[:1200]}")
    if footnotes:
        prompt_parts.append(f"\nPage footnotes/asides:\n{footnotes[:600]}")
    prompt_parts += [
        "\n\nEXTRACT ALL CONTENT from this image for document RAG ingestion.",
        "- Extract EVERY piece of visible text verbatim",
        "- For charts: extract ALL data points, axis labels, legend entries",
        "- For tables: extract ALL rows and columns as data_values",
        "- For diagrams: describe structure and label all nodes",
        "- Use enrichment_notes to add context from footnotes that clarifies this visual",
        f"\nReturn ONLY this exact JSON schema:\n{_VISUAL_JSON_SCHEMA}",
    ]
    prompt = "\n".join(prompt_parts)

    t0  = time.time()
    raw = _gemini_vision(image_file, prompt, _ledger=ledger)
    latency_ms = int((time.time() - t0) * 1000)

    result = _parse_llm_json(raw)

    # Retry once if extracted_text AND data_values are both empty
    if result.get("ok") is not False:
        if not result.get("extracted_text") and not result.get("data_values"):
            retry_prompt = prompt + "\n\nYou missed data. List EVERY number and label visible."
            raw2 = _gemini_vision(image_file, retry_prompt)
            if raw2:
                result2 = _parse_llm_json(raw2)
                if result2.get("extracted_text") or result2.get("data_values"):
                    result = result2
                    result["retried"] = True

    result.update({
        "ok": True,
        "route": route,
        "latency_ms": latency_ms,
        "ocr_source_quality": "degraded" if ocr_degraded else "ok",
    })
    if not result.get("ok"):
        result["ok"] = False

    ledger["llm_calls"] += 1
    _cache_set(image_file, route, result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Table-to-RAG text  (for embedding)
# ─────────────────────────────────────────────────────────────────────────────
def _table_to_rag_text(table_src: str) -> str:
    """Convert GFM/HTML table → embedding-friendly 'Header: Value' lines."""
    if not table_src:
        return ""
    if "|" in table_src and not table_src.strip().startswith("<"):
        lines = [
            l.strip() for l in table_src.splitlines()
            if l.strip() and "|" in l and not re.match(r"^\|[-:| ]+\|$", l.strip())
        ]
        if len(lines) >= 2:
            headers = [c.strip() for c in lines[0].strip("|").split("|")]
            rows    = []
            for line in lines[1:]:
                cells    = [c.strip() for c in line.strip("|").split("|")]
                row_text = " | ".join(
                    f"{h}: {v}" for h, v in zip(headers, cells)
                    if h and v and v not in ("", "—", "-")
                )
                if row_text:
                    rows.append(row_text)
            return "\n".join(rows)
    return re.sub(r"\s{2,}", " ", re.sub(r"<[^>]+>", " ", table_src)).strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Misclassified Table Detection + Rescue
# ─────────────────────────────────────────────────────────────────────────────
from heuristics import _TABLE_SIGNAL_RE  # noqa: E402


def fix_misclassified_tables(
    blocks: list[dict[str, Any]], task: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """
    Find text/paragraph blocks that contain tabular patterns.
    Send to Kimi text to reformat as proper GFM markdown table.
    """
    candidates = [
        (idx, blk)
        for idx, blk in enumerate(blocks)
        if str(blk.get("type") or blk.get("category") or "").lower() in {"text", "paragraph"}
        and _TABLE_SIGNAL_RE.search(extract_block_text(blk))
    ]
    if not candidates:
        return blocks, 0

    task.update({"status": "fixing_tables", "updated_at": now_ms(), "table_fixes_total": len(candidates)})
    fixed = 0

    schema = '{"is_table":true,"markdown_table":"| H1 | H2 |\\n|---|---|\\n| v | v |","headers":[],"needs_review":false}'

    def _fix_one(args: tuple) -> tuple[int, dict, dict]:
        idx, blk = args
        text = extract_block_text(blk)
        clean, degraded = _clean_text_for_llm(text)
        if degraded or len(clean) < 20:
            return idx, blk, {"ok": False, "needs_review": True}
        prompt = (
            "This text from a document contains misclassified tabular data.\n"
            "Reformat as a proper GFM markdown table. Preserve every number exactly. No hallucinations.\n"
            f"Return ONLY valid JSON:\n{schema}\n\nText:\n{clean[:3000]}"
        )
        raw    = _gemini_text(prompt)  # table fix calls don't have a per-call ledger
        result = _parse_llm_json(raw)
        result["ok"] = True
        return idx, blk, result

    with ThreadPoolExecutor(max_workers=_LLM_CONCURRENCY) as pool:
        futures = {pool.submit(_fix_one, item): item for item in candidates}
        for future in as_completed(futures):
            try:
                idx, blk, result = future.result()
            except Exception as exc:
                idx, blk = futures[future]
                result = {"ok": False, "needs_review": True, "error": repr(exc)}
            if result.get("ok") and result.get("is_table") and result.get("markdown_table"):
                blk["original_type"]    = blk.get("type")
                blk["type"]             = "table"
                blk["content"]          = result["markdown_table"]
                blk["table_reformat"]   = result
                fixed += 1

    task.update({"table_fixes_applied": fixed, "updated_at": now_ms()})
    return blocks, fixed


# ─────────────────────────────────────────────────────────────────────────────
#  Markdown Merging
# ─────────────────────────────────────────────────────────────────────────────
def merge_visuals_into_md(md_text: str, enrichments: list[dict]) -> str:
    """Replace image placeholders in full.md with Kimi-extracted content."""
    img_map: dict[str, dict] = {}
    for item in enrichments:
        ref = item.get("image_path", "")
        s   = item.get("enrichment", {})
        if isinstance(s, dict) and s.get("ok") and ref:
            img_map[Path(ref).name] = s

    def _replace(m: re.Match) -> str:
        fname = Path(m.group(1)).name
        s = img_map.get(fname)
        if not s:
            return m.group(0)
        vtype  = s.get("visual_type") or "visual"
        title  = s.get("title") or ""
        text   = (s.get("extracted_text") or "").strip()[:1200]
        data   = s.get("data_values") or []
        summary = s.get("summary") or ""
        notes   = s.get("enrichment_notes") or ""
        header  = f"**[{vtype.upper()}]** {title}".strip()
        lines   = [f"\n> {header}"]
        if summary:
            lines.append(f">\n> 📋 *{summary}*")
        if text:
            lines.append(f">\n> {text}")
        if data:
            pairs = " · ".join(
                f"{d.get('label', '')}: {d.get('value', '')}" for d in data[:15]
                if d.get("label") or d.get("value")
            )
            if pairs:
                lines.append(f">\n> *{pairs}*")
        if notes:
            lines.append(f">\n> 🗒 {notes}")
        return "\n".join(lines) + "\n"

    return re.sub(r"!\[\]\(([^)]+)\)", _replace, md_text)


def merge_all_enrichments_into_md(
    md_text: str,
    table_enrichments: list[dict],
    visual_enrichments: list[dict],
) -> str:
    """Merge all table & visual enrichments into markdown inline.

    Inserts corrections and AI summaries directly into the original content,
    keeping structure intact but adding enrichment callouts.
    """
    # Build block index maps
    table_map: dict[int, dict] = {}  # block_index → enrichment
    for item in table_enrichments:
        idx = item.get("block_index")
        if idx is not None:
            table_map[idx] = item.get("enrichment", {})

    visual_map: dict[str, dict] = {}  # image filename → enrichment
    for item in visual_enrichments:
        ref = item.get("image_path", "")
        s = item.get("enrichment", {})
        if isinstance(s, dict) and s.get("ok") and ref:
            visual_map[Path(ref).name] = s

    # Process visuals (replace image placeholders)
    def _replace_visual(m: re.Match) -> str:
        fname = Path(m.group(1)).name
        s = visual_map.get(fname)
        if not s:
            return m.group(0)
        vtype = s.get("visual_type") or "visual"
        title = s.get("title") or ""
        text = (s.get("extracted_text") or "").strip()[:1200]
        data = s.get("data_values") or []
        summary = s.get("summary") or ""
        notes = s.get("enrichment_notes") or ""
        header = f"**[{vtype.upper()}]** {title}".strip()
        lines = [f"\n> {header}"]
        if summary:
            lines.append(f">\n> 📋 *{summary}*")
        if text:
            lines.append(f">\n> {text}")
        if data:
            pairs = " · ".join(
                f"{d.get('label', '')}: {d.get('value', '')}" for d in data[:15]
                if d.get("label") or d.get("value")
            )
            if pairs:
                lines.append(f">\n> *{pairs}*")
        if notes:
            lines.append(f">\n> 🗒 {notes}")
        return "\n".join(lines) + "\n"

    merged_md = re.sub(r"!\[\]\(([^)]+)\)", _replace_visual, md_text)

    # Process tables (insert enrichment after table blocks)
    # Look for markdown tables and insert corrections + summary after
    lines = merged_md.split("\n")
    result_lines: list[str] = []
    i = 0
    while i < len(lines):
        result_lines.append(lines[i])
        # Check if this is a table separator line (|---|---|...)
        if i < len(lines) - 1 and "|" in lines[i] and re.match(r"^\|[\s\-:|]+\|$", lines[i].strip()):
            # Skip to end of table (next non-pipe line or empty line)
            j = i + 1
            while j < len(lines) and "|" in lines[j]:
                result_lines.append(lines[j])
                j += 1
            # Insert table enrichment after table (if available)
            # Note: we can't easily map back to block_index, so we insert enrichments for all
            for idx, enrichment in table_map.items():
                if not enrichment.get("ok", True):
                    continue  # skip invalid tables
                lines_to_add = ["\n> **AI Enrichment:**"]
                if enrichment.get("corrections"):
                    lines_to_add.append(f"> **Corrections:** {len(enrichment['corrections'])} cell(s) fixed")
                    for c in enrichment["corrections"][:3]:  # show first 3
                        lines_to_add.append(f">   - {c.get('location','?')}: `{c.get('original','?')}` → `{c.get('corrected','?')}`")
                if enrichment.get("summary"):
                    lines_to_add.append(f">\n> **Summary:** {enrichment['summary']}")
                if enrichment.get("enrichment_notes"):
                    lines_to_add.append(f"> **Context:** {enrichment['enrichment_notes']}")
                if enrichment.get("needs_review"):
                    lines_to_add.append("> ⚠️ **Flagged for review**")
                result_lines.extend(lines_to_add)
                result_lines.append("")
            i = j
            continue
        i += 1

    return "\n".join(result_lines)


# ─────────────────────────────────────────────────────────────────────────────
#  enrichment.md Generator
# ─────────────────────────────────────────────────────────────────────────────
def build_enrichment_md(
    blocks: list[dict[str, Any]],
    visual_enrichments: list[dict],
    table_enrichments: list[dict],
    checks: dict[str, Any],
    ledger: dict,
) -> str:
    lines = [
        "# Document Enrichment Report",
        "",
        "> Generated by DocExtract pipeline — Kimi K2.5 LLM enrichment (HPC-AI)",
        f"> LLM calls: {ledger['llm_calls']} "
        f"| Tokens in: {ledger['input_tokens']:,} (cached: {ledger.get('cached_tokens', 0):,}) "
        f"| Tokens out: {ledger['output_tokens']:,} "
        f"| Cache hits: {ledger['cache_hits']} "
        f"| Est. cost: **${ledger['cost_usd']:.4f}** USD",
        "",
        "---",
        "",
    ]

    # ── Tables ────────────────────────────────────────────────────────────────
    lines += ["## 📊 Table Enrichments", ""]
    if table_enrichments:
        for item in table_enrichments:
            e   = item.get("enrichment", {})
            pg  = item.get("page", "?")
            idx = item.get("block_index", "?")
            lines.append(f"### Block {idx} — Page {pg}")
            valid = e.get("valid", "?")
            lines.append(f"- **Valid:** {'✅ Yes' if valid else '❌ No'}")
            if e.get("corrections"):
                lines.append(f"- **Corrections:** {len(e['corrections'])} cell(s) fixed")
                for c in e["corrections"]:
                    lines.append(f"  - {c.get('location','?')}: `{c.get('original','?')}` → `{c.get('corrected','?')}`")
            if e.get("summary"):
                lines.append(f"- **Summary:** {e['summary']}")
            if e.get("enrichment_notes"):
                lines.append(f"- **Context Notes:** {e['enrichment_notes']}")
            if e.get("needs_review"):
                lines.append("- ⚠️ **Flagged for manual review**")
            lines.append("")
    else:
        lines.append("No table enrichments generated.\n")

    # ── Visuals ───────────────────────────────────────────────────────────────
    lines += ["---", "", "## 🖼 Visual Enrichments", ""]
    if visual_enrichments:
        for item in visual_enrichments:
            e    = item.get("enrichment", {})
            pg   = item.get("page", "?")
            idx  = item.get("block_index", "?")
            vtyp = e.get("visual_type", item.get("type", "?"))
            lines.append(f"### Block {idx} — Page {pg} — {vtyp} [{item.get('route','?')}]")
            if e.get("title"):
                lines.append(f"- **Title:** {e['title']}")
            if e.get("summary"):
                lines.append(f"- **Summary:** {e['summary']}")
            if e.get("extracted_text"):
                snippet = e["extracted_text"][:300].replace("\n", " ")
                lines.append(f"- **Extracted Text:** {snippet}{'…' if len(e['extracted_text']) > 300 else ''}")
            if e.get("data_values"):
                pairs = ", ".join(
                    f"{d.get('label','')}: {d.get('value','')}" for d in e["data_values"][:8]
                )
                lines.append(f"- **Data:** {pairs}")
            if e.get("enrichment_notes"):
                lines.append(f"- **Context Notes:** {e['enrichment_notes']}")
            if e.get("needs_review"):
                lines.append("- ⚠️ **Flagged for manual review**")
            lines.append("")
    else:
        lines.append("No visual enrichments generated.\n")

    # ── Heuristic Checks ─────────────────────────────────────────────────────
    lines += ["---", "", "## 🔍 Heuristic Quality Checks", ""]
    lines.append(checks_markdown(checks))

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Main Enrichment Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def enrich_zip_with_llm(zip_path: Path, task_dir: Path, task: dict[str, Any]) -> Path:
    """
    Full post-processing pipeline:
      1. Extract ZIP
      2. Fix misclassified tables
      3. Enrich all tables (validate + correct + enrich + summarize)
      4. Classify and enrich all visual blocks (images/charts)
      5. Merge into full_enriched.md
      6. Run heuristic checks
      7. Build enrichment.md
      8. Repack enriched ZIP
    """
    extract_dir = task_dir / "zip_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    # ── Locate content list ──────────────────────────────────────────────────
    content_path = (
        next(extract_dir.rglob("*content_list_v2.json"), None)
        or next(extract_dir.rglob("*content_list*.json"), None)
    )
    if not content_path:
        return zip_path  # nothing to enrich

    raw_content = read_json(content_path)
    blocks      = flatten_content_list(raw_content)
    image_files = {
        path.name
        for path in extract_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    }

    ledger = _make_ledger()

    # ── Step 1: Fix misclassified tables ────────────────────────────────────
    task.update({"status": "fixing_tables", "updated_at": now_ms()})
    blocks, table_fixes = fix_misclassified_tables(blocks, task)
    task["table_fixes_applied"] = table_fixes

    # ── Step 2: Build page-image repeat map ─────────────────────────────────
    image_like = {"image", "figure", "fig", "chart", "graph", "diagram"}
    img_page_map: dict[str, set[int]] = {}
    for block in blocks:
        typ = str(block.get("type") or block.get("category") or "").lower()
        if not (typ in image_like or any(k in typ for k in ("image", "chart", "figure"))):
            continue
        ref = image_path_from_block(block)
        if ref:
            img_page_map.setdefault(ref, set()).add(block.get("page_idx") or 0)
    page_image_counts = {ref: len(pages) for ref, pages in img_page_map.items()}

    # ── Step 3: Enrich TABLE blocks ──────────────────────────────────────────
    task.update({"status": "enriching_tables", "updated_at": now_ms()})
    table_items = [
        (idx, blk)
        for idx, blk in enumerate(blocks)
        if str(blk.get("type") or "").lower() == "table"
    ]
    table_enrichments: list[dict] = []

    def _enrich_table_worker(args: tuple) -> dict | None:
        idx, blk = args
        src = blk.get("content") or blk.get("table_body") or blk.get("text") or ""
        if len(str(src)) < 20:
            return None
        # Check for embedded image in the table block
        img_ref  = image_path_from_block(blk)
        img_file = resolve_zip_image(extract_dir, img_ref) if img_ref else None
        result   = enrich_table_block(blk, blocks, idx, img_file, ledger)
        blk["llm_enrichment"] = result
        # RAG text
        corrected_src = blk.get("content") or src
        if isinstance(corrected_src, dict):
            corrected_src = corrected_src.get("html") or corrected_src.get("table_body") or str(corrected_src)
        blk["rag_text"] = _table_to_rag_text(str(corrected_src))
        return {
            "block_index": idx,
            "page": (blk.get("page_idx") or 0) + 1,
            "type": "table",
            "enrichment": result,
        }

    with ThreadPoolExecutor(max_workers=_LLM_CONCURRENCY) as pool:
        futures = {pool.submit(_enrich_table_worker, item): item for item in table_items}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    table_enrichments.append(result)
            except Exception as exc:
                task.setdefault("table_errors", []).append(repr(exc))

    task.update({
        "tables_enriched": len(table_enrichments),
        "updated_at": now_ms(),
    })

    # ── Step 4: Collect visual candidates ───────────────────────────────────
    candidates: list[tuple[int, dict, Path, str, str]] = []
    for idx, block in enumerate(blocks):
        typ = str(block.get("type") or block.get("category") or block.get("mineru_type") or "").lower()
        if not (typ in image_like or any(k in typ for k in ("image", "chart", "figure"))):
            continue
        ref        = image_path_from_block(block)
        image_file = resolve_zip_image(extract_dir, ref)
        if not image_file:
            continue
        route = classify_visual_block(block, page_image_counts)
        block["route"] = route
        candidates.append((idx, block, image_file, ref or "", route))

    # Separate by route
    small_visuals = [(idx, blk, img, ref, route) for idx, blk, img, ref, route in candidates if route == "SMALL_VISUAL"]
    decorative    = [(idx, blk, img, ref, route) for idx, blk, img, ref, route in candidates if route == "DECORATIVE"]
    to_llm        = [(idx, blk, img, ref, route) for idx, blk, img, ref, route in candidates
                     if route not in ("DECORATIVE", "SMALL_VISUAL")]

    # Tag small visuals (OCR already done by MinerU)
    for idx, blk, img, ref, route in small_visuals:
        blk["llm_enrichment"] = {
            "ok": True, "route": "SMALL_VISUAL",
            "visual_type": "other", "skipped_reason": "below_size_threshold",
            "extracted_text": blk.get("ocr_text") or blk.get("caption") or "",
            "summary": "Small visual — OCR text preserved, vision LLM skipped.",
            "needs_review": False,
        }

    task.update({
        "status": "enriching_visuals",
        "updated_at": now_ms(),
        "visuals_total": len(candidates),
        "visuals_decorative": len(decorative),
        "visuals_small": len(small_visuals),
        "visuals_sent_to_llm": len(to_llm),
        "visuals_done": 0,
    })

    # ── Step 5: Enrich visual blocks via Kimi vision ────────────────────────
    visual_enrichments: list[dict] = []
    done_count = 0

    def _enrich_visual_worker(args: tuple) -> dict | None:
        nonlocal done_count
        idx, blk, image_file, ref, route = args
        if block_importance_score(blk) < _IMPORTANCE_THRESHOLD:
            blk["llm_enrichment"] = {
                "ok": True, "route": route, "skipped_reason": "low_importance",
                "visual_type": "other", "needs_review": False,
            }
            return None
        result = enrich_visual_block(blk, image_file, blocks, idx, route, ledger)
        blk["llm_enrichment"] = result
        done_count += 1
        task.update({"visuals_done": done_count, "updated_at": now_ms()})
        return {
            "block_index": idx,
            "page": (blk.get("page_idx") or 0) + 1,
            "type": str(blk.get("type") or "image").lower(),
            "route": route,
            "image_path": ref,
            "enrichment": result,
        }

    with ThreadPoolExecutor(max_workers=_LLM_CONCURRENCY) as pool:
        futures = {pool.submit(_enrich_visual_worker, item): item for item in to_llm}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    visual_enrichments.append(result)
            except Exception as exc:
                orig = futures[future]
                visual_enrichments.append({
                    "block_index": orig[0],
                    "page": (orig[1].get("page_idx") or 0) + 1,
                    "type": str(orig[1].get("type") or "image"),
                    "route": orig[4],
                    "image_path": orig[3],
                    "enrichment": {"ok": False, "error": repr(exc), "needs_review": True},
                })

    task["llm_visual_time_ms"] = now_ms()

    # ── Step 6: Heuristic checks ─────────────────────────────────────────────
    checks = run_heuristic_checks(blocks, image_files)
    (extract_dir / "heuristic_checks.json").write_text(
        json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (extract_dir / "heuristic_checks.md").write_text(
        checks_markdown(checks), encoding="utf-8"
    )
    task["heuristic_summary"] = checks.get("summary", {})
    missed_table_checks = {"suspected_missed_table", "missing_table_on_page"}
    if any(i.get("check") in missed_table_checks for i in checks.get("issues", [])):
        task["suggest_ocr_retry"] = True

    # ── Step 7: Write enriched content list ─────────────────────────────────
    (extract_dir / "content_list_enriched.json").write_text(
        json.dumps(blocks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Legacy visual_summaries.json (for UI compat)
    all_summaries = [
        {
            "block_index": item["block_index"],
            "page": item["page"],
            "type": item["type"],
            "route": item.get("route", "?"),
            "image_path": item.get("image_path", ""),
            "summary": item["enrichment"],  # renamed field — UI reads .summary
        }
        for item in visual_enrichments
    ]
    (extract_dir / "visual_summaries.json").write_text(
        json.dumps(all_summaries, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── Step 8: Merge into full_enriched.md ─────────────────────────────────
    md_path = next(extract_dir.rglob("full.md"), None)
    if md_path and md_path.exists():
        raw_md      = md_path.read_text(encoding="utf-8", errors="ignore")
        # Create comprehensive merged markdown with all enrichments inline
        merged_md = merge_all_enrichments_into_md(raw_md, table_enrichments, visual_enrichments)
        (extract_dir / "final_merged.md").write_text(merged_md, encoding="utf-8")
        # Keep legacy full_enriched.md for backwards compat
        enriched_md = merge_visuals_into_md(raw_md, visual_enrichments)
        (extract_dir / "full_enriched.md").write_text(enriched_md, encoding="utf-8")

    # ── Step 9: Build enrichment.md ─────────────────────────────────────────
    enrichment_md = build_enrichment_md(
        blocks, visual_enrichments, table_enrichments, checks, ledger
    )
    (extract_dir / "enrichment.md").write_text(enrichment_md, encoding="utf-8")

    # Visual summaries markdown (legacy)
    vsmd_lines = [
        "# Visual Enrichments",
        f"Total: {len(candidates)} | LLM: {len(to_llm)} | Decorative: {len(decorative)} | Small: {len(small_visuals)}",
        f"Tokens in: {ledger['input_tokens']} | out: {ledger['output_tokens']} | "
        f"Cache hits: {ledger['cache_hits']} | Est. cost: ${ledger['cost_usd']:.4f}",
        "",
    ]
    for item in visual_enrichments:
        e    = item["enrichment"]
        text = (e.get("extracted_text") or e.get("raw_text", "")) if isinstance(e, dict) else str(e)
        vsmd_lines.append(
            f"## Block {item['block_index']} — p{item['page']} — {item['type']} [{item.get('route','?')}]"
        )
        vsmd_lines.append(text[:400] if text else "No extraction.")
        vsmd_lines.append("")
    (extract_dir / "visual_summaries.md").write_text("\n".join(vsmd_lines), encoding="utf-8")

    task.update({
        "visual_stats": {
            "total": len(candidates), "decorative": len(decorative),
            "small": len(small_visuals), "llm_sent": len(to_llm),
            "successful": sum(1 for v in visual_enrichments if v["enrichment"].get("ok")),
            **ledger,
        },
        "updated_at": now_ms(),
    })

    # ── Step 10: Repack ZIP ──────────────────────────────────────────────────
    enriched_zip = task_dir / "result_enriched.zip"
    with zipfile.ZipFile(enriched_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in extract_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(extract_dir))
    return enriched_zip


# ─────────────────────────────────────────────────────────────────────────────
#  Background Task Runner
# ─────────────────────────────────────────────────────────────────────────────
def process_task(task_id: str) -> None:
    task     = TASKS[task_id]
    task_dir = Path(task["task_dir"])
    timings: dict[str, int] = {}
    wall_start = time.time()
    try:
        t = time.time()
        task.update({"status": "submitting", "updated_at": now_ms()})
        batch_id = submit_local_file_to_mineru(
            Path(task["input_path"]),
            model_version=task["model_version"],
            enable_formula=task["enable_formula"],
            enable_table=task["enable_table"],
            language=task["language"],
            is_ocr=task["is_ocr"],
            page_ranges=task.get("page_ranges") or None,
        )
        timings["submit_ms"] = int((time.time() - t) * 1000)

        task.update({"batch_id": batch_id, "status": "waiting_mineru", "updated_at": now_ms()})
        t = time.time()
        zip_url = poll_mineru_batch(batch_id, task)
        timings["mineru_ms"] = int((time.time() - t) * 1000)

        raw_zip = task_dir / "mineru_result.zip"
        task.update({"status": "downloading", "full_zip_url": zip_url, "updated_at": now_ms()})
        t = time.time()
        download_zip(zip_url, raw_zip)
        timings["download_ms"] = int((time.time() - t) * 1000)

        if task.get("enable_enrichment", True):
            task.update({"status": "enriching", "updated_at": now_ms()})
            t = time.time()
            result_zip = enrich_zip_with_llm(raw_zip, task_dir, task)
            timings["enrich_ms"] = int((time.time() - t) * 1000)
        else:
            result_zip = raw_zip
            timings["enrich_ms"] = 0

        timings["total_ms"] = int((time.time() - wall_start) * 1000)
        task.update({
            "status": "completed",
            "result_path": str(result_zip),
            "timings": timings,
            "updated_at": now_ms(),
        })
    except Exception as exc:
        timings["total_ms"] = int((time.time() - wall_start) * 1000)
        task.update({
            "status": "failed",
            "error": repr(exc),
            "timings": timings,
            "updated_at": now_ms(),
        })


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="DocExtract MinerU — Azure GPT-4o-mini Pipeline")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

_HTML_PATH = ROOT / "ext" / "index.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_ui() -> HTMLResponse:
    if _HTML_PATH.exists():
        return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>index.html not found at ext/index.html</h2>", status_code=404)


@app.get("/health")
def health() -> dict[str, Any]:
    llm_ready = bool(_AZURE_API_KEY and _AZURE_ENDPOINT)
    return {
        "ok": True,
        "service": "docextract-mineru-bridge",
        "mineru_key": bool(
            os.getenv("MINERU_API_KEY") or os.getenv("MINERU_TOKEN") or os.getenv("miner_api_key")
        ),
        "llm_provider": "azure",
        "llm_model": _AZURE_MODEL,
        "llm_deployment": _AZURE_DEPLOYMENT,
        "llm_ready": llm_ready,
        "pil_available": _PIL_AVAILABLE,
        "tesseract_available": _TESSERACT,
    }


@app.get("/models")
def list_models() -> dict[str, Any]:
    """Return available models (Azure GPT-4o-mini only)."""
    return {
        "models": [
            {
                "id": _AZURE_DEPLOYMENT,
                "label": "GPT-4o Mini (Vision + Text)",
                "note": "Azure OpenAI Foundry — primary model for tables + visuals",
                "vision": True,
                "recommended": True,
            }
        ]
    }


@app.post("/tasks")
async def submit_task(
    background_tasks: BackgroundTasks,
    file:          UploadFile = File(None),
    files:         list[UploadFile] = File(None),
    model_version: str  = Form("vlm"),
    enable_formula: str = Form("true"),
    enable_table:  str  = Form("true"),
    language:      str  = Form("en"),
    is_ocr:        str  = Form("false"),
    page_ranges:   str  = Form(""),
    enable_enrichment: str = Form("true"),
) -> dict[str, Any]:
    upload = file or (files[0] if files else None)
    if upload is None:
        return JSONResponse({"error": "expected upload field named 'file' or 'files'"}, status_code=422)

    task_id  = "mineru_" + uuid.uuid4().hex[:12]
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    input_path = task_dir / safe_name(upload.filename or "document.pdf")
    input_path.write_bytes(await upload.read())

    TASKS[task_id] = {
        "task_id":       task_id,
        "status":        "queued",
        "filename":      upload.filename,
        "input_path":    str(input_path),
        "task_dir":      str(task_dir),
        "model_version": model_version,
        "enable_formula": enable_formula.lower() == "true",
        "enable_table":   enable_table.lower() == "true",
        "language":       language,
        "is_ocr":         is_ocr.lower() == "true",
        "page_ranges":    page_ranges,
        "enable_enrichment": enable_enrichment.lower() == "true",
        "created_at":    now_ms(),
        "updated_at":    now_ms(),
    }
    background_tasks.add_task(process_task, task_id)
    return {"task_id": task_id, "status": "queued"}


@app.get("/tasks/{task_id}")
def task_status(task_id: str) -> Any:
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return task


@app.get("/tasks/{task_id}/result")
def task_result(task_id: str) -> Any:
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"error": "task not found"}, status_code=404)
    if task.get("status") != "completed":
        return JSONResponse({"error": "not completed", "status": task.get("status")}, status_code=409)
    return FileResponse(
        task["result_path"],
        media_type="application/zip",
        filename=f"{task_id}_result.zip",
    )


@app.post("/probe/run")
async def probe_run(body: dict[str, Any]) -> dict[str, Any]:
    """
    Model Lab: re-run enrichment on a single block with a chosen model.
    body: {task_id, block_index, model, custom_prompt}
    """
    task_id     = body.get("task_id", "")
    block_idx   = int(body.get("block_index", 0))
    probe_model = body.get("model", _KIMI_MODEL)
    custom_prompt = body.get("custom_prompt", "")

    task = TASKS.get(task_id)
    if not task:
        return {"ok": False, "error": "task not found"}

    task_dir    = Path(task["task_dir"])
    extract_dir = task_dir / "zip_extract"
    content_p   = (
        next(extract_dir.rglob("content_list_enriched.json"), None)
        or next(extract_dir.rglob("*content_list*.json"), None)
    )
    if not content_p:
        return {"ok": False, "error": "no content list found"}

    blocks = flatten_content_list(read_json(content_p))
    if block_idx >= len(blocks):
        return {"ok": False, "error": "block_index out of range"}

    block = blocks[block_idx]
    typ   = str(block.get("type") or "").lower()
    ref   = image_path_from_block(block)
    image_file = resolve_zip_image(extract_dir, ref)

    try:
        t0     = time.time()
        ledger = _make_ledger()
        if typ == "table":
            result = enrich_table_block(block, blocks, block_idx, image_file, ledger)
        elif image_file:
            route  = block.get("route", "UNKNOWN")
            if custom_prompt:
                raw    = _llm_vision(image_file, custom_prompt)
                result = _parse_llm_json(raw)
                result["ok"] = True
            else:
                result = enrich_visual_block(block, image_file, blocks, block_idx, route, ledger)
        else:
            text   = extract_block_text(block)
            prompt = custom_prompt or f"Summarize this document block:\n{text[:2000]}"
            raw    = _llm_text(prompt)
            result = _parse_llm_json(raw)
            result["ok"] = True

        return {
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
            "model": _KIMI_MODEL,
            "parsed": result,
            "raw_response": json.dumps(result),
            "tokens_in": ledger["input_tokens"],
            "tokens_out": ledger["output_tokens"],
        }
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


# ─────────────────────────────────────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
