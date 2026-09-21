/**
 * YOPOS AI ordering assistant — embeddable widget.
 *
 * Add to any page with one tag:
 *
 *   <script src="https://your-host/widget.js" data-api="https://your-host" defer></script>
 *
 * No framework, no build step, no dependencies — it has to work regardless of what the
 * host site is built in. Everything is namespaced and injected into a single container,
 * so it cannot collide with the host page's styles or globals.
 *
 * There is no API key here. The widget only ever talks to the backend, which holds the
 * key server-side.
 */
(function () {
  "use strict";

  if (window.__yoposWidgetLoaded) return;
  window.__yoposWidgetLoaded = true;

  var script = document.currentScript;
  var API = (script && script.dataset.api) || window.location.origin;
  var TITLE = (script && script.dataset.title) || "Ask AI";
  var SESSION_KEY = "yopos_session_id";
  var PANEL_W = 400;   // keep in sync with .yop-panel width in CSS
  var PUSH_MIN = 900;  // below this the panel covers the page instead of pushing it

  // Squeezing the host page only works if its layout follows <body>'s width. A site
  // with its own position:fixed chrome would not move, so this is opt-out.
  var PUSH = !(script && script.dataset.push === "off");

  var sessionId = null;
  try {
    sessionId = localStorage.getItem(SESSION_KEY);
  } catch (e) {
    /* private browsing: fall back to a per-page session */
  }

  var busy = false;

  /* ----------------------------------------------------------------- styles */
  var CSS = [
    ".yop-btn{position:fixed;right:22px;bottom:22px;z-index:2147483000;background:#c8452f;color:#fff;",
    "border:0;border-radius:999px;padding:13px 21px;font:600 15px system-ui,-apple-system,sans-serif;",
    "cursor:pointer;box-shadow:0 5px 18px rgba(0,0,0,.2)}",
    ".yop-btn:hover{background:#b23b28}",
    ".yop-btn[hidden]{display:none}",
    ".yop-panel{position:fixed;top:0;right:0;z-index:2147483001;width:" + PANEL_W + "px;max-width:100vw;height:100vh;",
    "background:#fff;border-left:1px solid #e2e2dd;display:none;flex-direction:column;",
    "font:15px/1.55 system-ui,-apple-system,sans-serif;color:#1c1c1a;box-shadow:-6px 0 26px rgba(0,0,0,.12)}",
    ".yop-panel.yop-open{display:flex}",
    /* The host page is squeezed, not covered: body keeps its box, just narrower.
       Qualified with html so it outranks the host's own body{margin:0} no matter
       which stylesheet the browser parses first. */
    "html body.yop-pushed{margin-right:" + PANEL_W + "px;transition:margin-right .22s ease}",
    "@media(prefers-reduced-motion:reduce){html body.yop-pushed{transition:none}}",
    "@media(max-width:" + (PUSH_MIN - 1) + "px){html body.yop-pushed{margin-right:0}}",
    ".yop-head{padding:13px 15px;border-bottom:1px solid #e2e2dd;display:flex;align-items:center;gap:10px}",
    ".yop-head .yop-t{font-weight:600;flex:1}",
    ".yop-head button{background:none;border:0;font-size:21px;line-height:1;cursor:pointer;color:#6b6b66}",
    ".yop-log{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:15px;display:flex;flex-direction:column;gap:11px}",
    ".yop-msg{max-width:86%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}",
    ".yop-bot{background:#f0f0ec;align-self:flex-start;border-bottom-left-radius:3px}",
    ".yop-me{background:#c8452f;color:#fff;align-self:flex-end;border-bottom-right-radius:3px}",
    ".yop-err{background:#fdecea;color:#8d2b1c;align-self:flex-start;font-size:13px}",
    ".yop-typing{align-self:flex-start;color:#6b6b66;font-size:13px}",
    ".yop-cart{border-top:1px solid #e2e2dd;padding:10px 15px;font-size:13px;background:#fafaf8;max-height:34vh;overflow-y:auto;overscroll-behavior:contain;flex-shrink:0}",
    ".yop-ln{display:flex;justify-content:space-between;gap:10px;padding:2px 0}",
    ".yop-sub{color:#6b6b66}",
    ".yop-tot{display:flex;justify-content:space-between;font-weight:600;border-top:1px solid #e2e2dd;margin-top:6px;padding-top:6px}",
    ".yop-form{display:flex;gap:8px;padding:11px 15px;border-top:1px solid #e2e2dd}",
    ".yop-form input{flex:1;padding:10px 12px;border:1px solid #e2e2dd;border-radius:9px;font:inherit;color:inherit}",
    ".yop-form button{background:#c8452f;color:#fff;border:0;border-radius:9px;padding:0 16px;font:600 15px inherit;cursor:pointer}",
    ".yop-form button:disabled{opacity:.5;cursor:default}",
    "@media(max-width:" + (PUSH_MIN - 1) + "px){.yop-panel{width:100vw}}"
  ].join("");

  /* ------------------------------------------------------------------- dom */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  var style = el("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  var button = el("button", "yop-btn", TITLE);
  var panel = el("div", "yop-panel");

  var head = el("div", "yop-head");
  var closeBtn = el("button", null, "×");
  closeBtn.setAttribute("aria-label", "Close");
  head.appendChild(el("span", "yop-t", TITLE));
  head.appendChild(closeBtn);

  var log = el("div", "yop-log");
  var cartBox = el("div", "yop-cart");

  var form = el("form", "yop-form");
  var input = el("input");
  input.placeholder = "What are you after?";
  input.autocomplete = "off";
  var send = el("button", null, "Send");
  send.type = "submit";
  form.appendChild(input);
  form.appendChild(send);

  panel.appendChild(head);
  panel.appendChild(log);
  panel.appendChild(cartBox);
  panel.appendChild(form);
  document.body.appendChild(button);
  document.body.appendChild(panel);

  /* --------------------------------------------------------------- render */
  function say(kind, text) {
    var node = el("div", "yop-msg " + kind, text);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  function money(n) {
    return "£" + Number(n).toFixed(2);
  }

  function renderCart(cart) {
    cartBox.textContent = "";
    if (!cart || !cart.lines || !cart.lines.length) {
      cartBox.appendChild(el("div", "yop-sub", "Cart is empty"));
      return;
    }
    cart.lines.forEach(function (line) {
      var row = el("div", "yop-ln");
      var left = el("span");
      left.appendChild(document.createTextNode(line.quantity + "× " + line.name));
      if (line.choices && line.choices.length) {
        left.appendChild(el("br"));
        left.appendChild(el("span", "yop-sub", line.choices.join(", ")));
      }
      row.appendChild(left);
      row.appendChild(el("span", null, money(line.line_total)));
      cartBox.appendChild(row);
    });
    var total = el("div", "yop-tot");
    total.appendChild(el("span", null, "Total"));
    total.appendChild(el("span", null, money(cart.total)));
    cartBox.appendChild(total);
  }

  /* ----------------------------------------------------------------- chat */
  function post(message) {
    return fetch(API.replace(/\/$/, "") + "/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: message, session_id: sessionId })
    }).then(function (res) {
      if (res.status === 429) throw new Error("You're sending messages too quickly — give it a moment.");
      if (res.status === 503) throw new Error("The assistant is unavailable right now.");
      if (!res.ok) throw new Error("Something went wrong (" + res.status + ").");
      return res.json();
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || busy) return;

    say("yop-me", text);
    input.value = "";
    busy = true;
    send.disabled = true;
    var typing = say("yop-typing", "…");

    post(text)
      .then(function (data) {
        typing.remove();
        sessionId = data.session_id;
        try {
          localStorage.setItem(SESSION_KEY, sessionId);
        } catch (e) {
          /* ignore */
        }
        say("yop-bot", data.reply);
        renderCart(data.cart);
      })
      .catch(function (err) {
        typing.remove();
        say("yop-err", err.message);
      })
      .finally(function () {
        busy = false;
        send.disabled = false;
        input.focus();
      });
  });

  /* ---------------------------------------------------------------- open */
  function setOpen(open) {
    panel.classList.toggle("yop-open", open);
    button.hidden = open;
    if (PUSH) document.body.classList.toggle("yop-pushed", open);
  }

  var greeted = false;
  button.addEventListener("click", function () {
    setOpen(true);
    input.focus();
    if (greeted) return;
    greeted = true;
    say("yop-bot", "Hi! I can help you find something to eat and add it to your cart. What are you after?");
    if (sessionId) {
      fetch(API.replace(/\/$/, "") + "/api/cart/" + sessionId)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (cart) { if (cart) renderCart(cart); })
        .catch(function () { /* cart is best-effort on open */ });
    } else {
      renderCart(null);
    }
  });

  closeBtn.addEventListener("click", function () {
    setOpen(false);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
})();
