# DocExtract Pipeline — Technical Reference

## How to Run

```bash
# Terminal 1 — backend bridge
python3 -m uvicorn mineru_server:app --host 127.0.0.1 --port 8000

# Terminal 2 — Streamlit UI
python3 -m streamlit run streamlit_app.py
```

Open http://localhost:8501 in your browser.  
Set `.env` with `MINERU_API_KEY` (or `miner_api_key`) and `NVIDIA_API_KEY` before starting.

---

## Architecture

```
PDF
 └─ mineru_server.py (FastAPI, port 8000)
       ├─ submit_local_file_to_mineru()   → MinerU cloud API (VLM, model_version=vlm)
       ├─ poll_mineru_batch()             → polls until done, gets full_zip_url
       ├─ download_zip()                  → downloads result ZIP from CDN
       ├─ enrich_zip_with_visual_summaries()
       │     ├─ fix_misclassified_tables()   → LLM fixes text blocks that are actually tables
       │     ├─ classify_visual_block()      → routes each image: CHART | DECORATIVE | UNKNOWN
       │     └─ summarize_visual()           → NVIDIA LLM extracts text/data from image
       └─ run_heuristic_checks()          → validates blocks, flags issues

streamlit_app.py (port 8501)
 └─ connects to port 8000, submits PDFs, polls, displays blocks + annotated PDF
```

---

## File Reference

| File | Purpose |
|---|---|
| `mineru_server.py` | FastAPI backend bridge — all pipeline logic lives here |
| `heuristics.py` | Block-level validation — math checks, duplicate detection, missed table detection |
| `streamlit_app.py` | UI — submit, poll, review blocks, view annotated PDF |
| `.env` | API keys (never commit) |

---

## mineru_server.py — Function Reference

### `load_env(path)`
Reads `.env` file and populates `os.environ`. Preserves existing env vars (uses `setdefault`). Handles unquoted and quoted values. Called once at module import.

---

### `mineru_token()`
Reads MinerU API token from env. Checks multiple key names in order:
`MINERU_API_KEY` → `MINERU_TOKEN` → `MINER_U_API_KEY` → `MINER_U_TOKEN` → `miner_api_key` (lowercase, matches the `.env` key name).

---

### `llm_config() → (key, base_url, model)`
Reads LLM credentials. Detects NVIDIA vs OpenAI endpoint automatically:
- If `NVIDIA_BASE_URL` contains `nvidia.com` or `integrate.api` → defaults to `nvidia/llama-3.2-90b-vision-instruct`
- Otherwise defaults to `gpt-4o-mini`
- Override with `VISION_MODEL` env var

---

### `submit_local_file_to_mineru(file_path, ...)`
Two-step MinerU upload:
1. `POST /api/v4/file-urls/batch` → gets a pre-signed S3 upload URL + `batch_id`
2. `PUT <upload_url>` → streams the file bytes directly to S3

Parameters sent: `model_version=vlm`, `enable_formula=true`, `enable_table=true`, `language`, and optionally `is_ocr`, `page_ranges` (per-file in the `files` array).

Returns `batch_id` for polling.

---

### `poll_mineru_batch(batch_id, task)`
Polls `GET /api/v4/extract-results/batch/{batch_id}` every 5 seconds (configurable via `MINERU_POLL_SECONDS`).  
MinerU states: `waiting-file` → `pending` → `running` → `done` | `failed`  
On `done`: returns `full_zip_url` (CDN link to the result ZIP).  
Timeout: 3600s by default (`MINERU_TIMEOUT_SECONDS`).

---

### `download_zip(zip_url, dest)`
Streams the result ZIP from the CDN URL to disk in 1 MB chunks. Timeout: 30s connect, 900s read.

---

### `flatten_content_list(raw)`
Normalises MinerU's two output formats into a flat list of block dicts:
- **v2 (preferred)**: nested `[[page0_blocks], [page1_blocks], ...]` → flattened, adds `page_idx` and `order`
- **v1 (fallback)**: already flat list

---

### `extract_block_text(block)`
Extracts all text from a block regardless of schema variant. Checks keys: `text`, `content`, `ocr_text`, `caption`, `html`, and nested `content` dicts/lists. Returns up to 6000 chars.

---

### `_bbox_area(bbox)`
Computes area in pts² from `[x1, y1, x2, y2]` bbox. Returns 0 if malformed.

---

### `classify_visual_block(block, page_image_counts) → "CHART" | "DECORATIVE" | "UNKNOWN"`
Decides whether to send an image block to the LLM or skip it.

| Rule | Result |
|---|---|
| bbox area < 2% of A4 page area (~10,000 pt²) | `DECORATIVE` — skip |
| Same image path appears on 3+ distinct pages | `DECORATIVE` — skip (repeated logo/watermark/header) |
| Block type is `chart` or `graph` | `CHART` — send to LLM |
| Everything else | `UNKNOWN` — send to LLM |

**On logos specifically:** A company logo in the top-left corner of every page will typically have a small bbox AND appear on every page. Both rules fire independently — it will be classified `DECORATIVE` and **not sent to the LLM**. No OCR is run on logos. They are silently dropped from visual processing.

**Edge case:** A logo that appears only once (e.g. cover page) with a large bbox will be classified `UNKNOWN` and sent to the LLM. The LLM prompt asks for all visible text, so it will OCR any text in that logo image and include it in `extracted_text`. This is correct behaviour — a cover-page logo may contain the company name which is useful for RAG.

---

### `summarize_visual(image_path, block, context, route)`
Sends an image to the NVIDIA vision LLM. Used for `CHART` and `UNKNOWN` blocks.

Prompt instructs the model to:
- Extract ALL visible text verbatim (axis labels, legend, footnotes, data labels)
- For charts: list every series and its values
- For tables-as-images: reproduce as markdown
- For figures/diagrams: describe labelled elements

Returns JSON:
```json
{
  "ok": true,
  "visual_type": "bar_chart",
  "extracted_text": "Revenue Q1: 1200, Q2: 3400...",
  "data_values": [{"label": "Q1 Revenue", "value": "1200"}],
  "summary": "Bar chart showing quarterly revenue.",
  "needs_review": false,
  "model": "nvidia/llama-3.2-90b-vision-instruct"
}
```

**Does it OCR logos?** Only if the logo passes the `classify_visual_block` filter (i.e. it's large AND appears on fewer than 3 pages). If it does reach here, yes — the LLM reads all text in the image.

---

### `_llm_text_request(prompt)`
Text-only LLM call (no image). Uses `TEXT_MODEL` env var if set, otherwise falls back to the vision model (vision models handle text-only prompts fine). Returns raw response string or `None` on failure.

---

### `reformat_text_as_table(block)`
Takes a block whose text contains tabular patterns and asks the LLM to reformat it as a proper GFM markdown table. Strict prompt: preserve every number exactly, return `is_table: false` if not confident. Returns JSON with `is_table`, `markdown_table`, `headers`, `needs_review`.

---

### `fix_misclassified_tables(blocks, task)`
**This is the fix for the most common MinerU failure mode: tables typed as `paragraph` or `text`.**

1. Scans all `text`/`paragraph` blocks for tabular signals using `_TABLE_SIGNAL_RE`:
   - Pipe-delimited rows (`| value | value |`)
   - Two numbers separated by 2+ spaces (aligned columns, e.g. `1,200  3,400`)
2. Sends each candidate to `reformat_text_as_table()` in parallel (up to 3 concurrent)
3. If the LLM confirms `is_table: true`, rewrites the block in-place:
   - `type` → `"table"` (was `"paragraph"`)
   - `content` → clean markdown table
   - `original_type` → preserved for audit
   - `table_reformat` → full LLM response attached

Returns `(blocks, count_fixed)`.

---

### `enrich_zip_with_visual_summaries(zip_path, task_dir, task)`
Main post-processing orchestrator. Steps in order:

1. Extract ZIP to `zip_extract/`
2. Find content list — prefers `*content_list_v2.json` (richer types: `paragraph`, `title`, `page_header`, `page_number`) over v1 flat format
3. **`fix_misclassified_tables()`** — fix text blocks that are really tables
4. Build `page_image_counts` — count how many distinct pages each image path appears on
5. Classify all visual blocks with `classify_visual_block()`
6. Filter: only non-`DECORATIVE` blocks go to LLM
7. Run LLM calls in parallel (max 3 concurrent — respects NVIDIA free tier rate limit)
8. Run `run_heuristic_checks()` — validate all blocks, flag issues
9. Set `suggest_ocr_retry: true` if heuristics detect suspected missed tables
10. Write outputs: `content_list_enriched.json`, `visual_summaries.json`, `visual_summaries.md`, `heuristic_checks.json`, `heuristic_checks.md`
11. Repack everything into `result_enriched.zip`

---

### `process_task(task_id)`
The background worker for a submitted task. Status progression:
```
queued → submitting → waiting-file → pending → running → downloading → post_processing → summarizing_visuals → fixing_tables → completed
                                                                                                                              └─ failed
```

---

## heuristics.py — Function Reference

### `run_heuristic_checks(blocks, image_names, page_count)`
Runs all checks on the block list. Returns `{summary: {error, warning, info}, issues: [...], passed: bool}`.

Checks performed:

| Check | Severity | What it catches |
|---|---|---|
| `page_presence` | warning | Block with no `page_idx` |
| `page_range` | error | Block page outside document page count |
| `bbox_presence` | warning | Non-header block with no bounding box |
| `bbox_shape` | error | Bbox that isn't 4 numbers |
| `bbox_geometry` | error | Bbox with zero/negative width or height |
| `reading_order` | warning | Block order goes backwards on a page |
| `empty_text` | warning | Text/paragraph/title block with <3 chars |
| `duplicate_text` | warning | Same text (>80 chars) appears in two blocks — excludes `page_header` and `page_number` by design |
| `image_link` | warning/error | Image block with no path, or path not in ZIP |
| `visual_summary` | warning | Image block with no successful LLM summary |
| `table_math` | error | Table total row doesn't match computed column sum (±1 tolerance) |
| `cross_reference` | info | "Table 3" referenced in text but no labeled block found |
| `suspected_missed_table` | warning | `paragraph`/`text` block contains pipe rows or aligned numbers |
| `missing_table_on_page` | warning | Text references "Table N" / "Schedule II" but no table block on that page |

### `check_table_math(markdown, block_index, page)`
Parses a GFM markdown table, finds rows labelled "total", "subtotal", "net income" etc., recomputes column sums from preceding rows, and flags if the stated total differs by more than 1.

Handles: `$`, `€`, `£`, commas in numbers, parentheses as negatives, `%` suffix.

---

## Task Status Fields

When you call `GET /tasks/{task_id}` you get:

```json
{
  "status": "completed",
  "mineru_state": "done",
  "mineru_progress": {"extracted_pages": 6, "total_pages": 6},
  "batch_id": "...",
  "full_zip_url": "https://cdn-mineru...",
  "table_fixes_applied": 2,
  "table_fixes_total": 3,
  "visuals_total": 4,
  "visuals_filtered_decorative": 2,
  "visuals_sent_to_llm": 2,
  "visuals_done": 2,
  "visual_stats": {
    "total_visual_blocks": 4,
    "filtered_decorative": 2,
    "sent_to_llm": 2,
    "successful": 2,
    "needs_review": 0
  },
  "heuristic_summary": {"error": 0, "warning": 1, "info": 0},
  "suggest_ocr_retry": true
}
```

`suggest_ocr_retry: true` means heuristics detected suspected missed tables — consider resubmitting with `is_ocr=true`.

---

## ENV Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MINERU_API_KEY` or `miner_api_key` | Yes | — | MinerU cloud API token |
| `NVIDIA_API_KEY` | Yes | — | NVIDIA / OpenAI-compatible LLM key |
| `NVIDIA_BASE_URL` | No | `https://integrate.api.nvidia.com/v1` | LLM endpoint |
| `VISION_MODEL` | No | auto-detected | Override vision LLM model name |
| `TEXT_MODEL` | No | same as VISION_MODEL | Override text-only LLM model name |
| `MINERU_POLL_SECONDS` | No | `5` | Polling interval while waiting for MinerU |
| `MINERU_TIMEOUT_SECONDS` | No | `3600` | Max wait for MinerU extraction |
| `PORT` | No | `8000` | Backend server port |

---

## Output Files (inside enriched ZIP)

| File | Content |
|---|---|
| `full.md` | Complete document as markdown (tables as HTML) |
| `content_list_enriched.json` | All blocks with added `route`, `llm_visual_summary`, `table_reformat` fields |
| `visual_summaries.json` | One entry per image block sent to LLM |
| `visual_summaries.md` | Human-readable version of visual summaries |
| `heuristic_checks.json` | Full issue list with severity, check name, page, block index |
| `heuristic_checks.md` | Human-readable heuristic report |
| `images/` | Cropped image files for visual blocks (and table renders) |

---

## Accuracy Signals (what to show in a presentation)

| Metric | Where to read it |
|---|---|
| Table math errors | `heuristic_summary.error` |
| Suspected missed tables fixed | `table_fixes_applied` |
| Images filtered (not sent to LLM) | `visuals_filtered_decorative` |
| Images requiring human review | `visual_stats.needs_review` |
| Needs OCR retry | `suggest_ocr_retry` |
| Cross-references resolved | count `info` issues with `check=cross_reference` in `heuristic_checks.json` |
