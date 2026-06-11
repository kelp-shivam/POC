#!/usr/bin/env python3
"""Test Azure Document Intelligence API."""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
except ImportError:
    print("Install: pip install azure-ai-documentintelligence")
    sys.exit(1)

endpoint = os.getenv("AZURE_DI_ENDPOINT", "").strip()
key = os.getenv("AZURE_DI_KEY", "").strip()

if not endpoint or not key:
    print("✗ Missing AZURE_DI_ENDPOINT or AZURE_DI_KEY")
    sys.exit(1)

print(f"Endpoint: {endpoint}")
print(f"Key: {key[:20]}...\n")

try:
    print("Creating client...")
    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key)
    )
    print("✓ Client created\n")

    # Test with a simple PDF (create one for testing)
    test_pdf = Path(__file__).parent / "test_doc.pdf"

    if not test_pdf.exists():
        print("⚠ No test_doc.pdf found. Create a test PDF to verify.")
        print("  Then run: python3 test_azure_di.py")
        sys.exit(0)

    print(f"Analyzing {test_pdf.name}...")
    with open(test_pdf, "rb") as f:
        poller = client.begin_analyze_document(
            model_id="prebuilt-layout",
            document=f,
            content_type="application/pdf"
        )

    result = poller.result()

    print("✓ Azure DI API working!")
    print(f"Pages: {len(result.pages)}")
    print(f"Paragraphs: {len(result.paragraphs)}")
    print(f"Tables: {len(result.tables)}")

except Exception as e:
    print(f"✗ Error: {type(e).__name__}")
    print(f"Message: {e}")
