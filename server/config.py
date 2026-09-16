"""Runtime configuration, read once from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# The cleaned menu produced by clean_menu.py. Regenerate it rather than editing by hand.
MENU_PATH = Path(os.getenv("MENU_PATH", BASE_DIR / "menu_clean.json"))

# Never hardcode this and never ship it to the browser; the whole point of the backend
# is that the key stays on the server. Get one at https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


def _collect_keys() -> list[str]:
    """
    Every usable key, in the order they should be tried.

    Groq's free tier caps tokens per day per organization, so a spare key from a separate
    account is a fresh budget. Keys come from GROQ_API_KEYS (comma-separated) and
    GROQ_API_KEY, plus any GROQ_API_KEY_2, _3, ... — whichever is convenient. Duplicates
    are dropped, order preserved.

    Note: several keys from the SAME Groq account share one budget, so rotation only buys
    headroom when the keys belong to different accounts.
    """
    found: list[str] = []
    raw = [os.getenv("GROQ_API_KEYS", "")]
    raw.append(GROQ_API_KEY)
    for n in range(2, 11):
        raw.append(os.getenv(f"GROQ_API_KEY_{n}", ""))

    for entry in raw:
        for key in entry.split(","):
            key = key.strip().strip("'\"")
            if key and key not in found:
                found.append(key)
    return found


GROQ_API_KEYS = _collect_keys()

# Groq exposes an OpenAI-compatible Chat Completions API.
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Groq serves open-weight models, and which ones a key can reach varies by account —
# check with GET {GROQ_BASE_URL}/models rather than assuming. Each model carries its own
# tokens-per-day budget on the free tier, so an exhausted model is fixed by switching to
# another rather than waiting. Swap via MODEL without touching code.
MODEL = os.getenv("MODEL", "qwen/qwen3.8-27b")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))

# Low temperature: this assistant looks things up and fills in ids. It is not writing prose,
# and creative variation here shows up as wrong option_ids.
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.3"))

# Upper bound on tool round-trips per user message. Open-weight models retry more often
# than frontier ones when a cart call is refused, so this is a little more generous than
# the two hops a clean customised add actually needs.
MAX_TOOL_HOPS = int(os.getenv("MAX_TOOL_HOPS", "8"))

# Retries for Groq's short input-tokens-per-minute throttle, which typically clears in
# under a second. Does not apply to an exhausted daily budget, which is not retryable.
RATE_LIMIT_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "3"))

# Longest we will sleep waiting out a throttle before giving up. Beyond this the window is
# genuinely full, not momentarily busy, and a customer should get an honest error rather
# than a long spinner.
MAX_THROTTLE_WAIT_SECONDS = float(os.getenv("MAX_THROTTLE_WAIT_SECONDS", "3"))

# Conversation turns kept per session. The menu dominates the prompt, so history is cheap,
# but this bounds memory growth on a long-lived session.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "40"))

SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))

# Browser origins allowed to call this API. Set to the YOPOS site origin in deployment;
# "*" is a development default only.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Per-session request throttle.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

# When true the assistant is allowed to estimate nutrition from item names. Off by default
# and intended to stay off: the menu carries no nutrition data, so anything it says in this
# mode is fabricated. See prompts.py.
ALLOW_NUTRITION_INFERENCE = os.getenv("ALLOW_NUTRITION_INFERENCE", "false").lower() == "true"
