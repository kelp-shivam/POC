#!/usr/bin/env python3
"""Test Azure OpenAI API (matches mineru_server.py implementation)."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    import openai as _openai_sdk
except ImportError:
    print("✗ openai SDK not installed. Run: pip install openai azure-identity")
    exit(1)

# Config (from env or .env)
_AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
_AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15")
_AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
_AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
_AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
_AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")

def get_client():
    """Create Azure OpenAI client (API key or service principal)."""
    # Auth option 1: API key
    if _AZURE_API_KEY:
        return _openai_sdk.AzureOpenAI(
            api_key=_AZURE_API_KEY,
            azure_endpoint=_AZURE_ENDPOINT,
            api_version=_AZURE_API_VERSION,
        )
    # Auth option 2: Service principal
    elif _AZURE_TENANT_ID and _AZURE_CLIENT_ID and _AZURE_CLIENT_SECRET:
        from azure.identity import ClientSecretCredential
        credential = ClientSecretCredential(
            tenant_id=_AZURE_TENANT_ID,
            client_id=_AZURE_CLIENT_ID,
            client_secret=_AZURE_CLIENT_SECRET,
        )
        return _openai_sdk.AzureOpenAI(
            azure_endpoint=_AZURE_ENDPOINT,
            azure_ad_token_provider=credential.get_token,
            api_version=_AZURE_API_VERSION,
        )
    else:
        raise RuntimeError("No Azure OpenAI auth: need AZURE_OPENAI_API_KEY or (AZURE_TENANT_ID + AZURE_CLIENT_ID + AZURE_CLIENT_SECRET)")

try:
    print("Initializing Azure OpenAI client...")
    client = get_client()
    print(f"✓ Client created. Endpoint: {_AZURE_ENDPOINT}, Model: {_AZURE_DEPLOYMENT}")

    print("\nSending test request...")
    response = client.chat.completions.create(
        model=_AZURE_DEPLOYMENT,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'API is working!' in one word."},
        ],
        max_tokens=10,
    )

    print("✓ Azure OpenAI API working!")
    print(f"Response: {response.choices[0].message.content}")
    print(f"Tokens — in: {response.usage.prompt_tokens}, out: {response.usage.completion_tokens}")

except Exception as e:
    print(f"✗ Azure OpenAI failed: {type(e).__name__}: {e}")
