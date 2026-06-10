# DocExtract MinerU Review

DocExtract is a local review workflow around the MinerU API. It submits PDFs to MinerU, downloads the returned extraction ZIP, enriches extracted charts/images with an LLM vision summary, runs deterministic heuristic checks, and gives you two review interfaces:

- `index.html`: browser-based Markdown/PDF/JSON explorer.
- `streamlit_app.py`: Streamlit submission and review UI with block-to-PDF interaction.

Secrets stay in `.env` and are read by the local bridge, not by the browser UI.

## What It Does

1. Uploads a local PDF through `mineru_server.py`.
2. Requests MinerU batch upload URLs.
3. Uploads the PDF to MinerU.
4. Polls MinerU until the extraction is complete.
5. Downloads MinerU's `full_zip_url`.
6. Finds extracted images/figures/charts from `content_list.json`.
7. Sends extracted visual assets plus nearby JSON/text context to a vision-capable LLM.
8. Writes visual summaries back into the result bundle.
9. Runs heuristic checks over blocks, tables, images, bbox metadata, and references.
10. Displays the result in Streamlit or `index.html`.

## Files

- `mineru_server.py`: FastAPI bridge for MinerU submission, polling, result download, LLM visual summaries, and ZIP enrichment.
- `streamlit_app.py`: Streamlit UI for submitting PDFs, polling tasks, loading result ZIPs, selecting blocks, and viewing PDF/Markdown/JSON/checks.
- `heuristics.py`: Fresh heuristic validation code. It was informed by `hurestics.txt` but does not copy that code directly.
- `index.html`: existing advanced browser UI for Markdown rendering, PDF layout validation, JSON block explorer, and visual summary display.
- `requirements.txt`: Python dependencies.
- `hurestics.txt`: reference-only prototype notes/code.

## Environment

Create `.env` in the project root:

```bash
MINERU_API_KEY=your_mineru_token

# Choose one LLM provider compatible with OpenAI chat/completions vision payloads.
OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_VISION_MODEL=gpt-4o-mini

# Or NVIDIA/OpenAI-compatible config:
# NVIDIA_API_KEY=your_nvidia_key
# NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
# NVIDIA_VISION_MODEL=meta/llama-3.2-90b-vision-instruct
```

The bridge checks these names for MinerU:

- `MINERU_API_KEY`
- `MINERU_TOKEN`
- `MINER_U_API_KEY`
- `MINER_U_TOKEN`

## Install

```bash
cd /Users/shivam/Desktop/DocExtract
python3 -m pip install -r requirements.txt
```

## Run

Start the local bridge:

```bash
cd /Users/shivam/Desktop/DocExtract
python3 mineru_server.py
```

Start Streamlit in another terminal:

```bash
cd /Users/shivam/Desktop/DocExtract
streamlit run streamlit_app.py
```

Default bridge URL:

```text
http://127.0.0.1:8000
```

You can also open `index.html` directly in a browser and point it at the same bridge URL.

## Streamlit Workflow

1. Open the Streamlit app.
2. Confirm the bridge endpoint.
3. Upload a PDF.
4. Choose MinerU mode:
   - `vlm`: better for complex layouts and visual-heavy PDFs.
   - `pipeline`: generally faster, less vision-heavy.
5. Click `Submit to MinerU`.
6. Use `Poll / Download` until the task is complete.
7. Review:
   - `Review`: select a block and see the matching PDF page with bbox overlay.
   - `Markdown`: extracted Markdown.
   - `Checks`: heuristic validation report.
   - `Visual Summaries`: LLM summaries of extracted images/charts.
   - `Raw`: task/result metadata.

You can also load an existing MinerU result ZIP from the sidebar.

## Output Artifacts

The enriched ZIP contains MinerU's original files plus:

- `visual_summaries.json`: structured summaries for extracted visuals.
- `visual_summaries.md`: readable visual summary appendix.
- `content_list_enriched.json`: flattened block list with `llm_visual_summary` attached where available.
- `heuristic_checks.json`: machine-readable validation issues.
- `heuristic_checks.md`: readable checks report.

## Heuristic Checks

Current checks include:

- Table total recomputation for simple Markdown tables.
- Missing page numbers.
- Page number out-of-range when a page count is available.
- Missing or malformed bounding boxes.
- Non-positive bbox geometry.
- Reading order regressions within a page.
- Empty text-like blocks.
- Large duplicate text blocks.
- Visual blocks without image paths.
- Visual blocks whose image asset is missing from the ZIP.
- Visual blocks without successful LLM summaries.
- Lightweight table/figure/chart cross-reference checks.

These checks are guardrails, not formal proof of extraction correctness.

## Accuracy Expectations

This approach should be strongest for:

- PDFs with clear digital text.
- Standard reports with ordinary headings, paragraphs, tables, and figures.
- Visual review workflows where a human can inspect the selected Markdown/JSON block against the PDF page.
- Chart/image summarization where the goal is narrative understanding, not guaranteed numeric extraction.

It will be weaker for:

- Low-resolution scans.
- Rotated pages, skewed scans, heavy handwriting, and dense multi-column scientific pages.
- Tables with merged cells, nested headers, footnotes, spanning columns, or totals that depend on hidden rows.
- Charts where exact values require pixel-level axis calibration.
- Documents where MinerU does not emit stable `bbox`, `page_idx`, or image paths.
- Any workflow that needs audited, legally binding extraction accuracy without human validation.

## Current Limitations

- No benchmark dataset is included yet, so there is no measured precision/recall/F1.
- LLM visual summaries are not deterministic and may omit or misread small labels.
- Visual summaries currently run sequentially, so many images can be slow and costly.
- No automatic filtering of decorative images beyond what MinerU returns.
- The Streamlit PDF bbox overlay uses a simple coordinate scaling heuristic. It may be off when MinerU uses a coordinate system that differs from the rendered PDF page.
- The local task registry is in memory. Restarting `mineru_server.py` loses task status, although downloaded task files remain under `.mineru_tasks`.
- No auth is applied to the local bridge. Keep it bound to localhost unless you add authentication.
- The heuristic table math checker only handles simple Markdown tables and simple total rows.
- There is no retry policy for failed LLM visual summary calls beyond recording the error.
- `index.html` has richer browser interactions than Streamlit; Streamlit gives practical block-to-page selection but not the same hover/canvas behavior.

## Recommended Next Steps

1. Build an evaluation set of PDFs with hand-labeled text blocks, tables, images, chart captions, and bbox/page references.
2. Measure:
   - page assignment accuracy
   - bbox IoU against ground truth
   - text exact/semantic match
   - table cell accuracy
   - visual-summary usefulness scored by a rubric
   - false positive/false negative rate of heuristic checks
3. Add visual classification before LLM calls to skip logos/icons/decorative images.
4. Add parallel LLM summarization with rate limiting.
5. Persist task metadata to SQLite or JSONL.
6. Add a manual correction/export flow for reviewed blocks.
7. Improve coordinate calibration by reading page dimensions from MinerU metadata when available.
8. Add OCR confidence and image quality checks for scanned documents.

## Security Notes

- Do not commit `.env`.
- MinerU and LLM API keys are used by `mineru_server.py`.
- The browser and Streamlit UI should call only the local bridge.
- If exposing the bridge beyond localhost, add authentication and restrict CORS.
