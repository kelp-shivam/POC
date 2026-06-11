#!/usr/bin/env python3
"""Discover Azure AI Foundry project resources."""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    from azure.ai.projects import AIProjectClient
    from azure.identity import EnvironmentCredential
except ImportError:
    print("Install: pip install azure-ai-projects azure-identity")
    sys.exit(1)

endpoint = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT", "").strip()
if not endpoint:
    print("✗ Missing AZURE_AI_FOUNDRY_ENDPOINT in .env")
    print("  Set: AZURE_AI_FOUNDRY_ENDPOINT=https://meren-mnhbzrk6-eastus2.api.azureml.ms")
    sys.exit(1)

print(f"Connecting to: {endpoint}\n")

try:
    credential = EnvironmentCredential()
    client = AIProjectClient(endpoint=endpoint, credential=credential)
    print("✓ Connected\n")
except Exception as e:
    print(f"✗ Connection failed: {e}")
    sys.exit(1)

# 1. Deployments
print("=" * 50)
print("[1] MODEL DEPLOYMENTS")
print("=" * 50)
try:
    deployments = client.models.list_deployments()
    deps = list(deployments)
    if not deps:
        print("(none)")
    else:
        for dep in deps:
            print(f"Name: {dep.name}")
            print(f"  Model: {dep.model_name} v{dep.model_version}")
            print()
except Exception as e:
    print(f"Error: {e}\n")

# 2. Connections
print("=" * 50)
print("[2] CONNECTIONS (Compute, Storage, APIs)")
print("=" * 50)
try:
    conns = client.connections.list()
    conn_list = list(conns)
    if not conn_list:
        print("(none)")
    else:
        for conn in conn_list:
            print(f"Name: {conn.name}")
            print(f"  Type: {conn.type}")
            if hasattr(conn, "target"):
                print(f"  Target: {conn.target}")
            print()
except Exception as e:
    print(f"Error: {e}\n")

# 3. Data assets
print("=" * 50)
print("[3] DATA ASSETS")
print("=" * 50)
try:
    assets = client.data.list()
    asset_list = list(assets)
    if not asset_list:
        print("(none)")
    else:
        for asset in asset_list:
            print(f"Name: {asset.name} (v{asset.version})")
except Exception as e:
    print(f"Error: {e}\n")

print("\n" + "=" * 50)
print("SUMMARY")
print("=" * 50)
print(f"Deployments: {len(deps) if 'deps' in locals() else '?'}")
print(f"Connections: {len(conn_list) if 'conn_list' in locals() else '?'}")
print(f"Data assets: {len(asset_list) if 'asset_list' in locals() else '?'}")
