from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any


TOTAL_RE = re.compile(r"\b(total|subtotal|sum|grand total|net income|gross profit)\b", re.I)
# Must be followed by a number or number-prefixed label (e.g. Table 1, Fig. 2A) — not plain words
REF_RE = re.compile(r"\b(table|figure|fig\.?|chart|image)\s*([0-9][A-Za-z0-9.-]*|[A-Z]-[0-9]+)\b", re.I)

# Patterns that strongly suggest tabular content inside a text block
_TABLE_SIGNAL_RE = re.compile(
    r"(\|\s*\S.*\|\s*\S)|"           # pipe-delimited rows
    r"(\d[\d,\.]+\s{2,}\d[\d,\.]+)", # two numbers separated by 2+ spaces (aligned columns)
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
    """Detect mixed decimal/thousand separators (Asian vs European): 1,234.56 vs 1.234,56"""
    issues: list[CheckIssue] = []

    # Find all number patterns
    european_decimal = re.findall(r"\d{1,3}\.\d{3}[.,]\d{2}", text)  # 1.234,56
    us_decimal = re.findall(r"\d{1,3},\d{3}\.\d{2}", text)  # 1,234.56

    if european_decimal and us_decimal:
        issues.append(CheckIssue(
            "warning", "decimal_format_mixed",
            f"Mixed number formats detected (US 1,234.56 AND European 1.234,56) — may indicate OCR errors.",
            block_index, page, "text"
        ))
    return issues


def check_date_format_consistency(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    """Detect mixed date formats: MM/DD/YY vs DD/MM/YY"""
    issues: list[CheckIssue] = []

    # MM/DD/YY pattern (month first, day > 12 impossible)
    us_dates = re.findall(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-](\d{2,4})\b", text)
    # DD/MM/YY pattern (day first, month > 12 impossible)
    eu_dates = re.findall(r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-](\d{2,4})\b", text)

    if us_dates and eu_dates and len(us_dates) > 1 and len(eu_dates) > 1:
        issues.append(CheckIssue(
            "warning", "date_format_mixed",
            f"Mixed date formats detected (MM/DD vs DD/MM) — ambiguous which is correct.",
            block_index, page, "text"
        ))
    return issues


def check_currency_consistency(text: str, block_index: int, page: int | None) -> list[CheckIssue]:
    """Detect multiple currency symbols in same block (indication of copy-paste errors)"""
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
    """Detect likely OCR errors: 0→O, 1→l (lowercase L), 8→B in numeric contexts"""
    issues: list[CheckIssue] = []

    # Look for suspicious patterns: letter 'O' or 'l' next to numbers
    suspicious = re.findall(r"\b\d+[Ol]+\b|\b[Ol]+\d+\b", text)

    if suspicious:
        issues.append(CheckIssue(
            "info", "ocr_suspicious",
            f"Possible OCR character confusion detected (0↔O, 1↔l) in: {', '.join(suspicious[:3])}",
            block_index, page, "text"
        ))
    return issues


def check_page_reference_validity(text: str, block_index: int, page: int | None, total_pages: int | None) -> list[CheckIssue]:
    """Verify page references exist (e.g., 'see page 150' when document has 80 pages)"""
    issues: list[CheckIssue] = []

    if not total_pages:
        return issues

    # Find "page X" references
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

    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[1:]]
    issues: list[CheckIssue] = []
    numeric_rows: list[list[float | None]] = []

    for row in rows:
        label = row[0] if row else ""
        nums = [parse_number(cell) for cell in row[1:]]
        if TOTAL_RE.search(label):
            for col_idx, expected in enumerate(nums):
                if expected is None:
                    continue
                values = [r[col_idx] for r in numeric_rows if col_idx < len(r) and r[col_idx] is not None]
                if len(values) < 2:
                    continue
                computed = sum(values)
                delta = abs(expected - computed)
                if delta > 1.0:
                    issues.append(
                        CheckIssue(
                            "error",
                            "table_math",
                            f"Total row '{label}' column {col_idx + 2} says {expected:g}, computed {computed:g}.",
                            block_index,
                            page,
                            "text",
                        )
                    )
        else:
            numeric_rows.append(nums)
    return issues


def run_heuristic_checks(
    blocks: list[dict[str, Any]],
    image_names: set[str] | None = None,
    page_count: int | None = None,
) -> dict[str, Any]:
    image_names = image_names or set()
    issues: list[CheckIssue] = []
    seen_text: dict[str, int] = {}
    last_order_by_page: dict[int, int] = {}
    labels: set[str] = set()
    refs: list[tuple[str, int, int | None]] = []
    table_pages: set[int] = set()  # pages that have at least one confirmed table block
    table_refs_by_page: list[tuple[str, int, int | None]] = []  # (ref, idx, page)

    for idx, block in enumerate(blocks):
        typ = block_type(block)
        page = block_page(block)
        text = block_text(block)
        bbox = block.get("bbox") or block.get("bounding_box")

        if page is None:
            issues.append(CheckIssue("warning", "page_presence", "Block has no page number.", idx, None, "page_idx"))
        elif page_count and (page < 1 or page > page_count):
            issues.append(CheckIssue("error", "page_range", f"Block page {page} is outside document page count {page_count}.", idx, page, "page_idx"))

        if bbox is None:
            if typ not in {"page_header", "page_footer", "page_number"}:
                issues.append(CheckIssue("warning", "bbox_presence", "Block has no bounding box.", idx, page, "bbox"))
        elif not (isinstance(bbox, list) and len(bbox) >= 4 and all(isinstance(x, (int, float)) for x in bbox[:4])):
            issues.append(CheckIssue("error", "bbox_shape", "Bounding box should be four numeric coordinates.", idx, page, "bbox"))
        else:
            x1, y1, x2, y2 = bbox[:4]
            if x2 <= x1 or y2 <= y1:
                issues.append(CheckIssue("error", "bbox_geometry", "Bounding box has non-positive width or height.", idx, page, "bbox"))

        order = block.get("order")
        if page is not None and isinstance(order, int):
            last = last_order_by_page.get(page, -1)
            if order < last:
                issues.append(CheckIssue("warning", "reading_order", "Block order goes backwards on this page.", idx, page, "order"))
            last_order_by_page[page] = max(last, order)

        if typ in {"paragraph", "text", "title", "list"} and len(text.strip()) < 3:
            issues.append(CheckIssue("warning", "empty_text", f"{typ} block has almost no text.", idx, page, "text"))

        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if len(normalized) > 80 and typ not in {"page_header", "page_footer", "page_number"}:
            old = seen_text.get(normalized)
            if old is not None:
                issues.append(CheckIssue("warning", "duplicate_text", f"Text duplicates block {old}.", idx, page, "text"))
            else:
                seen_text[normalized] = idx

        if typ in {"image", "figure", "fig", "chart", "graph"} or "image" in typ or "figure" in typ or "chart" in typ:
            ref = image_ref(block)
            if not ref:
                issues.append(CheckIssue("warning", "image_link", "Visual block has no image path.", idx, page, "img_path"))
            elif image_names and ref.split("/")[-1] not in image_names:
                issues.append(CheckIssue("error", "image_link", f"Image asset '{ref}' was not found in the result ZIP.", idx, page, "img_path"))
            summary = block.get("llm_visual_summary")
            if not isinstance(summary, dict) or not summary.get("ok"):
                issues.append(CheckIssue("warning", "visual_summary", "Visual block has no successful LLM summary.", idx, page, "llm_visual_summary"))

        if typ == "table":
            issues.extend(check_table_math(text, idx, page))
            if page is not None:
                table_pages.add(page)

        # Detect table-like content hiding inside text/paragraph blocks
        if typ in {"text", "paragraph"} and _TABLE_SIGNAL_RE.search(text):
            issues.append(CheckIssue(
                "warning", "suspected_missed_table",
                "Text block contains tabular patterns (pipe rows or aligned numbers) — MinerU may have missed a table here.",
                idx, page, "text",
            ))

        # NEW: Number format consistency (Asian vs European decimal/thousand separators)
        issues.extend(check_number_format_consistency(text, idx, page))

        # NEW: Date format consistency (MM/DD vs DD/MM)
        issues.extend(check_date_format_consistency(text, idx, page))

        # NEW: Currency symbol consistency
        issues.extend(check_currency_consistency(text, idx, page))

        # NEW: OCR character confusion (0→O, 1→l)
        issues.extend(check_ocr_character_confusion(text, idx, page))

        # NEW: Page reference validity
        total_pages = page_count or (max((block_page(b) for b in blocks if block_page(b)), default=None))
        issues.extend(check_page_reference_validity(text, idx, page, total_pages))

        # Track explicit table references (e.g. "as per Table 3", "see Schedule II")
        for match in _TABLE_REF_RE.finditer(text):
            table_refs_by_page.append((
                f"{match.group(1).lower()} {match.group(2).lower()}",
                idx, page,
            ))

        caption = text[:160]
        label_match = REF_RE.search(caption)
        if typ in {"table", "image", "figure", "chart"} and label_match:
            labels.add(f"{label_match.group(1).lower().replace('fig.', 'figure')} {label_match.group(2).lower()}")
        for match in REF_RE.finditer(text):
            refs.append((f"{match.group(1).lower().replace('fig.', 'figure')} {match.group(2).lower()}", idx, page))

    for ref, idx, page in refs:
        if ref not in labels:
            issues.append(CheckIssue("info", "cross_reference", f"Reference '{ref}' was mentioned but no matching labeled block was found.", idx, page, "text"))

    # Flag pages where a table is explicitly referenced but no table block was extracted
    for ref, idx, page in table_refs_by_page:
        if page is not None and page not in table_pages:
            issues.append(CheckIssue(
                "warning", "missing_table_on_page",
                f"Text references '{ref}' but no table block was extracted from page {page}. Possible missed table.",
                idx, page, "text",
            ))

    counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1

    return {
        "summary": counts,
        "issues": [issue.to_dict() for issue in issues],
        "passed": counts.get("error", 0) == 0,
    }


def checks_markdown(report: dict[str, Any]) -> str:
    lines = ["# Heuristic Checks", ""]
    summary = report.get("summary", {})
    lines.append(
        f"Errors: {summary.get('error', 0)} | Warnings: {summary.get('warning', 0)} | Info: {summary.get('info', 0)}"
    )
    lines.append("")
    for issue in report.get("issues", []):
        loc = []
        if issue.get("page"):
            loc.append(f"page {issue['page']}")
        if issue.get("block_index") is not None:
            loc.append(f"block {issue['block_index']}")
        where = f" ({', '.join(loc)})" if loc else ""
        lines.append(f"- **{issue['severity'].upper()} {issue['check']}**{where}: {issue['message']}")
    if not report.get("issues"):
        lines.append("- No heuristic issues found.")
    return "\n".join(lines)


def dumps_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
