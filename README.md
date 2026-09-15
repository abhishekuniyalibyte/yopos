# YOPOS AI ordering assistant

A chat assistant that answers questions about the menu and builds a cart, embeddable in
the YOPOS site with one script tag.

```
clean_menu.py  ->  menu_clean.json  ->  server/  <-  widget/widget.js
```

Runs on **Groq** (`openai/gpt-oss-120b` by default), via its OpenAI-compatible
Chat Completions API.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add your GROQ_API_KEY — https://console.groq.com/keys
python3 clean_menu.py         # regenerate menu_clean.json from the client's menu.json
uvicorn server.app:app --reload
```

Then open **http://localhost:8000** — a demo storefront rendering the real menu, with the
assistant in the bottom-right corner.

To put the assistant on any other page, that is the whole integration:

```html
<script src="http://localhost:8000/widget.js" data-api="http://localhost:8000" defer></script>
```

Tests: `pytest -q`

## Layout

| Path | What it does |
|---|---|
| `clean_menu.py` | Turns the client's 2.3 MB `menu.json` into `menu_clean.json` (~135 items) |
| `menu_mapping.json` | Our editorial decisions — meal-time tags, step labels, exclusions |
| `server/menu.py` | Loads the menu; the source of truth every id is checked against |
| `server/cart.py` | Cart state **and all validation** — the layer between the model and an order |
| `server/tools.py` | Tool schemas the model sees, plus the dispatcher |
| `server/prompts.py` | System prompt, including the nutrition rules |
| `server/agent.py` | The Groq call and the tool loop |
| `server/sessions.py` | Per-visitor history and cart, with a TTL |
| `server/app.py` | FastAPI routes |
| `widget/widget.js` | Embeddable frontend — no framework, no API key |
| `widget/index.html` | Demo storefront, served at `/`, for seeing the widget in context |

## Swapping the model

`MODEL` in `.env`, no code change. Groq serves open-weight models; these handle tool use:

Which models a key can reach varies by account. List yours before picking one:

```bash
curl -H "authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models
```

| Model | Notes |
|---|---|
| `openai/gpt-oss-20b` | Default. Fast, and calls tools correctly on this workload. |
| `openai/gpt-oss-120b` | Strongest, but burns its daily budget fastest. |
| `qwen/qwen3.8-27b` | Solid alternative; useful when another model's budget is spent. |

On Groq's free tier each model has its **own** 200k tokens-per-day budget. A 429 saying
`tokens per day (TPD)` means that one model is spent for the day — switch `MODEL` to
another rather than waiting. A 429 without `TPD` is the short per-minute throttle
(8k tokens/min) and does clear in seconds.

Tool-calling reliability varies more across open-weight models than chat quality does.
With 98 of 135 items needing required choices, a weaker model means more refuse-and-retry
loops — visible as latency, not as wrong orders, because `cart.py` refuses anything
invalid regardless of which model is driving.

## Two things to know before this ships

**The cart is in-memory.** There is no YOPOS cart API in anything we were given — no
endpoint, no payload shape — and the site is not accessible, so nothing here writes to a
real cart. `CartBackend` in `server/cart.py` is the seam: implement it against the real
API and pass it to `Cart`, and nothing else changes. The validation in front of it is
already correct and does not depend on where the cart lives.

**The menu has no nutrition data.** `item_kcal` is null on all 137 items and there are no
ingredient lists, so the assistant refuses calorie, protein, carb and allergen questions
and says why. `ALLOW_NUTRITION_INFERENCE=true` makes it estimate from item names instead;
everything it produces in that mode is fabricated, which is why it defaults off. If the
feature is genuinely wanted, the safe route is generating a nutrition file once and having
a person review it before it ships — not inferring at runtime, which is unreviewable and
inconsistent between answers.

## Design notes

**The whole menu goes in the system prompt.** 135 items is roughly 20k tokens, which fits
with room to spare. Retrieval over a corpus this small would be worse, not better: top-k
search would show the model part of the menu and let it answer "what burgers do you have?"
with a partial list.

**The model never touches the cart directly.** It proposes; `cart.py` validates every
item id, step id and option id against the menu and refuses anything invented, incomplete
or borrowed from another step. Refusals go back to the model as structured data — the
step it missed, with its options — so it asks the customer instead of guessing again.
Malformed tool arguments are handled the same way, since open-weight models occasionally
emit unparsable JSON.

**98 of 135 items need required choices** (sauce, salad, drink), and in the client's data
every one of those steps is labelled "Please Select" / "Choose 1". `menu_mapping.json`
maps them to real labels from their option values, which is what lets the assistant ask
"which sauce would you like?" instead of "please select".

## Not in scope

No payment, no order placement, no order status. The assistant stops at the cart and
hands off to the normal checkout.
# yopos
