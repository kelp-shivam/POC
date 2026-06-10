"""
Quick connectivity test for Kimi K2.5 via HPC-AI (OpenAI-compatible endpoint).
Reads api_key_1 from .env — same key used by mineru_server.py.
"""
import os
from pathlib import Path

# Load .env manually (no dotenv dependency required)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

try:
    import openai
except ImportError:
    raise SystemExit("openai package not installed. Run: pip install openai")

KIMI_BASE_URL = "https://api.hpc-ai.com/inference/v1"
KIMI_MODEL    = "moonshotai/kimi-k2.5"

# Pick first available key
key = next(
    (os.getenv(f"api_key_{i}", "") for i in range(1, 10) if os.getenv(f"api_key_{i}")),
    None,
)
if not key:
    raise SystemExit("No api_key_1 … api_key_9 found in .env")

print(f"Testing Kimi K2.5 via HPC-AI  (key: {key[:12]}…)")

client = openai.OpenAI(api_key=key, base_url=KIMI_BASE_URL)

resp = client.chat.completions.create(
    model=KIMI_MODEL,
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Say HELLO in one word"},
    ],
    max_tokens=16,
    temperature=0.0,
    timeout=30,
)

# ── Extract content ────────────────────────────────────────────────────────
content = resp.choices[0].message.content
print(f"\n✅ Kimi K2.5 connection OK")
print(f"📝 Response content : {content!r}")
print(f"🏁 Finish reason    : {resp.choices[0].finish_reason}")

# ── Token usage ────────────────────────────────────────────────────────────
usage = resp.usage
tok_in  = usage.prompt_tokens     if usage else 0
tok_out = usage.completion_tokens if usage else 0
print(f"\n📊 Token usage:")
print(f"   Input  tokens : {tok_in:,}")
print(f"   Output tokens : {tok_out:,}")
print(f"   Total  tokens : {tok_in + tok_out:,}")

# ── Cost calculation (Kimi K2.5 HPC-AI official pricing) ──────────────────
COST_PER_1M_IN  = 0.30   # $0.30 / 1M input tokens  (uncached)
COST_PER_1M_OUT = 1.50   # $1.50 / 1M output tokens

cost_in  = tok_in  * COST_PER_1M_IN  / 1_000_000
cost_out = tok_out * COST_PER_1M_OUT / 1_000_000
cost_total = cost_in + cost_out

print(f"\n💰 Cost estimate (Kimi K2.5 @ HPC-AI):")
print(f"   Input  : {tok_in:,} × $0.30/M = ${cost_in:.8f}")
print(f"   Output : {tok_out:,} × $1.50/M = ${cost_out:.8f}")
print(f"   Total  : ${cost_total:.8f}  (≈ ${cost_total * 1000:.6f} per 1k calls like this)")
print(f"\n   Rate card: Input $0.30/M | Cached $0.05/M | Output $1.50/M")