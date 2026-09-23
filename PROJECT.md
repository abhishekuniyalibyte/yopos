# YOPOS AI Ordering Assistant — Project Report

**Status:** working demo, September 2026
**Stack:** FastAPI (Python 3.12) · Groq (`qwen/qwen3.8-27b`) · vanilla JS widget
**Tests:** 87 passing

---

## 1. The brief

Integrate an AI chat assistant into the YOPOS restaurant site. A floating "Ask AI" button
opens a panel on the right; customers ask about the menu, get recommendations, and add
items to their cart through conversation.

Three constraints shaped everything that follows, and all three arrived after work began:

- **`menu.json` is the only artefact the client will provide.** No API docs, no schema,
  no follow-up questions possible.
- **No access to the site's frontend or backend code.**
- **A demo is wanted, not a production integration.**

The third constraint resolved what had been the blocking question — how the assistant
writes to a real cart — by removing it. The demo owns its own cart.

---

## 2. What the client's data actually contains

The export is 2.3 MB. Most of it is audit metadata: `created_at`/`updated_at` on every
row, and a `map_Item_Category` block repeating the same category object once per
historical edit (406 rows for 137 items, most soft-deleted).

What survives cleaning:

| | |
|---|---|
| Items | 137 (135 after exclusions) |
| Categories | 13 |
| Items needing required choices | 98 |
| Customisation steps | 204 |
| Option rows | 735 — but only **16 distinct** option names |
| Items with a description | **1** |
| Items with calorie data | **0** |

**Nothing in the export supports nutrition, allergen or dietary questions.** `item_kcal`
is null on all 137 items; there are no ingredients. This became the central design
question of the project (§4).

Recommendation data is equally thin: `is_chef_special` is `1` on every item and
`is_featured` is `0` on every item, so neither distinguishes anything.

### Data errors found

| Problem | Handling |
|---|---|
| `Chilly burger` priced **£100.00**, filed under Garlic Breads | Shown with a "check price" tag — visible, not hidden |
| `Water bottle` and `Burger King` priced **£0.00** | Excluded — a bot adding a free item looks broken |
| All 204 steps labelled "Please Select" / "Choose 1" | Real labels inferred from option values |
| One option name misspelled `BBQ Saucce` | Fixed in display text; original id preserved for the cart |
| `status: 0` on all items while `web_enable: 1` | Filter on `web_enable`; `status` appears unused |

---

## 3. What was built

### The cleaning step

`clean_menu.py` turns the 2.3 MB export into a 162 KB working file: **92.9% smaller**.
It filters on `web_enable`, drops the £0 items, resolves each item's live category, and
applies our own editorial decisions from `menu_mapping.json`.

Keeping that mapping file separate matters. It holds meal-time tags, step labels and
exclusions — judgments we made, not facts the client supplied. Anyone reading the project
can see exactly which is which, and the tags can be revised without regenerating anything.

### The backend

Seven modules, each with one job. The important boundary is `server/cart.py`: **the model
never touches the cart directly.** It proposes an add; the cart validates every item id,
step id and option against the menu and refuses anything invented, incomplete, or borrowed
from another step. Refusals return structured data — the step that was missed, with its
options — so the assistant asks the customer instead of guessing again.

That boundary is why a provider switch (Anthropic → Groq), a prompt rewrite, and three
rounds of rate-limit work all left the safety logic untouched.

### The frontend

`widget/widget.js` is the whole integration — one `<script>` tag, no framework, no build
step, no API key in the browser. It has to work regardless of what the real YOPOS site is
built in, which we cannot see.

`widget/index.html` is a demo storefront serving all 135 items with category filters and a
live open/closed badge computed from the store's real per-day hours.

---

## 4. The nutrition question

The brief asks the assistant to handle *"recommend me a high-protein item, low-carb
item"*, and specifies it should reason at runtime — *"paneer is high protein, so
recommend that."*

Two problems with that as written:

**There is no paneer on this menu.** Not one item across 137. The example was written
against a different restaurant's data, which is worth raising with whoever wrote the brief.

**There is no nutrition data at all**, so any such claim can only be inferred from an item
name. The model will say "high in protein" with identical confidence whether it is right
or wrong, and will not know that "Chicken Doner & Chips" comes with chips, or how much of
a "Mixed Doner" is meat. Customers often ask these questions for medical reasons.

### How it was resolved

A config flag, `ALLOW_NUTRITION_INFERENCE`, with both behaviours built:

**Off (default)** — the assistant declines and pivots to what is real:

> I don't have any protein or nutrition data for our menu, so I can't honestly recommend
> based on that — I'd suggest checking with the restaurant directly. What I *can* tell you
> is what's on the menu: we've got beef, chicken, fish and doner burgers, plus kebabs,
> curries and wraps.

**On** — the assistant estimates from names, and says so:

> For a high-protein pick, I'd point you toward the grilled chicken options. Grilled
> Chicken Doner & Chips — £5.49. Chicken Balti — £6.99. These are estimates based on the
> names, not measured values, so don't rely on them for any medical or dietary decisions.

The flag is currently **on**, at the user's direction. Allergen questions are refused in
both modes; that is not configurable.

Flipping the flag and asking the same question twice is the clearest way to show a
stakeholder what the data does and does not support.

**If the feature is wanted properly**, the route is a reviewed nutrition file — an LLM
pass over 135 items, checked by a person, stored as a real field — not runtime inference.
Stored values are consistent between answers and can be pointed at; inferred ones are
neither.

---

## 5. Problems solved along the way

### The menu did not fit — for the wrong reason

Early analysis concluded the menu should go in the system prompt: 135 items is ~20k
tokens, comfortable in any modern context window, and retrieval over a corpus that small
would show the model only part of the menu.

That reasoning was right about context size and **wrong about throughput**. Groq's free
tier caps *input tokens per minute* at 7,000. A 20k-token prompt failed on the first
request, every time, regardless of the model's 131k window.

The fix inverted the design: the prompt now carries a ~900-token category index, and the
model retrieves detail through `search_menu` — the retrieval step originally ruled out.

### Tool results were the real cost

Each result becomes input tokens on the *next* request. Returning 25 full items cost
~5,460 tokens per hop, so two hops could not fit in one minute and no retry could help.
Trimmed to 8 items with only the fields the model needs: **~293 tokens**.

### The assistant invented customisation options

Trimming went too far. Search results carried step *labels* ("Sauce choice") but not the
option names, so when the model asked the customer what they wanted — before ever
attempting an add — it filled the gap with what a takeaway usually has:

> Salad: lettuce, tomato, onion · Sauce: ketchup, mayo, garlic · Drink: cola, water

None of those exist on this menu. The cart would have refused them, so no invalid order
was possible — but customers were being offered food that does not exist.

Fixed by including real option names (~607 tokens/hop, still well inside budget), plus an
explicit prompt rule: *never offer anything that did not come back from a tool.*

### Adding an item took four API calls

`search_menu` returned option *names*; `add_to_cart` required option *ids*. So the model
had to submit a doomed add, read the ids out of the refusal, and retry — once per step.
A three-choice meal cost four round-trips, which is what exhausted the rate limits.

Choices can now be given by name (`{"step": "Sauce choice", "option": "BBQ Sauce"}`),
matched case- and space-insensitively against the real options. **Four calls became one.**

### Rate limits, twice

Groq returns 429 for two unrelated conditions: a daily token cap (200k per model, per
account) and a per-minute input ceiling (7k). They need opposite responses — the daily cap
is unrecoverable by waiting; the per-minute one clears in seconds.

The first implementation collapsed both into "rate limited, try again shortly", which sent
debugging down the wrong path entirely. Now the provider's own message is always
surfaced, and the two cases branch properly.

Key rotation across multiple Groq accounts handles both: a daily-capped key stands down
for an hour, a throttled key for the seconds Groq specifies, and the next key takes the
call immediately. The four keys in use were confirmed to be separate accounts — their
token budgets move independently — so rotation gives roughly 4× headroom.

---

## 6. Decisions and their reasons

| Decision | Why |
|---|---|
| No vector DB / RAG index | 135 items. The binding constraint was per-minute throughput, not corpus size — solved by trimming, not by embedding. |
| Model never touches the cart | Validation in one module survives provider swaps, prompt rewrites and model changes. |
| Editorial mapping kept in its own file | Keeps our judgments separable from client data, and revisable without regenerating. |
| Vanilla JS widget, no framework | The real site's stack is unknown and inaccessible. |
| Key in the backend, never the browser | The one non-negotiable of the production shape, kept even in the demo. |
| Data errors surfaced, not hidden | The £100 burger is visible on the storefront so it can be raised with whoever owns the data. |

---

## 7. What is not done

**The cart is in-memory.** No YOPOS cart API exists in anything provided, and the site is
inaccessible. `CartBackend` in `server/cart.py` is the seam — implement it against the real
API and nothing else changes. This is the one piece that genuinely requires client input.

**Sessions are in-process**, so this runs single-instance. More than one worker needs
Redis; the change is contained to `server/sessions.py`.

**No payment, order placement or order tracking.** The assistant stops at the cart.

**The live Groq path has no automated test.** Everything else is covered by the 87 tests,
which run without an API key.

---

## 8. Open questions for the client

Most of the original question list was resolved by the demo scope. These remain:

1. **Cart API** — endpoint and payload shape, whenever this moves past demo.
2. **The paneer example** in the brief does not exist on this menu. Was the brief written
   against different data?
3. **Nutrition data** — if dietary recommendations are genuinely wanted, someone must
   supply or sign off real values. Inference is a demo behaviour, not a shippable one.
4. **Data errors** — the £100 `Chilly burger`, the two £0 items, and the 204 steps all
   labelled "Please Select" are fixable at source and would improve the assistant
   immediately.
5. **Currency** — `country_currency` holds a rupee symbol; the address and prices are
   clearly GBP. We assume GBP.
