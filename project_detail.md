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

QUESTIONS TO ASK GITESH SIR BEFORE STARTING

The brief defines the UI and the goal, but the key implementation details are missing —
especially the cart integration. Below are the questions that need answers, grouped so they
can be worked through in one conversation.

Questions 1 and 2 are blocking: nothing can be built until they are answered. The rest have
reasonable defaults I can choose myself if needed, but answers will avoid rework.

What I found in menu.json (for context during the discussion):

   - 137 items across 13 categories — small enough to fit in the AI prompt directly, so no
     search index or vector database is needed.
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


3. Which AI provider and model, and who pays?

   - Which provider and model should we use?
   - Who supplies the API key — the client's account or ours?
   - Any data-residency or privacy constraints? Can the menu data and user messages be sent
     to a third-party AI API?
   - Is there a budget or cost ceiling, per conversation or per month?


4. Where does the menu come from at runtime?

   - Is menu.json a live API endpoint the chatbot should call, or a static export? If live,
     what is the URL and what authentication does it need?
   - How often does the menu change? This determines the caching strategy.
   - Should I filter items on status, web_enable, or deleted_at? All 137 items currently have
     status: 0 while web_enable: 1 — I need to know whether status: 0 means "hidden" or is
     simply unused.


5. Data quality — is this demo data, and who fixes it?

   - Is menu.json real YOP data or a demo export? The store is named "Demo Pizza", which
     suggests placeholder data.
   - Is the currency GBP, not INR? The country_currency field holds the rupee symbol, but the
     prices and the Birmingham UK address suggest pounds.
   - Two items are priced at 0 ("Water bottle" and "Burger King"). Real, or data errors? An AI
     that adds a zero-priced item to a cart looks broken.
   - Only 1 of 137 items has a description and none have calorie data. Will someone write
     descriptions and tags, or should the AI work from item names alone?


6. Recommendations — what should the AI actually recommend on?

   The brief says the AI should "recommend menu items and provide relevant information about
   them", but the data to support this does not exist yet.

   - Which axes should recommendations use — popular items, vegetarian/vegan, spice level,
     budget, meal pairings, chef specials?
   - Of these, only is_featured and is_chef_special exist in the data today. Everything else
     would need to be added. Who provides it?
   - Is there any order history or sales data available to define "popular"?


7. Allergens and dietary requirements — how must these be handled?

   - There is no allergen or ingredient data in the menu at all.
   - Is approved allergen information available, and who signs it off?
   - I do not want the AI inferring allergens or dietary suitability from item names, as this
     is a safety risk. I would like this ruled out explicitly in writing.
   - What disclaimer text is required, and where must it appear?


8. Scope boundaries — what should the AI NOT do?

   Needs an explicit list, or scope will creep.

   - Only menu and cart? Or also opening hours, delivery areas, order tracking, order status,
     past orders, offers, refunds, complaints, restaurant policies?
   - Can it remove or modify items already in the cart, or only add?
   - Can it place the order and take payment, or does it stop at the cart and hand off to the
     normal checkout flow?
   - What should it do with off-topic questions?


9. Failure and edge-case behaviour

   - What should happen when an item is unavailable or out of stock?
   - What should happen when the restaurant is closed? (start_time and end_time are both null
     in the store data, so opening hours are not currently available.)
   - What should happen when a required option is missing, or the user refuses to choose?
   - What should happen when the AI API is slow, errors, or hits a rate limit?
   - What should happen if the user is outside the delivery radius?


10. Tech stack and integration

   - What is the existing website built in (React, Next.js, Vue, WordPress, PHP), and can I
     get repository access?
   - Should the chat be a component inside the existing application, or an embeddable
     widget/iframe?
   - Where should the AI call live — a backend endpoint I build, or client-side? I recommend
     backend, because the API key must not be exposed in the browser.
   - Is there a Figma or design reference for the floating button and right-side chat panel,
     including mobile behaviour, colours and branding? Or should I design it?


11. Sessions, auth, history and privacy

   - Is the user logged in when using the chat? Does the AI need to know who they are?
   - Should the chat and cart persist after refresh, after login, and across devices?
   - Do chat logs need to be stored for analytics or quality review, and for how long?
   - Any privacy, consent, or logging requirements for chat messages?


12. Non-functional requirements

   - English only, or multiple languages? There is a language field in the store data.
   - Mobile behaviour — should the panel go full-screen on small screens?
   - Any accessibility requirements to meet?
   - Expected traffic — how many concurrent users, and roughly how many orders per day? This
     affects whether to optimise for cost or for response quality.
   - Should responses stream in word by word, or appear only when the full reply is ready?


13. Delivery, acceptance and future scope

   - What is the deadline, and are there phased milestones?
   - Is there a staging environment with a working cart I can test against?
   - What are the acceptance criteria and launch test cases? For example: "add a customised
     kebab from natural-language chat and show the correct cart total."
   - Is there a test script of example conversations the bot must handle correctly?
   - Who signs off on the final delivery?
   - Are voice input or a multilingual version planned for a later phase? This affects
     architectural decisions I need to make now.

---
                                                                                                           