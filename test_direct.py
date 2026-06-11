#!/usr/bin/env python3
"""Direct Azure OpenAI test with EnvironmentCredential."""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    from openai import AzureOpenAI
    from azure.identity import EnvironmentCredential
except ImportError:
    print("Install: pip install openai azure-identity")
    sys.exit(1)

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()

if not endpoint:
    print("✗ Missing AZURE_OPENAI_ENDPOINT")
    sys.exit(1)

print(f"Endpoint: {endpoint}")
print(f"Deployment: {deployment}\n")

try:
    print("Getting credential...")
    credential = EnvironmentCredential()

    print("Creating client...")
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=lambda: credential.get_token("https://cognitiveservices.azure.com/.default").token,
        api_version="2024-05-01-preview"
    )

    print("Sending request...\n")
    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": "Say 'working' in one word."}],
        max_tokens=10
    )

    print("✓ Success!")
    print(f"Response: {response.choices[0].message.content.strip()}")
    print(f"Tokens: in={response.usage.prompt_tokens}, out={response.usage.completion_tokens}")

except Exception as e:
    print(f"✗ Error: {type(e).__name__}")
    print(f"Message: {e}")
