# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

An AI ordering assistant for a UK takeaway (YOPOS / "Demo Pizza", Birmingham). A chat
widget embeds in the restaurant's site, answers questions about the menu, and builds a
cart. Backend is FastAPI; the model is served by Groq.

```
menu.json ──clean_menu.py──> menu_clean.json ──> server/ <── widget/
(client export)              (135 items)         FastAPI      one <script> tag
```

## Commands

```bash
source .venv/bin/activate
uvicorn server.app:app --reload     # serves API + storefront at localhost:8000
pytest -q                            # 100 tests, no API key needed
python3 clean_menu.py                # regenerate menu_clean.json from menu.json
```

## Hard rules

**Never let the assistant state nutrition or allergen facts as truth.** The client's menu
has no calorie, protein, carb, ingredient or allergen data — `item_kcal` is null on all
137 items. `ALLOW_NUTRITION_INFERENCE=true` lets the model estimate from item names, and
everything it says in that mode is fabricated. Allergen questions are refused in *both*
modes; keep it that way. Customers ask these questions for medical reasons.

**Never let the model reach the cart directly.** It proposes; `server/cart.py` validates
every item, step and option against the menu and refuses anything invented, incomplete,
or borrowed from another step. If you add a tool that mutates state, validate it there
too — not in the prompt, not in the tool wrapper.

**Never invent menu content.** The assistant may only name items and options that came
back from `search_menu`. This has been broken twice (see "Bugs worth not repeating"), so
treat any change to search results or the prompt as touching a load-bearing rule.

**Never commit `.env`.** It holds live Groq keys, and `.gitignore` covers it. Nothing else
is ignored: `menu.json` (the client's full export) and `project_detail.md` (the brief and
internal notes) are committed on purpose. The GitHub repo is **public**, so everything
committed is published — don't add anything to a tracked file that shouldn't be public.

## Architecture

| File | Responsibility |
|---|---|
| `clean_menu.py` | 2.3 MB client export → 135-item clean JSON. Never edit the output by hand. |
| `menu_mapping.json` | **Our** editorial decisions: meal-time tags, step labels, exclusions. Kept separate from client data on purpose. |
| `server/menu.py` | Loads the menu. The source of truth every id is checked against. `category_in` detects a category named in a message. |
| `server/cart.py` | Cart state **and all validation**. The layer between model output and an order. |
| `server/tools.py` | Tool schemas (OpenAI function-calling format) + dispatcher. |
| `server/prompts.py` | System prompt, including the nutrition rules and output formatting. |
| `server/agent.py` | Groq call, tool loop, rate-limit handling, and turning the reply into item tiles. |
| `server/keys.py` | API key rotation across multiple Groq accounts. |
| `server/sessions.py` | Per-visitor history and cart, TTL'd. In-process — single instance only. |
| `server/app.py` | FastAPI routes. |
| `widget/widget.js` | Embeddable frontend. No framework, no build step, no API key. |
| `widget/index.html` | Demo storefront, served at `/`. |
| `PROJECT.md` | Project report for stakeholders. |
| `project_detail.md` | The client brief, the pre-build question list and each question's status. |

## Constraints that shaped the design

**The menu is NOT in the system prompt.** It was, and it broke. 135 items is ~20k tokens,
which fits any modern context window — but Groq's free tier caps *input tokens per
minute* at 7,000, so a full-menu prompt failed on the very first request. The prompt now
carries a ~900-token category index and the model retrieves detail via `search_menu`.
Context size was never the binding constraint; throughput was.

**Tool results must stay small.** Every result becomes input tokens on the next hop,
against that same 7k/minute ceiling. `SEARCH_LIMIT = 8` and results are trimmed to
`item_id`, `name`, `price` and option *names*. There is a test asserting search results
stay under ~600 tokens — if it fails, do not raise the threshold without understanding
why the limit exists.

**Listing uses a lighter mode, and paging is capped.** `detail="names"` drops choices and
returns up to `NAMES_LIMIT = 30` items (id, name, price) — enough for the largest category,
Burgers (27), in one call. 40 was measured and did not fit under 600 tokens. When more
than 30 match, names mode returns a per-category breakdown instead of items. Decide that
by match *count*: an earlier guard keyed on "was a filter passed?" was bypassed by
`max_price=1000`, `query=""` and `meal_time="dinner"`, which all match the whole menu.
Full mode returns `next_offset`, and `run_tool` allows **one** page with `offset > 0` per
customer message (`MAX_EXTRA_PAGES_PER_TURN`, state passed in by the agent) — paging all
135 in one turn measured ~17k input tokens against the 7k/minute cap, and a prompt rule
alone cannot guarantee the model won't. Once the customer picks an item,
`search_menu(item_id=...)` fetches it with its choices.

**Choices are matched by name, not id.** `search_menu` returns option names but not ids.
Requiring ids forced the model to submit a doomed `add_to_cart`, read the ids out of the
refusal, and retry — one wasted round-trip per step. Both forms work now; prefer names.

**Rate limits come in two flavours and need different handling.** Daily cap (TPD) retires
a key for an hour; per-minute (ITPM) stands it down for the few seconds Groq names. Both
rotate to the next key. Never collapse these into one branch.

**Always surface the provider's own error text.** Two separate debugging dead-ends came
from handlers that replaced Groq's message with something generic — a 404 hidden behind
"could not process that request", and a daily cap hidden behind "rate limited, try again".

## Output formatting

The chat widget renders replies as **plain text**. Markdown is not parsed, so `**bold**`
and `- bullets` appear as literal characters. `server/prompts.py` instructs the model to
write in prose. If you change the widget to render markdown, relax that section — and if
you change that section, check the widget still displays sensibly.

## Item tiles

Items the assistant offers render as clickable tiles under its reply (name, category,
price, an "Options" tag if the item has required choices). Clicking one sends "I'd like
the <name>" as the customer's next message.

- **The model picks which items; the menu says what they are.** The model ends a reply
  with `ITEMS: id, id`. `Assistant._tiles` in `server/agent.py` strips the line, drops any
  id `search_menu` has not returned in this conversation, and builds every tile from
  `Menu`. Never render a name or price from model text.
- **Earlier turns' searches count.** "Show me the names" is usually answered without a
  fresh search. Scoping ids to the current turn produced zero tiles on exactly that turn.
- **Fallbacks, in order:** the `ITEMS` line; searched items the reply names verbatim
  (longest name first, so "Double Cheeseburger Meal" doesn't also match "Double
  Cheeseburger"); this turn's `detail="names"` listing.
- **Category questions force a search.** The model answers "do you have starters?" from
  the category index in the prompt — a count, no items — whatever the prompt says. When a
  message names exactly one category (`Menu.category_in`), the first hop sends
  `tool_choice=search_menu`. Later hops are `auto`.
- **The text doesn't repeat the tiles.** Sentences naming 3+ tiled items are cut; a
  lead-in before a colon and any question are kept.
- **No tiles on a turn that changed the cart.** That reply is a confirmation, and the cart
  panel already shows the items.

## Bugs worth not repeating

1. **Invented customisation options.** Search results carried step *labels* but not option
   names, so the model offered customers "ketchup, lettuce, onion" — none on this menu.
   Fixed by including real option names. Tests cover it.
2. **Menu inlined in the prompt.** See "Constraints" above. Cost every request a 429.
3. **Stale provider references after a rewrite.** Switching Anthropic → Groq left three
   `ANTHROPIC_API_KEY` references in `app.py` that crashed startup, because tests never
   exercised the lifespan path. Grep the whole tree after a provider change.
4. **Scratch venv masking a real failure.** `pytest` passed in a throwaway venv and failed
   for the user because the project root was on `sys.path` there but not here. `pytest.ini`
   now sets `pythonpath = .`. Verify in the project's own environment.
5. **A stale widget in the browser.** The server was returning tiles and the user saw none:
   `widget.js` went out with no `Cache-Control`, so Chrome kept running the old copy. `/`
   and `/widget.js` are now served `no-cache`. When a widget change "doesn't work", check
   the API response with curl before touching the code.
6. **Trusting the prompt to make the model search.** "Always call search_menu" and "never
   answer with just a count" were both in the prompt and both ignored for category
   questions. If a behaviour must happen, enforce it in code (`tool_choice`, `run_tool`
   limits) — the prompt is a request, not a guarantee.

## Known data problems in the client's export

These are real errors in `menu.json`, not bugs to fix in code:

- **`Chilly burger` priced £100.00** (id 3291), and filed under Garlic Breads.
  Surfaced on the storefront with a "check price" tag rather than hidden.
- **Two £0.00 items** — `Water bottle` (3293) and `Burger King` (3356) — excluded via
  `menu_mapping.json`. A bot adding a free item to a cart looks broken.
- **All 204 customisation steps are labelled "Please Select" / "Choose 1".** Real labels
  are inferred from option values in `menu_mapping.json`.
- **`is_chef_special` is 1 on all 137 items; `is_featured` is 0 on all 137.** Neither can
  support recommendations.
- **`status` is 0 on all items while `web_enable` is 1.** We filter on `web_enable` and
  ignore `status`, since filtering on `status` yields an empty menu.

## Not in scope

No payment, no order placement, no order status. The assistant stops at the cart.

**The cart is in-memory.** There is no YOPOS cart API in anything the client provided, and
the live site is not accessible. `CartBackend` in `server/cart.py` is the seam: implement
it against the real API and pass it to `Cart`. Nothing else changes — the validation in
front of it is already correct and backend-independent.
