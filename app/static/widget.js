/*!
 * AgentGrid web chat widget
 * Embed: <script src="https://YOUR-HOST/widget.js" data-key="pk_..." async></script>
 * Optional attributes: data-api="https://YOUR-HOST" data-open="true"
 * Self-contained: Shadow DOM for style isolation, no dependencies, XSS-safe rendering.
 */
(function () {
  "use strict";
  if (window.__agentgridWidget) return;
  window.__agentgridWidget = true;

  var script = document.currentScript || document.querySelector("script[data-key][src*='widget.js']");
  if (!script) return;
  var KEY = script.getAttribute("data-key");
  if (!KEY) { console.warn("[AgentGrid] data-key is missing"); return; }
  var API = (script.getAttribute("data-api") || new URL(script.src).origin).replace(/\/$/, "");
  var STORE = "agentgrid:" + KEY;

  function load() { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { return {}; } }
  function save(v) { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  var state = load();

  function utm() {
    var p = new URLSearchParams(location.search), m = { page_url: location.href.slice(0, 2000) };
    ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].forEach(function (k) {
      if (p.get(k)) m[k] = p.get(k).slice(0, 200);
    });
    if (document.referrer) m.referrer = document.referrer.slice(0, 2000);
    m.locale = (navigator.language || "").slice(0, 20);
    return m;
  }
  // Attribution is captured on the first page view and kept for the session.
  if (!state.meta) { state.meta = utm(); save(state); }

  var host = document.createElement("div");
  host.id = "agentgrid-widget";
  document.body.appendChild(host);
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;

  var CSS = [
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}",
    ".ag-launcher{position:fixed;bottom:20px;width:60px;height:60px;border-radius:50%;border:0;cursor:pointer;",
    "box-shadow:0 6px 24px rgba(0,0,0,.2);display:flex;align-items:center;justify-content:center;z-index:2147483000;",
    "transition:transform .15s}",
    ".ag-launcher:hover{transform:scale(1.06)}.ag-launcher svg{width:28px;height:28px;fill:#fff}",
    ".ag-badge{position:absolute;top:2px;right:2px;min-width:18px;height:18px;border-radius:9px;background:#ef4444;",
    "color:#fff;font-size:11px;display:none;align-items:center;justify-content:center;padding:0 5px}",
    ".ag-panel{position:fixed;bottom:92px;width:370px;max-width:calc(100vw - 24px);height:560px;",
    "max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 12px 48px rgba(0,0,0,.22);",
    "display:none;flex-direction:column;overflow:hidden;z-index:2147483000}",
    ".ag-panel.open{display:flex}",
    ".ag-head{padding:16px;color:#fff;display:flex;align-items:center;gap:10px}",
    ".ag-head img{width:34px;height:34px;border-radius:8px;object-fit:cover;background:#fff}",
    ".ag-title{font-weight:600;font-size:15px}.ag-sub{font-size:12px;opacity:.85;margin-top:2px}",
    ".ag-close{margin-left:auto;background:transparent;border:0;color:#fff;font-size:22px;cursor:pointer;line-height:1}",
    ".ag-body{flex:1;overflow-y:auto;padding:14px;background:#f7f7f9;display:flex;flex-direction:column;gap:8px}",
    ".ag-msg{max-width:82%;padding:9px 12px;border-radius:14px;font-size:14px;line-height:1.45;white-space:pre-wrap;",
    "word-wrap:break-word}",
    ".ag-user{align-self:flex-end;color:#fff;border-bottom-right-radius:4px}",
    ".ag-bot{align-self:flex-start;background:#fff;color:#111;border:1px solid #e5e7eb;border-bottom-left-radius:4px}",
    ".ag-human{align-self:flex-start;background:#ecfdf5;color:#064e3b;border:1px solid #a7f3d0}",
    ".ag-label{font-size:11px;color:#6b7280;margin:2px 4px}",
    ".ag-sys{align-self:center;font-size:12px;color:#6b7280;background:#eef0f3;padding:4px 10px;border-radius:10px}",
    ".ag-src{font-size:11px;color:#6b7280;margin-top:6px;border-top:1px solid #f0f0f0;padding-top:4px}",
    ".ag-qr{display:flex;flex-wrap:wrap;gap:6px}",
    ".ag-qr button{background:#fff;border:1px solid;border-radius:14px;padding:5px 11px;font-size:13px;cursor:pointer}",
    ".ag-typing{align-self:flex-start;color:#9ca3af;font-size:13px;padding:4px 8px;display:none}",
    ".ag-foot{display:flex;gap:8px;padding:10px;border-top:1px solid #eee;background:#fff}",
    ".ag-foot textarea{flex:1;resize:none;border:1px solid #d1d5db;border-radius:10px;padding:9px 10px;font-size:14px;",
    "height:40px;max-height:100px;outline:none}",
    ".ag-foot button{border:0;color:#fff;border-radius:10px;padding:0 14px;cursor:pointer;font-weight:600}",
    ".ag-foot button:disabled{opacity:.5;cursor:default}",
    ".ag-powered{text-align:center;font-size:11px;color:#9ca3af;padding:0 0 8px;background:#fff}",
    "@media (max-width:480px){.ag-panel{bottom:0;right:0!important;left:0!important;width:100vw;max-width:100vw;",
    "height:100vh;max-height:100vh;border-radius:0}}"
  ].join("");

  var ICON_CHAT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="#fff" stroke-width="2.5"/></svg>';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text; // never innerHTML for dynamic text
    return e;
  }

  var style = el("style"); style.textContent = CSS; root.appendChild(style);
  var launcher = el("button", "ag-launcher"); launcher.setAttribute("aria-label", "Open chat");
  launcher.innerHTML = ICON_CHAT; // static SVG only
  var badge = el("span", "ag-badge"); launcher.appendChild(badge);
  var panel = el("div", "ag-panel"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Chat");
  var head = el("div", "ag-head");
  var logo = el("img"); logo.alt = ""; logo.style.display = "none";
  var titles = el("div"); var title = el("div", "ag-title", "Chat with us"); var sub = el("div", "ag-sub", "");
  titles.appendChild(title); titles.appendChild(sub);
  var closeBtn = el("button", "ag-close", "×"); closeBtn.setAttribute("aria-label", "Close chat");
  head.appendChild(logo); head.appendChild(titles); head.appendChild(closeBtn);
  var body = el("div", "ag-body"); body.setAttribute("aria-live", "polite");
  var typing = el("div", "ag-typing", "Typing…");
  body.appendChild(typing); // messages are inserted above the typing indicator
  var foot = el("div", "ag-foot");
  var input = el("textarea"); input.placeholder = "Type your message…"; input.setAttribute("aria-label", "Message");
  input.maxLength = 2000;
  var send = el("button", null, "Send");
  foot.appendChild(input); foot.appendChild(send);
  var powered = el("div", "ag-powered", "Powered by AgentGrid");
  panel.appendChild(head); panel.appendChild(body); panel.appendChild(foot); panel.appendChild(powered);
  root.appendChild(launcher); root.appendChild(panel);

  var color = "#4F46E5", unread = 0, ws = null, wsReady = false, retry = 0, qrBox = null;

  function brand(cfg) {
    var b = cfg.branding || {};
    color = b.primary_color || color;
    var side = b.launcher_position === "left" ? "left" : "right";
    launcher.style[side] = "20px"; panel.style[side] = "20px";
    launcher.style.background = color; head.style.background = color; send.style.background = color;
    title.textContent = b.widget_title || cfg.tenant.name;
    sub.textContent = b.widget_subtitle || "";
    if (b.logo_url && /^(https?:)?\//.test(b.logo_url)) { logo.src = b.logo_url; logo.style.display = "block"; }
    if (b.hide_powered_by) powered.style.display = "none";
  }

  function scroll() { body.scrollTop = body.scrollHeight; }
  function clearQuick() { if (qrBox) { qrBox.remove(); qrBox = null; } }

  function addMessage(role, text, extra) {
    extra = extra || {};
    clearQuick();
    if (role === "human_agent") body.insertBefore(el("div", "ag-label", "Team member"), typing);
    var cls = role === "user" ? "ag-user" : role === "human_agent" ? "ag-human" : "ag-bot";
    var bubble = el("div", "ag-msg " + cls, text);
    if (role === "user") bubble.style.background = color;
    var cites = (extra.citations || []).map(function (c) { return c.title; }).filter(Boolean);
    if (cites.length) {
      var uniq = cites.filter(function (t, i) { return cites.indexOf(t) === i; });
      bubble.appendChild(el("div", "ag-src", "Sources: " + uniq.join(", ")));
    }
    body.insertBefore(bubble, typing);
    if (extra.quick_replies && extra.quick_replies.length) {
      qrBox = el("div", "ag-qr");
      extra.quick_replies.slice(0, 6).forEach(function (q) {
        var b = el("button", null, q); b.style.borderColor = color; b.style.color = color;
        b.addEventListener("click", function () { submit(q); });
        qrBox.appendChild(b);
      });
      body.insertBefore(qrBox, typing);
    }
    if (!panel.classList.contains("open") && role !== "user") {
      unread += 1; badge.textContent = String(unread); badge.style.display = "flex";
    }
    scroll();
  }

  function system(text) { body.insertBefore(el("div", "ag-sys", text), typing); scroll(); }
  function setTyping(on) { typing.style.display = on ? "block" : "none"; if (on) scroll(); }

  function onServerEvent(evt) {
    if (evt.type === "typing") { setTyping(true); return; }
    if (evt.type === "pong") return;
    setTyping(false);
    if (evt.type === "message") {
      addMessage(evt.role, evt.text, evt);
      if (evt.status === "handed_over" && !state.handedOver) {
        state.handedOver = true; save(state); system("You're now connected with our team");
      }
    } else if (evt.type === "status" && evt.status === "handed_over") {
      if (!state.handedOver) { state.handedOver = true; save(state); system("A team member will reply here shortly"); }
    } else if (evt.type === "error") { system(evt.message || "Something went wrong"); }
  }

  function connect() {
    if (!("WebSocket" in window) || !state.session) return;
    var url = API.replace(/^http/, "ws") + "/api/v1/widget/ws?key=" + encodeURIComponent(KEY) +
      "&session_id=" + encodeURIComponent(state.session.id) + "&token=" + encodeURIComponent(state.session.token);
    try { ws = new WebSocket(url); } catch (e) { return; }
    ws.onopen = function () { wsReady = true; retry = 0; };
    ws.onmessage = function (m) { try { onServerEvent(JSON.parse(m.data)); } catch (e) { /* ignore */ } };
    ws.onclose = function (e) {
      wsReady = false;
      if (e.code === 4401) return; // invalid session: HTTP fallback still works
      retry = Math.min(retry + 1, 6);
      setTimeout(connect, Math.pow(2, retry) * 500); // exponential backoff
    };
  }
  setInterval(function () { if (wsReady) ws.send(JSON.stringify({ type: "ping" })); }, 25000);

  function http(method, path, payload) {
    return fetch(API + path, {
      method: method, headers: { "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : undefined
    }).then(function (r) { return r.json().then(function (d) { if (!r.ok) throw d; return d; }); });
  }

  function submit(text) {
    text = (text || "").trim();
    if (!text || !state.session) return;
    addMessage("user", text);
    input.value = "";
    if (wsReady) {
      ws.send(JSON.stringify({ type: "message", text: text, metadata: state.meta }));
      setTyping(true);
      return;
    }
    send.disabled = true; setTyping(true);
    http("POST", "/api/v1/widget/messages", {
      key: KEY, session_id: state.session.id, token: state.session.token, text: text, metadata: state.meta
    }).then(function (r) {
      setTyping(false);
      if (r.text) addMessage("assistant", r.text, r);
      else if (r.handed_over) onServerEvent({ type: "status", status: "handed_over" });
    }).catch(function (err) {
      setTyping(false);
      system((err && err.error && err.error.message) || "Message not sent. Please try again.");
    }).then(function () { send.disabled = false; });
  }

  function open() {
    panel.classList.add("open"); launcher.innerHTML = ICON_CLOSE; launcher.appendChild(badge);
    unread = 0; badge.style.display = "none"; input.focus(); scroll();
  }
  function close() { panel.classList.remove("open"); launcher.innerHTML = ICON_CHAT; launcher.appendChild(badge); }

  launcher.addEventListener("click", function () { panel.classList.contains("open") ? close() : open(); });
  closeBtn.addEventListener("click", close);
  send.addEventListener("click", function () { submit(input.value); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(input.value); }
  });

  var q = "?key=" + encodeURIComponent(KEY) + (state.session ? "&session_id=" + encodeURIComponent(state.session.id) +
    "&token=" + encodeURIComponent(state.session.token) : "");
  http("GET", "/api/v1/widget/config" + q).then(function (cfg) {
    brand(cfg);
    if (script.getAttribute("data-open") === "true") open();
    var fresh = !state.session || state.session.id !== cfg.session.id;
    state.session = cfg.session; save(state);
    var showGreeting = function () { addMessage("assistant", cfg.greeting, { quick_replies: cfg.quick_replies }); };
    if (fresh) { state.handedOver = false; save(state); showGreeting(); connect(); return; }
    http("GET", "/api/v1/widget/history?key=" + encodeURIComponent(KEY) + "&session_id=" +
      encodeURIComponent(state.session.id) + "&token=" + encodeURIComponent(state.session.token))
      .then(function (h) {
        if (!h.messages.length) showGreeting();
        h.messages.forEach(function (m) { addMessage(m.role, m.text); });
        unread = 0; badge.style.display = "none";
      }).catch(showGreeting).then(connect);
  }).catch(function (err) {
    console.warn("[AgentGrid] widget disabled:", err && err.error ? err.error.message : err);
    host.remove();
  });
})();
