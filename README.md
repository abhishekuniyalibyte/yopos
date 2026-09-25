# YOPOS AI ordering assistant

A chat assistant for a takeaway's website. It answers questions about the menu, shows the
items as clickable tiles, and builds a cart through conversation. It embeds in any site
with one script tag.

```
menu.json ──clean_menu.py──> menu_clean.json ──> server/ <── widget/widget.js
(client export)              (135 items)         FastAPI      one <script> tag
```

Runs on **Groq** through its OpenAI-compatible Chat Completions API. The default model is
`qwen/qwen3.8-27b`.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add your GROQ_API_KEY — https://console.groq.com/keys
uvicorn server.app:app --reload
```

Then open **http://localhost:8000**: a demo storefront showing the real menu, with the
assistant in the bottom-right corner.

`menu_clean.json` is committed, so there is nothing to generate. After the client's
`menu.json` or `menu_mapping.json` changes, rebuild it with `python3 clean_menu.py`.
Never edit `menu_clean.json` by hand.

To put the assistant on any other page, this is the whole integration:

```html
<script src="http://localhost:8000/widget.js" data-api="http://localhost:8000" defer></script>
```

Tests: `pytest -q` (100 tests, no API key needed).

## What it does

- Answers menu questions, opening hours, address and delivery radius.
- Shows the items it offers as **tiles** (name, category, price). Clicking a tile picks
  that item.
- Adds items to the cart and asks for each required choice (sauce, salad, drink, bread).
  It never picks a choice for the customer.
- Views, removes from and clears the cart. It stops there: no payment and no order
  placement.

## Layout

| Path | What it does |
|---|---|
| `clean_menu.py` | Turns the client's 2.3 MB `menu.json` into `menu_clean.json` (135 items) |
| `menu_mapping.json` | Our editorial decisions: meal-time tags, step labels, exclusions |
| `server/config.py` | Settings from `.env` |
| `server/menu.py` | Loads the menu; the source of truth every id is checked against |
| `server/cart.py` | Cart state **and all validation**, the layer between the model and an order |
| `server/tools.py` | Tool schemas the model sees, plus the dispatcher |
| `server/prompts.py` | System prompt, including the nutrition rules |
| `server/agent.py` | The Groq call, the tool loop, and turning replies into item tiles |
| `server/keys.py` | Rotation across several Groq API keys |
| `server/sessions.py` | Per-visitor history and cart, with a TTL |
| `server/app.py` | FastAPI routes |
| `widget/widget.js` | Embeddable frontend: no framework, no build step, no API key |
| `widget/index.html` | Demo storefront, served at `/` |
| `PROJECT.md` | Project report: what was built, what went wrong, what is still open |
| `CLAUDE.md` | Rules and history for anyone (or any AI assistant) changing the code |

## API keys and rate limits

On Groq's free tier, every account has a daily token budget per model and a per-minute
cap on input tokens (about 7k). A single key runs out quickly, so the server rotates
through several keys. Give them in `.env` in whichever form is convenient:

```bash
GROQ_API_KEYS=gsk_aaa,gsk_bbb,gsk_ccc      # comma-separated
GROQ_API_KEY=gsk_aaa                       # and/or single keys
GROQ_API_KEY_2=gsk_bbb                     # ... up to GROQ_API_KEY_10
```

When a key hits its daily cap, it sits out for an hour. When it hits the per-minute cap,
it sits out for the few seconds Groq asks for. Either way, the next key takes the call
immediately. Keys from the **same** Groq account share one budget, so rotation only adds
headroom when each key comes from a different account. `GET /api/health` shows how many
keys are configured and which are available.

## Swapping the model

Set `MODEL` in `.env`; no code change is needed. Which models a key can reach varies by
account. List yours before picking one:

```bash
curl -H "authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models
```

| Model | Notes |
|---|---|
| `qwen/qwen3.8-27b` | Default in the code, and the one tested against. |
| `openai/gpt-oss-20b` | Fast; set in `.env.example`, so a fresh `.env` uses it until you change `MODEL`. |
| `openai/gpt-oss-120b` | Strongest, but uses up its daily budget fastest. |

Each model has its own daily budget, so if one is used up, switching `MODEL` works
straight away.

Tool-calling reliability varies more between open-weight models than chat quality does.
98 of the 135 items need required choices, so a weaker model means more refuse-and-retry
loops. You see that as slower replies, not wrong orders, because `cart.py` refuses
anything invalid whichever model is driving.

## Design notes

**The menu is not in the system prompt.** 135 items is about 20k tokens. That fits the
model's context easily but not the free tier's 7k-per-minute input cap, so the first
request always failed. The prompt carries a short category index instead, and the model
looks items up with a `search_menu` tool. Results are trimmed to what the model needs:
8 items with their choices, or up to 30 names and prices for a listing.

**The model never touches the cart directly.** It proposes an add; `cart.py` checks every
item, step and option against the menu, and refuses anything invented, incomplete or
borrowed from another step. Refusals go back to the model as structured data (the step it
missed, with its options), so it asks the customer instead of guessing again.

**The model chooses which items to show as tiles, but not what the tiles say.** It lists
item ids at the end of its reply. The server keeps only ids that a real search returned,
and builds each tile's name and price from the menu.

**Rules that must hold are enforced in code, not only in the prompt.** For example, the
model tended to answer "do you have starters?" from its category index without
searching, so there were no items to show. When a message names a category, the server
now requires a search first.

**98 of 135 items need required choices**, and in the client's data every one of those
steps is labelled "Please Select" / "Choose 1". `menu_mapping.json` gives them real labels
based on their options, so the assistant can ask "which sauce would you like?" rather than
"please select".

## Before this ships

**The cart is in-memory.** Nothing we were given includes a YOPOS cart API (no endpoint,
no payload shape), and the live site is not accessible. `CartBackend` in `server/cart.py`
is the place to connect it: implement it against the real API and pass it to `Cart`.
Nothing else changes.

**The menu has no nutrition data.** `item_kcal` is null on every item and there are no
ingredient lists. With `ALLOW_NUTRITION_INFERENCE=false` (the code default), the assistant
says it has no nutrition data. With `true`, it estimates calories and protein from item
names and labels them as estimates, but those figures are made up. Allergen questions are
refused either way. If dietary answers are really wanted, generate a nutrition file once
and have a person review it, rather than inferring at runtime.

**Sessions live in the server's memory**, so run a single instance. More than one worker
needs a shared store such as Redis; the change is limited to `server/sessions.py`.

**Set `ALLOWED_ORIGINS`** to the real site's origin. `*` is a development default.

## Not in scope

No payment, no order placement, no order status. The assistant stops at the cart and
hands off to the normal checkout.
