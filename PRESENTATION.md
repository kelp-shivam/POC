# DocExtract: Complete Document Intelligence Pipeline
## MinerU + Kimi K2.5 Enrichment System

---

## The Problem

### Document Processing Landscape
- **Azure Document Intelligence**: Partial extraction only. Requires additional processing for semantic understanding.
- **LLaMA Parse**: 10 tokens/page input → 400k tokens output → **$500+ per 100-page document**. Cost prohibitive.
- **MinerU alone**: Excellent extraction but tables/visuals lack semantic context.

**Reality**: No single tool delivers complete, production-grade document intelligence at reasonable cost.

---

## Our Solution

### DocExtract Pipeline
A complete, modular system for **complex document structures**:

```
PDF Input
    ↓
[MinerU] OCR + Layout Analysis
    ├─ Extract text, tables, images
    ├─ Preserve document structure
    └─ 40-50 sec per document
    ↓
[Quality Checks] Heuristic Validation
    ├─ Missing table detection
    ├─ Structure integrity
    └─ Early warning for re-processing
    ↓
[Kimi K2.5 Vision] Semantic Enrichment (Optional)
    ├─ Table validation & correction
    ├─ Visual classification (chart/diagram/photo)
    ├─ Extracted text + data values
    └─ Contextual enrichment from surrounding blocks
    ↓
[Output] Rich, Structured Document
    ├─ enriched.zip (all artifacts)
    ├─ content_list_enriched.json (block-by-block metadata)
    ├─ enrichment.md (human-readable summary)
    └─ full_enriched.md (inline visual summaries)
```

---

## What We're Doing

### 1. **Extraction Phase** (MinerU)
- Cloud-based GPU OCR & layout detection
- Handles: PDFs, scans, complex layouts
- Output: Blocks, tables, images with coordinates
- Time: **~45 seconds**

### 2. **Validation Phase** (Heuristics)
- Detect corruption, missing tables, text degradation
- Classify visuals (decorative vs. data-bearing)
- Flag blocks needing human review
- Time: **<1 second**

### 3. **Enrichment Phase** (Kimi K2.5 Vision LLM)
- **Tables**: Validate structure, correct OCR errors, extract key insights
- **Visuals**: Classify type, extract embedded text, parse data points (charts → axis labels + values)
- **Context**: Inject footnotes, headers, surrounding text into enrichment
- **Speed**: 2 concurrent requests (avoiding API rate limits)
- Time: **~5-15 minutes** for large documents (87-188 visuals)

### 4. **Assembly Phase**
- Merge enrichment results into structured JSON
- Generate human-readable enrichment.md
- Preserve original + corrected versions for audit
- Create embedable RAG content from tables

---

## Key Features

### ✅ Table Intelligence
- **Problem**: OCR tables have cell errors, misaligned columns
- **Solution**: Send to Kimi with surrounding context (footnotes, headers)
- **Output**: 
  - Corrected markdown table
  - Cell-level corrections logged
  - Key insight summary
  - Raw LLM response stored for audit

### ✅ Visual Intelligence
- **Problem**: Charts, diagrams lose meaning in extraction
- **Solution**: Vision LLM analyzes images + OCR text + context
- **Output**:
  - Classification (bar chart / line chart / scatter / table-in-image / diagram)
  - ALL visible text extracted verbatim
  - Data point labels + numeric values
  - Context-aware enrichment notes
  - Perceptual hash dedup (skip repeated logos)

### ✅ Quality Assurance
- Heuristic checks for corruption, missing content
- Manual review flagging for high-uncertainty blocks
- Severity scoring: critical / warning / info
- Completeness metrics per page

### ✅ Audit Trail
- Original extraction preserved unchanged
- All corrections logged with location + original + new value
- Raw LLM responses stored in content_list_enriched.json
- Full traceability for compliance

---

## Architecture

### Components
1. **mineru_server.py** - FastAPI backend
   - Orchestrates MinerU submission & polling
   - Manages Kimi API calls with rate-limit-aware concurrency
   - Implements block-by-block enrichment
   - Generates output artifacts

2. **streamlit_app.py** - User interface
   - PDF upload & task submission
   - Live pipeline progress display
   - Real-time task polling
   - Model Lab (block re-enrichment UI)
   - Interactive enrichment.md viewer

3. **heuristics.py** - Quality checks
   - Missing table detection
   - Text degradation scoring
   - Visual importance ranking
   - Structure integrity validation

### Data Flow
```
User → Streamlit → FastAPI Backend → MinerU Cloud
                        ↓
                   [Rate-Limited]
                        ↓
                   Kimi K2.5 API
                        ↓
                   Result ZIP → Streamlit → Download
```

---

## Why This Works

### Cost Efficiency
- **MinerU**: ~$0.05 per document (cloud GPU)
- **Kimi enrichment**: ~$0.10-0.50 per document (LLM, varies by visual count)
- **Total**: **$0.15-0.60 per document**
- vs. **$5+ for LLaMA Parse** or **Azure DI (partial) + external LLM**

### Completeness
- Extraction: Preserves document structure (MinerU strength)
- Enrichment: Adds semantic understanding (Kimi strength)
- Quality: Detects failure modes early (custom heuristics)
- **Not leaving money/accuracy on the table** (unlike extraction-only tools)

### Flexibility
- **Skip enrichment** if cost is priority → 50 sec total
- **Full enrichment** for complex, high-value documents → 5-15 min
- **Adjustable concurrency** to respect API limits
- **Pluggable**: Replace Kimi with Claude API, GPT-4V, or local models

---

## Use Cases

### ✓ Financial Documents
- Annual reports: Extract financials + narrative context
- Tables corrected automatically
- Charts analyzed for trends

### ✓ Technical Documentation
- Schematics: Diagrams classified, components extracted
- Tables: Specs validated, missing rows detected
- Cross-references enriched with context

### ✓ Legal Contracts
- Clauses extracted with structure preserved
- Highlighted text recovered (often lost in OCR)
- Tables (payment terms, schedules) validated

### ✓ Academic Papers
- Figures + captions enriched with surrounding text
- Tables: Data integrity checked, summary extracted
- References enriched with context

### ✗ NOT for
- Simple single-page documents (over-engineering)
- Real-time processing (5-15 min latency)
- Handwritten documents (MinerU has limits)

---

## How to Use

### Local Deployment
```bash
# Start backend
PORT=8000 python3 -m mineru_server &

# Start UI
streamlit run streamlit_app.py
```

### Workflow
1. Upload PDF via Streamlit
2. Choose enrichment level:
   - **OFF**: Extraction only (~50 sec)
   - **ON**: Full enrichment (5-15 min)
3. Monitor progress in real-time
4. Download result ZIP
5. View enrichment.md in Streamlit or text editor

### Output Artifacts
```
result_enriched.zip/
├── content_list_enriched.json    ← All blocks + enrichment metadata
├── full_enriched.md               ← Original markdown + visual summaries
├── enrichment.md                  ← Report: tables + visuals + quality checks
├── enriched_images/               ← All extracted images
├── heuristic_checks.json          ← QA findings (structured)
└── heuristic_checks.md            ← QA findings (human-readable)
```

---

## Configuration

### Environment Variables
```bash
MINERU_API_KEY=<your-mineru-token>
api_key_1=<kimi-hpc-ai-key>
api_key_2=<kimi-hpc-ai-key>
api_key_3=<kimi-hpc-ai-key>
api_key_4=<kimi-hpc-ai-key>

LLM_CONCURRENCY=2              # Reduce if rate-limited
IMPORTANCE_THRESHOLD=0.25      # Skip trivial visuals
MINERU_TIMEOUT_SECONDS=3600    # Extraction timeout
```

### Tuning
- **More concurrency** (4+): Faster but risk rate limits
- **Less concurrency** (1): Slower but safer for API quotas
- **Lower importance threshold** (0.1): Enrich more visuals (more cost)
- **Higher importance threshold** (0.5): Skip small/trivial visuals (lower cost)

---

## Results

### First Document (Financial Report, 30 pages)
- **Extraction**: 44.5 sec
- **Enrichment**: 660 sec (101 visuals → 77 successful)
- **Tables**: 18 extracted + validated
- **Quality**: 3 tables flagged for review
- **Cost**: ~$0.36 USD

### Second Document (Technical Spec, 2 pages)
- **Extraction**: 25 sec
- **Enrichment**: 78 sec (9 visuals → 8 successful)
- **Tables**: 3 extracted + validated
- **Quality**: 0 issues
- **Cost**: ~$0.08 USD

---

## What's Next

### Improvements Planned
1. **Streaming output** - Return results as they complete (not wait for all enrichment)
2. **Batch processing** - Upload 100+ documents, process queue
3. **Custom LLM prompts** - Model Lab UI for tuning enrichment
4. **Multi-language** - Extend beyond English
5. **Local LLM fallback** - Use Llama/Mistral if Kimi unavailable

### Open Questions
- Integrate with RAG/semantic search pipeline?
- Real-time streaming to storage (S3/BigQuery)?
- Document classification before enrichment?
- Feedback loop: Mark incorrect corrections → retrain?

---

## Conclusion

**DocExtract** bridges the gap between fast extraction and complete understanding:
- **Faster than pure LLM** (MinerU handles layout)
- **Cheaper than LLaMA Parse** (structured enrichment only where needed)
- **More complete than DI alone** (adds semantic layer)
- **Audit-ready** (preserves originals, logs all corrections)

**Best for**: Complex, high-value documents where accuracy matters and cost is secondary.
