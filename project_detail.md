PROJECT OVERVIEW

This project is for a restaurant website called YOPOS. The goal is to integrate an AI-powered
chat assistant into the website that allows users to interact with the restaurant and add
food items to their cart directly through the chat.
my ai bot should be able to answer question like recommemnd me high protein item, low carb item.
we dont need to create enhanced-menu-json file for it, ai should be able to tell it at run time like
panner is high protein item so recommend that.
i will not get backend and frontend code.
---

AI ASSISTANT

A floating “Ask AI” button should be displayed in the bottom-right corner of the website.

When the user clicks the “Ask AI” button:

1. A chat panel should open on the right side of the screen.
2. The user can interact with the AI assistant through this panel.
3. The AI should understand the user's questions and requests related to the restaurant menu.
4. The AI should be able to recommend menu items and provide relevant information about them.
5. Most importantly, the AI should be able to add items to the user's cart directly from the
conversation.


---

STATUS (25 September 2026)

A working demo is built. Everything in the brief above works, with one limit: the cart
belongs to the demo, because no YOPOS cart API was provided.

What works today:

   - "Ask AI" button bottom-right; the chat panel opens on the right and pushes the page
     aside (full screen on mobile).
   - The AI answers menu questions, opening hours, address and delivery radius.
   - Items the AI offers appear as clickable tiles showing name, category and price.
     Clicking one picks it.
   - The AI adds items to the cart and asks the customer each required choice (sauce,
     salad, drink, bread). It never picks a choice for them.
   - It can view, remove from and clear the cart. It stops at the cart: no payment or
     order placement.
   - Every item and option the AI adds is checked against the real menu, so it cannot
     add anything that does not exist.
   - High-protein / low-carb questions: answered with estimates from item names, clearly
     marked as estimates (switchable off). Allergen questions are always refused.
   - Groq (model qwen/qwen3.8-27b), with 7 API keys rotated automatically when one hits
     its rate limit.
   - 100 automated tests.

Still needed from the client before this can go on the real website: the cart API
(question 1 below). Full technical write-up: PROJECT.md.

Each question below now has a STATUS line: RESOLVED (answered or built), ASSUMED (we
chose a default that the client should confirm) or OPEN.

---

QUESTIONS TO ASK GITESH SIR BEFORE STARTING

The brief defines the UI and the goal, but the key implementation details are missing —
especially the cart integration. Below are the questions that need answers, grouped so they
can be worked through in one conversation.

Questions 1 and 2 are blocking: nothing can be built until they are answered. The rest have
reasonable defaults I can choose myself if needed, but answers will avoid rework.

What I found in menu.json (for context during the discussion):

   - 137 items across 13 categories — small enough to fit in the AI prompt directly, so no
     search index or vector database is needed.
     UPDATE: it fit the model's memory but not Groq's free-tier limit of 7,000 input tokens
     per minute, so the AI now looks items up with a search tool. Still no vector database.
   - 98 items (72%) have at least one REQUIRED customisation step. 204 steps in total.
   - All 204 steps are labelled "Please Select" with the subtitle "Choose 1" — there are no
     meaningful names anywhere in the data.
   - 735 option rows, but only 16 distinct option names (Chilli Sauce, Mayo, Tomato Sauce,
     BBQ Sauce, No Sauce, Salad, No Salad, various cans, Nan, 2x Roti). One is a typo:
     "BBQ Saucce".
   - Only 1 item has a description. None have calorie, allergen, or dietary data.
   - Only 1 item uses variations ("Burger King"), and only 1 option out of 735 costs extra.


1. BLOCKING — How does the AI actually add items to the cart?

   - Does the website have a working cart today? Everything in the brief assumes one exists.
   - Is there an existing cart API (endpoint + payload shape), or should the chatbot write
     directly into the website's frontend cart state?
   - What is the exact payload for a customised item — how do the item_step_id and
     item_step_option_id selections get submitted?
   - What is the payload to remove an item, update quantity, and clear the cart?
   - Is there existing documentation for these endpoints, or should I reverse-engineer them
     from the current website's network calls?

   STATUS: OPEN. No cart API was provided and the website is not accessible, so the demo
   keeps its own cart in memory. Connecting the real cart means writing one class
   (CartBackend in server/cart.py); all the validation in front of it stays as it is.


2. BLOCKING — How should the mandatory customisation flow work?

   98 of 137 items cannot be added to the cart without required choices.

   - If a user says "add a chicken doner wrap", should the AI ask each required question in
     chat across multiple turns, apply sensible defaults and let the user change them, or
     open the website's existing customisation modal?
   - Every step is labelled "Please Select" / "Choose 1", so the AI cannot tell a sauce
     question from a drink question except by reading the option values. Can the backend
     labels be corrected to real names (Sauce, Salad, Drink)?
   - If the labels cannot be fixed, I can hardcode a mapping — there are only 16 distinct
     option names, so this is workable as a fallback, but it will break whenever the menu
     changes. Is that acceptable as a short-term approach?

   STATUS: RESOLVED in the demo. The AI asks each required choice in chat, one reply at a
   time, and never picks a default. Step labels (Sauce / Salad / Drink / Bread choice)
   come from the option-name mapping in menu_mapping.json. A new option name falls back
   to "Please choose one of the following" rather than a wrong label. Fixing the labels
   in the client's backend would still be better.


3. Which AI provider and model, and who pays?

   - Which provider and model should we use?
   - Who supplies the API key — the client's account or ours?
   - Any data-residency or privacy constraints? Can the menu data and user messages be sent
     to a third-party AI API?
   - Is there a budget or cost ceiling, per conversation or per month?

   STATUS: PARTLY RESOLVED. Groq, model qwen/qwen3.8-27b, on free-tier accounts we
   supply (7 keys, rotated). Who pays in production, the privacy question and the budget
   are OPEN. Menu data and customer messages are sent to Groq.


4. Where does the menu come from at runtime?

   - Is menu.json a live API endpoint the chatbot should call, or a static export? If live,
     what is the URL and what authentication does it need?
   - How often does the menu change? This determines the caching strategy.
   - Should I filter items on status, web_enable, or deleted_at? All 137 items currently have
     status: 0 while web_enable: 1 — I need to know whether status: 0 means "hidden" or is
     simply unused.

   STATUS: ASSUMED. The menu is treated as a static export: after a menu change, re-run
   clean_menu.py. Items are filtered on web_enable and deleted_at; status is ignored,
   because filtering on it leaves an empty menu. A live menu endpoint, if one exists, is
   OPEN.


5. Data quality — is this demo data, and who fixes it?

   - Is menu.json real YOP data or a demo export? The store is named "Demo Pizza", which
     suggests placeholder data.
   - Is the currency GBP, not INR? The country_currency field holds the rupee symbol, but the
     prices and the Birmingham UK address suggest pounds.
   - Two items are priced at 0 ("Water bottle" and "Burger King"). Real, or data errors? An AI
     that adds a zero-priced item to a cart looks broken.
   - Only 1 of 137 items has a description and none have calorie data. Will someone write
     descriptions and tags, or should the AI work from item names alone?

   STATUS: ASSUMED. Currency treated as GBP. Both £0 items are hidden from the AI. The
   "Chilly burger" priced £100 (filed under Garlic Breads) is shown with a "check price"
   tag. The AI works from item names alone. Whether this is demo data is still OPEN.


6. Recommendations — what should the AI actually recommend on?

   The brief says the AI should "recommend menu items and provide relevant information about
   them", but the data to support this does not exist yet.

   - Which axes should recommendations use — popular items, vegetarian/vegan, spice level,
     budget, meal pairings, chef specials?
   - Of these, only is_featured and is_chef_special exist in the data today. Everything else
     would need to be added. Who provides it?
   - Is there any order history or sales data available to define "popular"?

   STATUS: OPEN. is_chef_special is 1 on every item and is_featured is 0 on every item,
   so neither can drive recommendations. For now the AI suggests a few items from a
   search and offers to show more. Real recommendations need data from the client.


7. Allergens and dietary requirements — how must these be handled?

   - There is no allergen or ingredient data in the menu at all.
   - Is approved allergen information available, and who signs it off?
   - I do not want the AI inferring allergens or dietary suitability from item names, as this
     is a safety risk. I would like this ruled out explicitly in writing.
   - What disclaimer text is required, and where must it appear?

   STATUS: PARTLY RESOLVED. Allergen questions are always refused; that cannot be switched
   off. Nutrition (protein, carbs, calories) is behind a setting,
   ALLOW_NUTRITION_INFERENCE. Off: the AI says it has no nutrition data. On (current
   setting, as requested in this brief): it estimates from item names and says the
   figures are estimates, not to be relied on for medical or dietary decisions. Those
   figures are guesses. It should be off before real customers use it, unless someone
   supplies checked nutrition data. Note: the brief's "paneer" example is not on this
   menu. Disclaimer wording and sign-off are OPEN.


8. Scope boundaries — what should the AI NOT do?

   Needs an explicit list, or scope will creep.

   - Only menu and cart? Or also opening hours, delivery areas, order tracking, order status,
     past orders, offers, refunds, complaints, restaurant policies?
   - Can it remove or modify items already in the cart, or only add?
   - Can it place the order and take payment, or does it stop at the cart and hand off to the
     normal checkout flow?
   - What should it do with off-topic questions?

   STATUS: ASSUMED. Menu, store information (hours, address, delivery radius) and cart
   only. It can add, remove, view and clear. It stops at the cart and hands over to the
   normal checkout. For off-topic or out-of-scope requests, it says plainly that it
   can't help.


9. Failure and edge-case behaviour

   - What should happen when an item is unavailable or out of stock?
   - What should happen when the restaurant is closed? (start_time and end_time are both null
     in the store data, so opening hours are not currently available.)
   - What should happen when a required option is missing, or the user refuses to choose?
   - What should happen when the AI API is slow, errors, or hits a rate limit?
   - What should happen if the user is outside the delivery radius?

   STATUS: PARTLY RESOLVED. Missing required choice: the cart refuses the item and the AI
   asks the customer. Rate limits: switches to the next API key; if all are exhausted,
   the customer sees "The assistant is unavailable right now" (the real reason is in the
   server log). Opening hours: start_time/end_time are null, but the
   real per-day hours were found elsewhere in the export and are used (the storefront
   shows open/closed). Out of stock and delivery-radius checks: OPEN, as there is no data
   for them.


10. Tech stack and integration

   - What is the existing website built in (React, Next.js, Vue, WordPress, PHP), and can I
     get repository access?
   - Should the chat be a component inside the existing application, or an embeddable
     widget/iframe?
   - Where should the AI call live — a backend endpoint I build, or client-side? I recommend
     backend, because the API key must not be exposed in the browser.
   - Is there a Figma or design reference for the floating button and right-side chat panel,
     including mobile behaviour, colours and branding? Or should I design it?

   STATUS: RESOLVED for the demo. The chat is an embeddable widget: one <script> tag, no
   framework, so it works whatever the site is built in. The AI call and API keys stay on
   our backend (FastAPI), never in the browser. The design is our own. The site's stack,
   repository access and any brand guidelines are OPEN.


11. Sessions, auth, history and privacy

   - Is the user logged in when using the chat? Does the AI need to know who they are?
   - Should the chat and cart persist after refresh, after login, and across devices?
   - Do chat logs need to be stored for analytics or quality review, and for how long?
   - Any privacy, consent, or logging requirements for chat messages?

   STATUS: ASSUMED. No login. Each visitor gets an anonymous session id stored in their
   browser. The conversation and cart are kept on the server for 1 hour and survive a page
   refresh, but not a server restart. Chat logs are not stored. Limit: 20 messages per
   minute per visitor. Requirements are OPEN.


12. Non-functional requirements

   - English only, or multiple languages? There is a language field in the store data.
   - Mobile behaviour — should the panel go full-screen on small screens?
   - Any accessibility requirements to meet?
   - Expected traffic — how many concurrent users, and roughly how many orders per day? This
     affects whether to optimise for cost or for response quality.
   - Should responses stream in word by word, or appear only when the full reply is ready?

   STATUS: ASSUMED. English only. The panel goes full screen below 900px wide. Replies
   appear when complete (no streaming). Basic accessibility: keyboard focus, labelled
   buttons, Escape to close. No formal accessibility audit has been done. Traffic figures
   are OPEN.


13. Delivery, acceptance and future scope

   - What is the deadline, and are there phased milestones?
   - Is there a staging environment with a working cart I can test against?
   - What are the acceptance criteria and launch test cases? For example: "add a customised
     kebab from natural-language chat and show the correct cart total."
   - Is there a test script of example conversations the bot must handle correctly?
   - Who signs off on the final delivery?
   - Are voice input or a multilingual version planned for a later phase? This affects
     architectural decisions I need to make now.

   STATUS: OPEN. There is no agreed test script yet. The deadline, a staging cart,
   acceptance criteria and sign-off all still need answers.

---
                                                                                                           