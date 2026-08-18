/* Trade Assistant — dashboard app.
   Implements the design handoff (frames 1a–1j) on the existing Flask API.
   Vanilla JS, no build step, no CDN.

   Safety rules encoded here (do not soften):
     * Order entry appears only on YOUR approved ideas, only when the broker is
       verifiably connected.
     * execution.mode 'unverified' is rendered with LIVE severity — never paper.
     * Turning execution OFF is one click; turning it ON requires typing ENABLE.
     * Status is never colour alone: every state carries a glyph AND a word.
*/

const GLYPH = { ok: "●", running: "◐", advisory: "■", fail: "▲", idle: "○" };
const VERDICT = {
  approved_for_review: { word: "Ready for your review", cls: "ok", glyph: GLYPH.ok },
  needs_more_research: { word: "Needs more research", cls: "warn", glyph: GLYPH.advisory },
  rejected: { word: "Rejected by risk gate", cls: "bad", glyph: GLYPH.fail },
};

let S = {};                 // last /api/state payload
let view = "today";
let openIdeaId = null;
let poll = null;

/* ---------- helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
const CCY = { GBP: "£", USD: "$", EUR: "€", JPY: "¥", CHF: "CHF ", CAD: "C$", AUD: "A$" };
/* Amounts are shown in the currency they are actually denominated in. A trade
   plan is priced in the instrument's currency, not your base currency, so a
   plan figure must be passed its own `plan.currency` — labelling a €-priced
   position with a £ sign is how you talk yourself into the wrong size. */
const money = (v, ccy) => {
  if (v === null || v === undefined) return "—";
  const code = ccy || S.base_currency || "USD";
  const sym = CCY[code] || (code + " ");
  return sym + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
};
const pct = v => (v === null || v === undefined) ? "—" : Number(v).toFixed(1) + "%";
const ideas = () => S.ideas || [];
const byId = id => ideas().find(i => i.id === id);

/* Market regimes, in plain English. The code names are what the journal stores;
   these are what a human should ever have to read. */
const REGIME = {
  TRENDING_UP: { word: "Trending up", cls: "ok" },
  TRENDING_DOWN: { word: "Trending down", cls: "bad" },
  SIDEWAYS: { word: "Sideways", cls: "muted" },
  // "Compressed" describes the MARKET; "Squeeze" is the strategy that trades
  // it. Using the same word for both made the two tags read as a duplicate.
  VOLATILITY_SQUEEZE: { word: "Compressed", cls: "warn" },
  UNKNOWN: { word: "Unknown", cls: "muted" },
};
const regimeWord = r => (REGIME[r] || {}).word || (r || "—");

/* The two tags that answer "why is this here?" at a glance: which rule set
   found it, and what kind of market it found it in. */
function strategyTag(i) {
  if (!i.strategy && !i.regime) return "";
  const r = REGIME[i.regime] || REGIME.UNKNOWN;
  return `<div class="tags">
    ${i.strategy_label ? `<span class="pill">${esc(i.strategy_label)}</span>` : ""}
    ${i.regime ? `<span class="pill ${r.cls}">${esc(r.word)}</span>` : ""}
  </div>`;
}

function ago(iso) {
  if (!iso) return "";
  const mins = Math.floor((Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z"))) / 60000);
  if (mins < 60) return mins + "m ago";
  if (mins < 1440) return Math.floor(mins / 60) + "h ago";
  return Math.floor(mins / 1440) + "d ago";
}

/* ---------- data ---------- */
async function fetchState() {
  try {
    const r = await fetch("/api/state");
    S = await r.json();
    render();
  } catch (e) {
    $("#viewRoot").innerHTML = `<div class="callout bad"><b>${GLYPH.fail} Cannot reach the app server.</b>
      <div class="muted" style="margin-top:6px">${esc(e)}</div></div>`;
  }
  clearTimeout(poll);
  poll = null;

  // Poll only while something can actually change.
  //
  // A scan is work in progress and its own reason to poll. Otherwise the only
  // thing that moves these numbers is a trading session, so once every market
  // is closed a timer re-fetches figures that are identical by definition —
  // and keeps doing it all night. Nothing is gained and the page flickers
  // through a re-render every fifteen seconds.
  //
  // When everything is shut there is exactly one moment worth waking for: the
  // next open, which the server reports. One timer is set for then rather than
  // hundreds of requests spent discovering it. Refresh by hand any time.
  if (S.scanning) {
    poll = setTimeout(fetchState, 3000);
  } else if (S.any_market_open !== false) {
    poll = setTimeout(fetchState, 15000);
  } else {
    scheduleWakeAtOpen();
  }
}

/* One timer, set for the next market open. Capped because setTimeout is
   unreliable over very long sleeps and a laptop suspended overnight would
   otherwise wake with a dead page. */
const MAX_SLEEP_MS = 30 * 60 * 1000;

function scheduleWakeAtOpen() {
  let wait = MAX_SLEEP_MS;
  if (S.next_market_open) {
    const ms = new Date(S.next_market_open).getTime() - Date.now();
    if (isFinite(ms)) wait = Math.max(30000, Math.min(ms + 5000, MAX_SLEEP_MS));
  }
  clearTimeout(poll);
  poll = setTimeout(fetchState, wait);
}

/* Shown wherever auto-refresh has been suspended, so a page that is not
   updating never looks like a page that has broken. */
function marketClosedNotice() {
  if (S.any_market_open !== false) return "";
  const when = S.next_market_open
    ? new Date(S.next_market_open).toLocaleString(undefined,
        { weekday: "short", hour: "2-digit", minute: "2-digit" })
    : "the next session";
  return `<div class="card">
    <div class="meta">Every market is closed — auto-refresh is off, because no
      price can change until ${esc(when)}. Prices below are the last close.
      <button class="btn btn-sm" style="margin-left:8px" onclick="fetchState()">Refresh now</button>
    </div>
  </div>`;
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch (_) { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}

/* ---------- shell chrome ---------- */
function renderModePlate() {
  const ex = S.execution || {};
  const mode = ex.mode || (ex.enabled ? "unverified" : "off");
  // 'unverified' is deliberately grouped with live: fail closed.
  const map = {
    off: { cls: "", glyph: GLYPH.idle, word: "ORDER ENTRY OFF" },
    paper: { cls: "is-paper", glyph: GLYPH.running, word: "ON · PAPER" },
    live: { cls: "is-live", glyph: GLYPH.fail, word: "ON · LIVE MONEY" },
    unverified: { cls: "is-unverified", glyph: GLYPH.fail, word: "ON · MODE UNVERIFIED" },
  }[mode];
  const acct = ex.account_id ? `<div class="prov" style="margin-top:5px">${esc(ex.account_id)}</div>` : "";
  const action = mode === "off"
    ? `<a href="#" id="enableLink" style="font-size:12px">Turn on order entry…</a>`
    : `<button class="btn btn-sm" id="disableBtn" style="margin-top:10px;width:100%">Turn off</button>`;
  $("#modePlate").innerHTML = `
    <div class="plate ${map.cls} ${mode !== "off" ? "marks" : ""}"><i class="mk"></i>
      <div class="state ${mode === "paper" ? "warn" : mode === "off" ? "muted" : "live"}">
        <span aria-hidden="true">${map.glyph}</span>${map.word}
      </div>
      ${acct}
      <div class="why">${esc(ex.note || "")}</div>
      ${action}
    </div>`;
  $("#enableLink")?.addEventListener("click", e => { e.preventDefault(); enableDialog(); });
  $("#disableBtn")?.addEventListener("click", async () => {
    await post("/api/execution/toggle", { enabled: false });   // one click, no confirmation
    fetchState();
  });
}

function renderProviders() {
  const STATE = {
    active: { glyph: GLYPH.ok, cls: "ok", word: "active" },
    ready: { glyph: GLYPH.idle, cls: "muted", word: "ready" },
    degraded: { glyph: GLYPH.fail, cls: "bad", word: "degraded" },
    off: { glyph: GLYPH.idle, cls: "muted", word: "not configured" },
  };
  $("#providerList").innerHTML = (S.providers || []).map(p => {
    const st = STATE[p.state] || STATE.off;
    return `<div style="display:flex;gap:7px;align-items:baseline;margin-bottom:7px" title="${esc(p.role)} — ${esc(p.note)}">
      <span class="${st.cls}" aria-hidden="true">${st.glyph}</span>
      <span style="flex:1;font-size:12.5px">${esc(p.name)}</span>
      <span class="prov ${st.cls}">${st.word}</span></div>`;
  }).join("");
}

function setNav() {
  document.querySelectorAll("[data-view]").forEach(b =>
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false"));
  const pending = ideas().filter(i => i.decision === "pending").length;
  $("#nb-today").textContent = pending || "";
  $("#nb-watch").textContent = (S.watchlist_symbols || []).length || "";
  $("#nb-ideas").textContent = ideas().length || "";
  $("#nb-journal").textContent = (S.stats?.approved_total) || "";
}

/* ---------- shared components ---------- */
function triageRow(i) {
  const v = VERDICT[i.verdict] || VERDICT.rejected;
  const plan = i.payload?.plan;
  // The age goes ON the row, not only in a filter. An idea researched three
  // weeks ago and one researched this morning looked identical here, which is
  // how a journal of old scans came to read as today's work.
  const stale = i.stale
    ? `<span class="pill warn" style="margin-left:6px">${GLYPH.advisory} ${esc(i.stale_reason || "stale")}</span>`
    : "";
  return `<button class="triage v-${i.verdict}" data-idea="${i.id}">
    <div>
      <div class="tk">${esc(i.ticker)}</div>
      <div class="sub">${esc((i.direction || (i.status === "watch" ? "watching" : "no plan")).toUpperCase())} · #${i.id}</div>
      ${strategyTag(i)}
    </div>
    <div>
      <div style="font-size:12px" class="${v.cls}">${v.glyph} ${v.word}${stale}</div>
      ${i.judgment ? `<div style="font-size:12px;margin-top:2px">
        <b class="${{ BUY: "ok", SELL: "bad", WAIT: "warn", AVOID: "muted" }[i.judgment.action] || ""}">${esc(i.judgment.action)}</b>
        <span class="prov">${esc(i.judgment.confidence || "")} confidence</span></div>` : ""}
      <div class="excerpt">${esc(i.thesis_summary || "")}</div>
    </div>
    <div class="rr num" style="font-size:13px">
      ${plan ? `${n(plan.reward_risk, 1)}:1<div class="prov">max loss ${money(plan.risk_amount, plan.currency)}</div>` : "—"}
    </div>
    <div class="conf-bar">
      ${plan ? `<div class="num" style="text-align:right;font-variant-numeric:tabular-nums" style="font-size:18px">${plan.confidence}</div>
                <div class="meter"><i style="width:${plan.confidence}%"></i></div>` : ""}
    </div>
  </button>`;
}

function bindTriage() {
  document.querySelectorAll("[data-idea]").forEach(el =>
    el.addEventListener("click", () => { openIdeaId = +el.dataset.idea; go("detail"); }));
}

/* ---------- 1a: Today ---------- */
function viewToday() {
  const pending = ideas().filter(i => i.decision === "pending" && i.verdict !== "rejected");
  // A ticker earns a place here by producing a real setup, not by tripping a
  // single indicator. Everything else stays on the watchlist tab.
  const flagged = (S.scan?.watchlist || []).filter(w => w.setup_count > 0);
  const p = S.portfolio || {};
  const degraded = (S.providers || []).filter(x => x.state === "degraded");
  const staleIdeas = ideas().filter(i => i.stale);

  const attention = [
    ...degraded.map(d => `<div class="callout bad" style="margin-bottom:10px">
      <b>${GLYPH.fail} ${esc(d.name)} is degraded</b>
      <div class="prov" style="margin-top:4px">${esc(d.note)}</div></div>`),
    ...staleIdeas.slice(0, 3).map(i => `<div class="callout warn" style="margin-bottom:10px">
      <b>${GLYPH.advisory} ${esc(i.ticker)} plan may be stale</b>
      <div class="prov" style="margin-top:4px">${esc(i.stale_reason || "")}</div></div>`),
    (!S.ai_ready ? `<div class="callout warn" style="margin-bottom:10px">
      <b>${GLYPH.advisory} AI thesis engine is off</b>
      <div class="prov" style="margin-top:4px">Theses fall back to a rule-based screen.</div></div>` : ""),
  ].filter(Boolean).join("") || `<div class="prov">${GLYPH.ok} Nothing needs attention.</div>`;

  // How much of the queue is left over from old scans. When it is all of it,
  // the honest headline is "nothing current", not "81 decisions waiting".
  const freshPending = pending.filter(i => !i.stale);
  const stalePending = pending.filter(i => i.stale);
  const queueNotice = (pending.length && !freshPending.length) ? `
    <div class="callout warn" style="margin-bottom:12px">
      <b>${GLYPH.advisory} Nothing here is current.</b>
      <div class="prov" style="margin-top:4px">All ${pending.length} pending ideas
        come from earlier scans — the oldest is
        ${Math.round(Math.max(...pending.map(i => i.age_hours || 0)) / 24)} days old.
        Their prices and levels were computed then, not now.
        Run a scan to get ideas against today's market.</div>
      <button class="btn btn-sm" style="margin-top:8px" onclick="document.getElementById('scanBtn').click()">Run a scan</button>
    </div>` : "";

  return `
  <div class="grid" style="grid-template-columns:1fr 320px;align-items:start">
    <div class="grid" style="gap:var(--s5)">
      <section>
        <div class="label" style="margin-bottom:12px">Needs your decision
          (${freshPending.length}${stalePending.length ? ` current · ${stalePending.length} from old scans` : ""})</div>
        ${queueNotice}
        ${pending.length ? `<div class="grid" style="gap:10px">${[...freshPending, ...stalePending].slice(0, 5).map(triageRow).join("")}</div>`
      : emptyIdeas()}
        ${pending.length > 5 ? `<button class="btn btn-sm" style="margin-top:12px" data-goto="ideas">See all ${pending.length}</button>` : ""}
      </section>

      <section>
        <div class="label" style="margin-bottom:12px">${S.scan ? `Setups found in last scan (${flagged.length})` : "Setups found in last scan"}</div>
        ${!S.scan ? `<div class="callout dashed" style="margin-bottom:10px"><span class="muted">
          No scan has run in this session, so there is nothing to show here — this is
          not the same as a scan that found nothing.</span></div>` : ""}
        ${flagged.length ? `<div class="card" style="padding:0"><table class="wide"><thead><tr>
            <th>Ticker</th><th class="n">Price</th><th class="n">Chg</th>
            <th class="n">Vol vs 20d</th><th class="n">RSI</th><th>Market</th>
            <th>Strategies run</th></tr></thead><tbody>
            ${flagged.map(w => {
    const r = REGIME[w.regime] || REGIME.UNKNOWN;
    return `<tr>
              <td class="n">${esc(w.ticker)}</td>
              <td class="n">${n(w.price)}</td>
              <td class="n ${w.change_pct >= 0 ? "ok" : "bad"}">${n(w.change_pct)}%</td>
              <td class="n">${n(w.volume_ratio, 1)}x</td>
              <td class="n">${n(w.rsi, 0)}</td>
              <td><span class="pill ${r.cls}">${esc(regimeWord(w.regime))}</span></td>
              <td class="prov">${esc((w.strategies_run || []).join(", ") || "—")}</td></tr>`;
  }).join("")}
          </tbody></table></div>`
      : `<div class="callout dashed"><span class="muted">No strategy produced a setup in the last scan.
          That is a normal result — a setup needs a strategy's full rule set to line up, not just one signal.</span></div>`}
      </section>
    </div>

    <div class="grid" style="gap:var(--s4)">
      <div class="card marks"><i class="mk"></i>
        <div class="label">${S.paper ? "Paper book" : "Portfolio"}</div>
        ${S.paper ? (() => {
          // The book runs in its OWN currency — converted once when it opened,
          // so its return measures the strategies rather than the strategies
          // plus the exchange rate. Formatting it with the account's currency
          // symbol produced "£1,362,499 USD", which is two different claims
          // about the same number.
          const bc = S.paper.base_currency || "USD";
          const m = v => money(v, bc);
          const t = S.paper.today;
          const rep = S.paper.reporting;
          // The ACCOUNT currency leads, because that is what the holder can
          // spend. The book currency sits underneath as the strategy's own
          // result, with the FX contribution named rather than buried — one
          // figure includes sterling's move and the other does not, and a
          // reader who cannot tell them apart will credit the rules for a
          // currency swing they never took a position on.
          return `
          <div class="stat-v" style="margin-top:10px">${rep ? money(rep.equity, rep.currency) : m(S.paper.equity)}</div>
          <div class="prov">
            <span class="${(rep ? rep.return_pct : S.paper.return_pct) > 0 ? "up" : (rep ? rep.return_pct : S.paper.return_pct) < 0 ? "down" : ""}">
              ${(rep ? rep.return_pct : S.paper.return_pct) > 0 ? "+" : ""}${Number((rep ? rep.return_pct : S.paper.return_pct) ?? 0).toFixed(2)}%</span>
            from ${rep ? money(rep.starting_equity, rep.currency) : m(S.paper.starting_equity)} ·
            ${S.paper.closed_trades} closed
          </div>
          ${rep ? `<div class="prov" style="margin-top:6px;padding-top:6px;border-top:1px solid var(--line)">
            <b>${m(S.paper.equity)}</b> in the book &mdash;
            <span class="${S.paper.return_pct > 0 ? "up" : S.paper.return_pct < 0 ? "down" : ""}">
              ${S.paper.return_pct > 0 ? "+" : ""}${Number(S.paper.return_pct ?? 0).toFixed(2)}%</span>
            strategy only${rep.fx_contribution_pct != null ? `,
            <span class="${rep.fx_contribution_pct > 0 ? "up" : rep.fx_contribution_pct < 0 ? "down" : ""}">
              ${rep.fx_contribution_pct > 0 ? "+" : ""}${rep.fx_contribution_pct}%</span> from ${esc(rep.pair)}` : ""}
            <div style="margin-top:2px">1 ${esc(bc)} = ${rep.rate} ${esc(rep.currency)}</div>
          </div>` : ""}
          ${t ? `<div class="prov" style="margin-top:4px">
            Today <span class="${t.change > 0 ? "up" : t.change < 0 ? "down" : ""}">
            ${t.change > 0 ? "+" : ""}${m(t.change)}</span>
            (realised ${m(t.realised)})</div>` : ""}
          <div class="prov" style="margin-top:4px">Risk budget ${money(p.value)} from config.yaml</div>`;
        })() : `
          <div class="stat-v" style="margin-top:10px">${money(p.value)}</div>
          <div class="prov">${esc(S.base_currency || "USD")} · from ${S.execution?.connected ? "IBKR" : "config.yaml"}
            — no paper book has been started, so this is the configured risk budget,
            not a traded balance.</div>
        `}
        ${(() => {
          // Exposure and position count must describe the SAME portfolio as the
          // equity above it. They read from config.positions — holdings declared
          // by hand for the risk gate — which is empty, so a book carrying 46
          // positions at 88% exposure displayed "0 open positions, 0.0%"
          // directly beneath its own equity.
          const usingBook = !!S.paper;
          const exposure = usingBook ? S.paper.exposure_pct : p.exposure_pct;
          const cap = usingBook ? 90 : (p.max_total_exposure_pct || 60);
          const count = usingBook ? S.paper.open_positions : (p.positions || []).length;
          return `
        <div style="margin-top:16px">
          <div style="display:flex;justify-content:space-between;font-size:12px">
            <span class="muted">Exposure</span><span class="num" style="text-align:right;font-variant-numeric:tabular-nums">${pct(exposure)}</span>
          </div>
          <div class="meter" style="height:6px;margin-top:5px">
            <i style="width:${Math.min(100, (exposure / cap) * 100)}%"></i>
          </div>
          <div class="prov" style="margin-top:4px">cap ${pct(cap)}${usingBook ? " · paper book" : ""}</div>
        </div>
        <div class="grid" style="grid-template-columns:1fr 1fr;margin-top:16px;gap:var(--s2)">
          <div><div class="num" style="font-size:19px;font-variant-numeric:tabular-nums">${count}</div><div class="stat-l">Open positions</div></div>
          <div><div class="num" style="font-size:19px;font-variant-numeric:tabular-nums">${pct(p.risk_per_trade_pct)}</div><div class="stat-l">Risk per trade</div></div>
        </div>`;
        })()}
      </div>

      <div class="card" id="scheduleCard">
        <div class="label">Automatic runs</div>
        <div class="prov" style="margin-top:8px">Loading…</div>
      </div>

      <div class="card">
        <div class="label">Thesis accuracy</div>
        <div class="stat-v" style="margin-top:10px">${S.stats?.win_rate_pct === null || S.stats?.win_rate_pct === undefined ? "—" : S.stats.win_rate_pct + "%"}</div>
        <div class="prov">${S.stats?.wins || 0} wins · ${S.stats?.losses || 0} losses · ${S.stats?.open || 0} open</div>
        <button class="btn btn-sm" style="margin-top:12px;width:100%" data-goto="journal">Open journal</button>
      </div>

      <div class="card">
        <div class="label" style="margin-bottom:10px">Attention</div>
        ${attention}
      </div>
    </div>
  </div>`;
}

function emptyIdeas() {
  return `<div class="callout dashed" style="text-align:center;padding:var(--s6)">
    <div class="label">No ideas yet</div>
    <p class="muted" style="margin:10px 0 16px;font-size:13px">
      Run a scan to check your watchlist for unusual volume, momentum shifts and breakouts.</p>
    <button class="btn btn-primary" data-runscan>Run the first scan</button></div>`;
}

/* ---------- 1b: Ideas queue ---------- */
let ideaFilter = "pending", ideaSort = "verdict", ideaSearch = "";

function viewIdeas() {
  const all = ideas();
  const groups = {
    pending: all.filter(i => i.decision === "pending" && i.verdict !== "rejected"),
    approved: all.filter(i => i.decision === "approved"),
    needs_research: all.filter(i => i.decision === "needs_research"),
    rejected: all.filter(i => i.verdict === "rejected"),
  };
  const chips = [
    ["pending", "Awaiting you", groups.pending.length],
    ["approved", "Approved", groups.approved.length],
    ["needs_research", "More research", groups.needs_research.length],
    ["rejected", "Rejected", groups.rejected.length],
    ["all", "All", all.length],
  ];
  let list = ideaFilter === "all" ? all : groups[ideaFilter] || [];
  if (ideaSearch) list = list.filter(i => i.ticker.toLowerCase().includes(ideaSearch.toLowerCase()));
  if (ideaSort === "confidence") list = [...list].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  if (ideaSort === "newest") list = [...list].sort((a, b) => b.id - a.id);

  const rejectedCollapsed = ideaFilter === "rejected"
    ? `<div class="callout dashed" style="margin-bottom:12px">
         <span class="muted">These failed a hard risk check, so there is nothing to action.
         They are kept for the record.</span></div>` : "";

  return `
    <div style="display:flex;gap:var(--s2);flex-wrap:wrap;align-items:center;margin-bottom:var(--s4)">
      <input id="ideaSearch" placeholder="Filter by ticker" value="${esc(ideaSearch)}" size="14">
      <div style="display:flex;gap:0;margin-left:auto">
        ${["verdict", "confidence", "newest"].map(s =>
    `<button class="btn btn-sm" data-sort="${s}" ${ideaSort === s ? 'style="border-color:var(--accent);color:var(--accent)"' : ""}>${s}</button>`).join("")}
      </div>
    </div>
    <div style="display:flex;gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s4)">
      ${chips.map(([k, label, count]) => `<button class="pill" data-chip="${k}"
        ${ideaFilter === k ? 'style="background:var(--accent-dd);border-color:var(--accent);color:var(--text)"' : ""}>
        ${esc(label)} · ${count}</button>`).join("")}
    </div>
    ${rejectedCollapsed}
    ${list.length ? `<div class="grid" style="gap:10px">${list.map(triageRow).join("")}</div>`
      : `<div class="callout dashed"><span class="muted">Nothing in this group.</span></div>`}`;
}

/* ---------- 1c: Idea detail ---------- */
/* Why this setup fired at all — the strategy's own checklist, in order.
   This is the part that is NOT the language model: every line is a rule that
   was measured and passed, not a narrative. */
function setupCard(i) {
  const si = i.payload?.strategy_idea;
  if (!si) return "";
  const reasons = si.reasons || [];
  return `<div class="card">
    <div class="label" style="margin-bottom:8px">The setup — why this was flagged</div>
    <p style="max-width:68ch;margin-bottom:10px"><b>${esc(si.headline || "")}</b></p>
    <ul style="padding-left:16px">
      ${reasons.map(r => `<li style="font-size:13px;margin-bottom:5px">${esc(r)}</li>`).join("")}
    </ul>
    <div class="prov" style="margin-top:10px">
      Found by the ${esc(si.strategy_label || "strategy")} rules in a
      ${esc(regimeWord(si.regime).toLowerCase())} market. These are measured rules,
      not the AI's opinion — the written thesis below is the separate second opinion.
    </div>
  </div>`;
}

/* The answer, above the evidence for it.

   Placed at the top of the idea deliberately. Everything below this card was
   already on the page and the reader was left to combine five panels into a
   decision; putting the conclusion last is what made the tool feel like it
   never gave one. */
function judgmentCard(j) {
  if (!j) return "";
  const tone = { BUY: "ok", SELL: "bad", HOLD: "", WAIT: "warn", AVOID: "muted" }[j.action] || "";
  const bullets = (title, arr, cls) => (arr && arr.length)
    ? `<div style="flex:1;min-width:210px">
         <div class="label ${cls}" style="margin-bottom:6px">${title}</div>
         <ul style="padding-left:16px">${arr.map(x =>
            `<li style="font-size:13px;margin-bottom:4px">${esc(x)}</li>`).join("")}</ul>
       </div>` : "";

  const news = (j.news || []).length ? `
    <div style="margin-top:14px">
      <div class="label" style="margin-bottom:6px">Recent news</div>
      ${j.news.map(n => `<div style="font-size:13px;margin-bottom:4px">
          ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.headline)}</a>`
                  : esc(n.headline)}
          <span class="prov">${esc([n.source, n.date].filter(Boolean).join(" · "))}</span>
        </div>`).join("")}
      <div class="prov" style="margin-top:6px">Context only — no rule in this system
        trades on a headline.</div>
    </div>` : "";

  return `<div class="card">
    <div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">
      <span class="h ${tone}" style="font-size:26px">${esc(j.action)}</span>
      <span class="pill">${esc(j.confidence || "")} confidence</span>
    </div>
    <p style="max-width:70ch;margin-top:8px"><b>${esc(j.headline || "")}</b></p>
    <p class="meta" style="max-width:70ch">${esc(j.meaning || "")}</p>
    ${(j.because || []).length ? `<ul style="padding-left:16px;margin-top:8px">
      ${j.because.map(x => `<li style="font-size:13px;margin-bottom:4px">${esc(x)}</li>`).join("")}
    </ul>` : ""}
    ${j.conflict ? `<div class="callout warn" style="margin-top:10px">${esc(j.conflict)}</div>` : ""}
    <div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:14px">
      ${bullets("Reasons for", j.pros, "ok")}
      ${bullets("Reasons against", j.cons, "bad")}
    </div>
    ${news}
  </div>`;
}

function viewDetail() {
  const i = byId(openIdeaId);
  if (!i) return `<div class="callout bad">Idea not found.</div>`;
  const p = i.payload || {}, th = p.thesis || {}, gate = p.gate || {}, plan = p.plan;
  const v = VERDICT[i.verdict] || VERDICT.rejected;
  const ex = S.execution || {};
  const list = (title, arr, cls = "") => `<div class="card">
      <div class="label ${cls}" style="margin-bottom:8px">${title}</div>
      <ul style="padding-left:16px">${(arr || []).map(x => `<li style="font-size:13px;margin-bottom:5px">${esc(x)}</li>`).join("") || "<li class='muted'>None</li>"}</ul>
    </div>`;

  return `
  <button class="btn btn-sm" data-goto="ideas" style="margin-bottom:var(--s4)">← All ideas</button>
  <div class="grid" style="grid-template-columns:1fr 348px;align-items:start;gap:var(--s5)">
    <div class="grid" style="gap:var(--s4)">
      <div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <span class="h" style="font-size:30px">${esc(i.ticker)}</span>
          ${i.direction ? `<span class="pill">${esc(i.direction.toUpperCase())}</span>` : ""}
          ${i.status === "watch" ? `<span class="pill warn">${GLYPH.advisory} Watching</span>` : ""}
          <span class="pill ${v.cls}">${v.glyph} ${v.word}</span>
          ${i.strategy_label ? `<span class="pill">${esc(i.strategy_label)}</span>` : ""}
          ${i.regime ? `<span class="pill ${(REGIME[i.regime] || REGIME.UNKNOWN).cls}">${esc(regimeWord(i.regime))}</span>` : ""}
          ${i.stale ? `<span class="pill warn">${GLYPH.advisory} Stale · ${esc(i.stale_reason || "")}</span>` : ""}
        </div>
        <div class="prov" style="margin-top:6px">Idea #${i.id} · ${esc(i.created_at)} UTC · ${esc(th.source || "")}</div>
      </div>

      ${judgmentCard(i.judgment)}

      ${setupCard(i)}

      <div class="card">
        <div class="label" style="margin-bottom:10px">Thesis</div>
        <p style="max-width:68ch">${esc(th.thesis_summary || "")}</p>
      </div>

      <div class="grid" style="grid-template-columns:1fr 1fr">
        ${list("Bull case", th.bull_case, "ok")}
        ${list("Bear case", th.bear_case, "bad")}
      </div>
      <div class="grid" style="grid-template-columns:1fr 1fr">
        ${list("Catalysts up", th.catalysts_up, "ok")}
        ${list("Catalysts down", th.catalysts_down, "bad")}
      </div>
      <div class="grid" style="grid-template-columns:1fr 1fr 1fr">
        <div class="card"><div class="label" style="margin-bottom:8px">Valuation</div>
          <div style="font-size:13px">${esc(th.valuation_view || "—")}</div></div>
        <div class="card"><div class="label" style="margin-bottom:8px">Balance sheet</div>
          <div style="font-size:13px">${esc(th.balance_sheet_risk || "—")}</div></div>
        ${list("Data gaps", th.data_gaps)}
      </div>
    </div>

    <!-- fixed rail -->
    <div class="grid" style="gap:var(--s4);position:sticky;top:var(--s4)">
      ${plan ? `
      <div class="card marks"><i class="mk"></i>
        <div class="label" style="margin-bottom:12px">Trade plan</div>
        <div class="grid" style="grid-template-columns:1fr 1fr;gap:12px">
          <div><div class="stat-l">Entry zone</div><div class="num" style="text-align:right;font-variant-numeric:tabular-nums">${n(plan.entry_zone[0])}–${n(plan.entry_zone[1])}</div></div>
          <div><div class="stat-l">Target</div><div class="num ok">${n(plan.target)}</div></div>
          <div><div class="stat-l">Stop</div><div class="num bad">${n(plan.stop)}</div></div>
          <div><div class="stat-l">Reward:risk</div><div class="num" style="text-align:right;font-variant-numeric:tabular-nums">${n(plan.reward_risk, 1)}:1</div></div>
          <div><div class="stat-l">Size</div><div class="num" style="text-align:right;font-variant-numeric:tabular-nums">${plan.shares} sh</div>
               <div class="prov">${money(plan.position_value, plan.currency)}</div></div>
          <div><div class="stat-l">Max loss</div><div class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(plan.risk_amount, plan.currency)}</div>
               <div class="prov">${n(plan.risk_pct_of_portfolio ?? (plan.risk_amount / (S.portfolio?.value || 1) * 100), 2)}% of book</div></div>
        </div>
        ${plan.sized_down_to_cap ? `<div class="callout warn" style="margin-top:12px;font-size:12px">
          ${GLYPH.advisory} Sized down to your position cap: ${plan.shares} instead of ${plan.shares_by_risk_alone}
          — risking ${plan.risk_budget_used_pct}% of budget.</div>` : ""}
        <div style="margin-top:14px">
          <div style="display:flex;justify-content:space-between;font-size:12px">
            <span class="muted">Confidence</span><span class="num" style="text-align:right;font-variant-numeric:tabular-nums">${plan.confidence}/100</span></div>
          <div class="meter" style="margin-top:5px"><i style="width:${plan.confidence}%"></i></div>
        </div>
        <details style="margin-top:10px"><summary class="prov">Why ${plan.confidence} — reasons</summary>
          <ul style="padding-left:16px;margin-top:6px">
            ${(plan.confidence_reasons || []).map(r => `<li class="prov">${esc(r)}</li>`).join("")}</ul></details>
      </div>` : i.status === "watch"
      ? `<div class="callout dashed"><span class="muted">No trade plan yet — this is a watch item.
          The move hasn't picked a direction, so there is nothing to size. It will become a
          plan only if it breaks and a strategy confirms it.</span></div>`
      : `<div class="callout dashed"><span class="muted">No trade plan — no defined-risk setup was found.</span></div>`}

      <div class="card">
        <div class="label" style="margin-bottom:10px">Risk checks</div>
        ${(gate.hard_failures || []).map(x => `<div style="font-size:12.5px;margin-bottom:6px" class="bad">${GLYPH.fail} ${esc(x)}</div>`).join("")}
        ${(gate.soft_flags || []).map(x => `<div style="font-size:12.5px;margin-bottom:6px" class="warn">${GLYPH.advisory} ${esc(x)}</div>`).join("")}
        ${!(gate.hard_failures || []).length && !(gate.soft_flags || []).length
      ? `<div class="prov ok">${GLYPH.ok} All checks passed.</div>` : ""}
      </div>

      <div class="card">
        <div class="label">Your decision</div>
        <div class="h" style="font-size:20px;margin:6px 0 12px">${esc((i.decision || "pending").toUpperCase())}</div>
        <button class="btn btn-primary" style="width:100%;text-align:left" data-decide="approved">Approve for my watch</button>
        <div class="grid" style="grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
          <button class="btn" data-decide="needs_research">More research</button>
          <button class="btn" data-decide="rejected">Reject</button>
        </div>
        <div class="prov" style="margin-top:10px">Approving records your view. It places nothing.</div>
        ${outcomeControl(i)}
      </div>

      ${orderEntry(i, plan, ex)}
    </div>
  </div>`;
}

function outcomeControl(i) {
  if (i.decision !== "approved") return "";
  const plan = i.payload?.plan;
  const ccy = plan?.currency;
  const closed = i.outcome && i.outcome !== "open";

  // The realised result, once it exists. R is shown next to the cash figure
  // because cash alone can't be compared across differently-sized positions.
  const result = (closed && i.pnl_instrument !== null && i.pnl_instrument !== undefined)
    ? `<div class="callout ${i.pnl_instrument >= 0 ? "ok" : "bad"}" style="margin-top:10px;font-size:12.5px">
        <b>${i.pnl_instrument >= 0 ? "+" : ""}${money(i.pnl_instrument, ccy)}</b>
        ${i.r_multiple !== null && i.r_multiple !== undefined
      ? ` · ${i.r_multiple >= 0 ? "+" : ""}${n(i.r_multiple, 2)}R` : ""}
        ${i.pnl_base !== null && i.pnl_base !== undefined && ccy !== S.base_currency
      ? `<div class="prov" style="margin-top:4px">${money(i.pnl_base)} in your base currency
           (rate ${n(i.exit_fx_rate, 4)} on the day you closed)</div>` : ""}
        <div class="prov" style="margin-top:4px">
          ${esc((i.direction || "").toUpperCase())} ${plan?.shares ?? "—"} sh ·
          in ${n(i.entry_used ?? plan?.entry)} → out ${n(i.outcome_price)}</div>
      </div>`
    : closed
      ? `<div class="callout dashed" style="margin-top:10px;font-size:12px">
          <span class="muted">Counted in your hit rate, but not in profit and loss —
          no exit price was recorded.</span></div>`
      : "";

  return `<div style="margin-top:14px;border-top:1px solid var(--line);padding-top:12px">
    <div class="label" style="margin-bottom:8px">Outcome</div>
    ${plan ? `<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
      <input id="exitPrice" placeholder="Exit price${ccy ? " (" + ccy + ")" : ""}"
             inputmode="decimal" size="12" value="${i.outcome_price ?? ""}">
      <span class="prov">the price you actually got out at</span>
    </div>
    <details style="margin-bottom:8px"><summary class="prov">Filled at a different entry?</summary>
      <input id="entryUsed" placeholder="Actual entry (planned ${n(plan.entry)})"
             inputmode="decimal" size="18" value="${i.entry_used ?? ""}" style="margin-top:6px">
      <div class="prov" style="margin-top:4px">Leave blank to use the planned entry.</div>
    </details>` : ""}
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      ${["open", "win", "loss", "scratch"].map(o =>
    `<button class="btn btn-sm" data-outcome="${o}"
        ${i.outcome === o ? 'style="border-color:var(--accent);color:var(--accent)"' : ""}>${o}</button>`).join("")}
    </div>
    <div id="outcomeMsg" class="prov" style="margin-top:8px"></div>
    ${result}
    <div class="prov" style="margin-top:8px">Recording outcomes is what makes the journal's
      accuracy stats meaningful. Adding the exit price is what makes the profit-and-loss
      and expectancy figures per strategy possible.</div>
  </div>`;
}

/* order entry — the only place a ticket can originate */
function orderEntry(i, plan, ex) {
  if (!ex.enabled || i.decision !== "approved" || !plan) return "";
  const ticket = (S.orders || []).find(o => o.idea_id === i.id && o.status === "pending_confirmation");
  if (ticket) {
    return `<div class="callout warn"><b>${GLYPH.advisory} Order ticket #${ticket.id} awaiting confirmation</b>
      <button class="btn btn-sm" style="margin-top:10px" data-openticket="${ticket.id}">Review ticket</button></div>`;
  }
  if (!ex.connected) {
    return `<div class="callout warn"><b>${GLYPH.fail} Order entry unavailable</b>
      <div class="prov" style="margin-top:5px">${esc(ex.note || "")}</div></div>`;
  }
  const modeWord = ex.mode === "paper" ? "PAPER" : "LIVE MONEY";
  return `<div class="callout dashed">
    <button class="btn" style="width:100%" data-prepare="${i.id}">Prepare order ticket…</button>
    <div class="prov" style="margin-top:8px">Re-checks live risk and current price, then asks you to confirm.
      Broker mode: <b class="${ex.mode === "paper" ? "warn" : "live"}">${modeWord}</b>.</div></div>`;
}

/* ---------- 1f: Watchlist ---------- */
function viewWatchlist() {
  const rows = S.scan?.watchlist || [];
  const seen = new Set(rows.map(r => r.ticker));
  const prog = S.scan_progress;
  const banner = (S.scanning && prog) ? scanBanner(prog) : "";
  const symbols = S.watchlist_symbols || [];

  return `
    ${banner}
    <div class="callout dashed" style="margin:var(--s4) 0">
      <div style="display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap">
        <input id="wlInput" placeholder="Search any stock — ticker or company name" size="30">
        <button class="btn" id="wlAdd">Add to scan list</button>
        <span class="prov" id="wlMsg"></span>
      </div>
      <div class="prov" style="margin-top:8px">
        Search covers every US-listed symbol<span id="uniCount"></span> — not just this list.
        Only the symbols below are scanned, so you control what costs time and API credits.
      </div>
    </div>
    <div class="card" style="padding:0"><table class="wide"><thead><tr>
      <th>Ticker</th><th class="n">Price</th><th class="n">Chg</th><th class="n">Vol vs 20d</th>
      <th class="n">RSI</th><th>Market</th><th>Setups found</th><th></th></tr></thead><tbody>
      ${symbols.map(sym => {
    const w = rows.find(r => r.ticker === sym);
    const setups = w?.setup_count || 0;
    const glyph = w ? (setups ? `<span class="warn">${GLYPH.advisory}</span>`
      : `<span class="ok">${GLYPH.ok}</span>`)
      : (S.scanning ? `<span class="muted">${GLYPH.running}</span>` : `<span class="muted">${GLYPH.idle}</span>`);
    if (!w) return `<tr class="dim"><td>${glyph} ${esc(sym)}</td><td class="n">—</td><td class="n">—</td>
        <td class="n">—</td><td class="n">—</td><td class="prov">—</td>
        <td class="prov">${S.scanning ? "queued" : "not scanned yet"}</td>
        <td><button class="btn btn-sm" data-wlremove="${esc(sym)}">Remove</button></td></tr>`;
    const r = REGIME[w.regime] || REGIME.UNKNOWN;
    // Why nothing fired matters as much as what did — a silent row with no
    // explanation is indistinguishable from a broken scan.
    const outcome = setups
      ? `<span class="warn">${setups} setup${setups > 1 ? "s" : ""}</span>`
      : `<span class="prov">${esc((w.router_notes || [])[0] || "—")}</span>`;
    return `<tr title="${esc((w.regime_reasons || []).join(" · "))}">
        <td>${glyph} <b>${esc(sym)}</b></td>
        <td class="n">${n(w.price)}</td>
        <td class="n ${w.change_pct >= 0 ? "ok" : "bad"}">${n(w.change_pct)}%</td>
        <td class="n">${n(w.volume_ratio, 1)}x</td>
        <td class="n">${n(w.rsi, 0)}</td>
        <td><span class="pill ${r.cls}">${esc(regimeWord(w.regime))}</span></td>
        <td>${outcome}</td>
        <td><button class="btn btn-sm" data-wlremove="${esc(sym)}">Remove</button></td></tr>`;
  }).join("")}
    </tbody></table></div>
    <div class="prov" style="margin-top:10px">Showing ${rows.length} of ${symbols.length} · rows fill in as the scan reaches them.
      Hover a row to see why its market was classified that way.</div>`;
}

function scanBanner(prog) {
  const doneW = (prog.done / Math.max(prog.total, 1)) * 100;
  const elapsed = prog.started_at ? Math.round((Date.now() - new Date(prog.started_at + "Z")) / 1000) : 0;
  const rate = prog.done ? elapsed / prog.done : 0;
  const eta = prog.done ? Math.round(rate * (prog.total - prog.done)) : null;
  return `<div class="scanbar">
    <div style="display:flex;justify-content:space-between;gap:var(--s3);flex-wrap:wrap;align-items:center">
      <div>
        <div class="h" style="font-size:15px">${GLYPH.running} Scanning ${prog.done} of ${prog.total}</div>
        <div class="prov">${esc(prog.current_ticker || "")} · ${esc(prog.current_stage || "")}</div>
      </div>
      <div class="prov">${elapsed}s elapsed${eta !== null ? ` · ~${eta}s left` : ""}</div>
      <button class="btn btn-sm" id="cancelScan">Cancel scan</button>
    </div>
    <div class="progress"><span class="done" style="width:${doneW}%"></span><span class="now" style="width:${100 / Math.max(prog.total, 1)}%"></span></div>
  </div>`;
}

/* One breakdown table: wins, losses, hit rate and total risked for whatever
   the stats were grouped by. Deliberately shows the sample size next to every
   percentage — "100% win rate" off two trades means nothing, and a bare
   percentage invites exactly that mistake. */
function performanceTable(title, groups, note) {
  const rows = Object.entries(groups || {});
  if (!rows.length) return "";
  return `<div style="margin-top:var(--s5)">
    <div class="label" style="margin-bottom:12px">${esc(title)}</div>
    <div class="card" style="padding:0"><table class="wide"><thead><tr>
      <th>${esc(title)}</th><th class="n">Closed</th><th class="n">Won</th>
      <th class="n">Lost</th><th class="n">Hit rate</th><th class="n">P&amp;L</th>
      <th class="n">Expectancy</th><th class="n">Profit factor</th>
    </tr></thead><tbody>
      ${rows.map(([, o]) => {
    const thin = o.closed < 5;
    const pnlCls = o.pnl === null ? "" : o.pnl >= 0 ? "ok" : "bad";
    // Expectancy is the figure that decides a strategy's future, so it is
    // coloured on the sign: positive average R keeps it, negative kills it.
    const expCls = o.expectancy_r === null ? "" : o.expectancy_r >= 0 ? "ok" : "bad";
    return `<tr>
        <td>${esc(o.label)}</td>
        <td class="n">${o.closed}</td>
        <td class="n ok">${o.wins}</td>
        <td class="n bad">${o.losses}</td>
        <td class="n">${o.win_rate_pct === null ? "—" : o.win_rate_pct + "%"}
          ${thin ? `<span class="prov"> too few to judge</span>` : ""}</td>
        <td class="n ${pnlCls}">${o.pnl === null ? "—" : (o.pnl >= 0 ? "+" : "") + money(o.pnl)}
          ${o.pnl_partial ? `<span class="prov" title="Some closed ideas have no exit price recorded"> partial</span>` : ""}</td>
        <td class="n ${expCls}">${o.expectancy_r === null ? "—"
        : (o.expectancy_r >= 0 ? "+" : "") + n(o.expectancy_r, 2) + "R"}</td>
        <td class="n">${o.profit_factor !== null ? n(o.profit_factor, 2)
        : o.no_losses_yet ? `<span class="prov">no losses yet</span>` : "—"}</td></tr>`;
  }).join("")}
    </tbody></table></div>
    <div class="prov" style="margin-top:8px">${esc(note)}</div>
  </div>`;
}

/* ---------- 1g: Journal ---------- */
function viewJournal() {
  const st = S.stats || {};
  const kpi = (v, l) => `<div class="card marks"><i class="mk"></i>
    <div class="num" style="text-align:right;font-variant-numeric:tabular-nums" style="font-size:26px">${v}</div><div class="stat-l">${l}</div></div>`;
  const months = Object.entries(st.by_month || {});
  const maxM = Math.max(1, ...months.map(([, o]) => (o.win || 0) + (o.loss || 0) + (o.scratch || 0)));

  const buckets = Object.entries(st.by_confidence_bucket || {});
  const verdicts = Object.entries(st.by_verdict || {});
  const closedRows = ideas().filter(i => i.decision === "approved" && i.outcome !== "open").slice(0, 12);

  return `
  <div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:var(--s5)">
    ${kpi(st.approved_total || 0, "Approved by you")}
    ${kpi((st.wins || 0) + (st.losses || 0), "Closed")}
    ${kpi(st.win_rate_pct === null || st.win_rate_pct === undefined ? "—" : st.win_rate_pct + "%", "Win rate")}
    ${kpi(st.open || 0, "Still open")}
    ${kpi(st.avg_confidence ?? "—", "Avg confidence")}
  </div>

  <div class="grid" style="grid-template-columns:1.35fr 1fr;align-items:start">
    <div class="card">
      <div class="label" style="margin-bottom:14px">Outcomes by month</div>
      ${months.length ? `<div class="bars">${months.map(([m, o]) => {
    const w = o.win || 0, l = o.loss || 0, s = o.scratch || 0, tot = w + l + s;
    const h = x => (x / maxM) * 150;
    return `<div class="col" title="${esc(m)}: ${w}W ${l}L ${s}S">
          <div class="prov" style="text-align:center">${tot ? Math.round(w / Math.max(w + l, 1) * 100) + "%" : ""}</div>
          <div class="seg" style="height:${h(w)}px;background:var(--ok)"></div>
          <div class="seg" style="height:${h(l)}px;background:var(--bad)"></div>
          <div class="seg" style="height:${h(s)}px;background:var(--line2)"></div>
          <div class="prov" style="text-align:center">${m.slice(5)}</div></div>`;
  }).join("")}</div>
        <div class="prov" style="margin-top:10px">
          <span class="ok">${GLYPH.ok} win</span> · <span class="bad">${GLYPH.fail} loss</span> · <span class="muted">${GLYPH.idle} scratch</span></div>`
      : `<div class="callout dashed"><span class="muted">No closed ideas yet — log outcomes on approved ideas to build this.</span></div>`}
    </div>

    <div class="grid" style="gap:var(--s4)">
      <div class="card">
        <div class="label" style="margin-bottom:12px">Win rate by confidence</div>
        ${buckets.map(([label, o]) => `<div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;font-size:12px">
            <span>${esc(label)}</span>
            <span class="num" style="text-align:right;font-variant-numeric:tabular-nums">${o.win_rate_pct === null ? "—" : o.win_rate_pct + "%"} <span class="prov">(${o.wins}W/${o.losses}L)</span></span>
          </div>
          <div class="hbar"><i style="width:${o.win_rate_pct || 0}%"></i></div></div>`).join("")}
        <div class="prov">Does a higher confidence score actually predict a better outcome?</div>
      </div>
      <div class="card">
        <div class="label" style="margin-bottom:12px">Win rate by risk-gate verdict</div>
        ${verdicts.length ? verdicts.map(([v, o]) => `<div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:6px">
          <span>${esc((VERDICT[v] || {}).word || v)}</span>
          <span class="num" style="text-align:right;font-variant-numeric:tabular-nums">${o.win_rate_pct === null ? "—" : o.win_rate_pct + "%"}</span></div>`).join("")
      : `<span class="muted prov">No closed ideas yet.</span>`}
      </div>
    </div>
  </div>

  ${performanceTable("Which strategy actually works", st.by_strategy,
    "Expectancy is the number that decides a strategy's future: it is the average profit per trade "
    + "measured in units of the risk taken, so a strategy that trades small is judged fairly against "
    + "one that trades large. Positive expectancy with a 40% hit rate beats negative expectancy with 70%.")}
  ${performanceTable("Which market conditions suit you", st.by_regime,
    "You may be good in trends and poor in ranges, or the reverse. Worth knowing before you widen the watchlist.")}
  ${performanceTable("Strategy × market", st.by_strategy_regime,
    "The specific combination is where the real edge (or the real leak) shows up.")}

  <div style="margin-top:var(--s5)">
    <div class="label" style="margin-bottom:12px">Recently closed</div>
    ${closedRows.length ? `<div class="card" style="padding:0"><table class="wide"><thead><tr>
      <th>Ticker</th><th>Idea</th><th>Opened</th><th class="n">Confidence</th>
      <th class="n">Exit</th><th>Outcome</th></tr></thead><tbody>
      ${closedRows.map(i => {
        const cls = i.outcome === "win" ? "ok" : i.outcome === "loss" ? "bad" : "muted";
        const g = i.outcome === "win" ? GLYPH.ok : i.outcome === "loss" ? GLYPH.fail : GLYPH.idle;
        return `<tr><td class="n">${esc(i.ticker)}</td><td class="prov">#${i.id} ${esc(i.direction || "")}</td>
          <td class="prov">${esc((i.created_at || "").slice(0, 10))}</td>
          <td class="n">${i.confidence ?? "—"}</td>
          <td class="n">${n(i.outcome_price)}</td>
          <td><span class="pill ${cls}">${g} ${esc(i.outcome)}</span></td></tr>`;
      }).join("")}</tbody></table></div>` : `<div class="callout dashed"><span class="muted">Nothing closed yet.</span></div>`}
  </div>`;
}

/* ---------- 1d / 1e: order ticket modal ---------- */
function showTicket(ticket) {
  const isLive = (S.execution?.mode || "unverified") !== "paper";
  const money0 = v => money(v);
  // esc() even though tickers are symbol-shaped: this string goes into
  // innerHTML on the one dialog in the app that ends in real money, and names
  // resolved through router.suggest() come straight from an upstream API.
  const tkr = esc(ticket.ticker);
  const title = isLive ? `Spend real money on ${tkr}?` : `Place a simulated order for ${tkr}?`;
  const lede = isLive
    ? `${money0(ticket.limit_price * ticket.quantity)} of your own cash is committed and the trade cannot be undone from here.`
    : `This is simulated money. Nothing real is at stake.`;

  $("#modalRoot").innerHTML = `
    <div class="scrim ${isLive ? "is-live" : ""}"></div>
    <div class="modal-wrap" role="dialog" aria-modal="true" aria-labelledby="tkTitle">
      <div class="modal ${isLive ? "is-live" : ""}">
        <div class="topbar"></div>
        ${isLive ? `<div class="hazard-band">LIVE MONEY</div>` : ""}
        <div class="inner">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">
            <span class="pill ${isLive ? "live" : "warn"}">
              ${isLive ? GLYPH.fail + " REAL FUNDS" : GLYPH.running + " PAPER MONEY"}
              ${S.execution?.account_id ? " · " + esc(S.execution.account_id) : ""}</span>
            <span class="prov">STEP 2 OF 2</span>
          </div>
          <h2 id="tkTitle" class="h" style="font-size:24px;margin:14px 0 6px">${esc(title)}</h2>
          <p class="prov">${esc(lede)}</p>

          <div class="ticket-grid">
            <div class="ticket-cell"><div class="stat-l">Side / quantity</div>
              <div class="num" style="text-align:right;font-variant-numeric:tabular-nums" style="font-size:18px">${esc(ticket.side.toUpperCase())} ${ticket.quantity}</div></div>
            <div class="ticket-cell"><div class="stat-l">Limit entry</div>
              <div class="num" style="text-align:right;font-variant-numeric:tabular-nums" style="font-size:18px">${n(ticket.limit_price)} ${esc(ticket.currency)}</div></div>
            <div class="ticket-cell"><div class="stat-l">Protective stop</div>
              <div class="num bad" style="font-size:18px">${n(ticket.stop_price)}</div></div>
            <div class="ticket-cell ${isLive ? "danger" : ""}"><div class="stat-l">Max loss</div>
              <div class="num" style="text-align:right;font-variant-numeric:tabular-nums" style="font-size:18px">${money0(ticket.max_loss)}</div>
              ${isLive ? `<div class="prov live" style="margin-top:3px">REAL MONEY AT RISK</div>` : ""}</div>
          </div>

          <p class="prov">Sent as a bracket: the entry plus the attached stop. An order without a stop is refused.
            ${isLive ? " Slippage past the stop is possible in a gap, so the loss can exceed this figure." : ""}</p>

          <div style="display:flex;gap:var(--s2);margin-top:var(--s4);flex-wrap:wrap;align-items:center">
            <label class="sr-only" for="tkConfirm">Type ${esc(ticket.ticker)} to confirm</label>
            <input id="tkConfirm" placeholder="type ${esc(ticket.ticker)}" size="12" autocomplete="off">
            <button class="btn ${isLive ? "btn-live" : "btn-primary"}" id="tkPlace">
              ${isLive ? `Place live order — ${money0(ticket.limit_price * ticket.quantity)}` : "Place paper order"}</button>
            <button class="btn" id="tkCancel">Cancel ticket</button>
          </div>
          <div class="prov" style="margin-top:10px" id="tkMsg">Cancelling costs nothing and keeps the idea approved.</div>
          ${isLive ? `<div class="prov" style="margin-top:8px">Automated research is not advice. You are the only party deciding to place this order.</div>` : ""}
        </div>
      </div>
    </div>`;

  $("#tkCancel").addEventListener("click", async () => {
    await post(`/api/orders/${ticket.ticket_id || ticket.id}/cancel`);
    closeModal(); fetchState();
  });
  $("#tkPlace").addEventListener("click", async () => {
    const msg = $("#tkMsg");
    msg.textContent = "Placing…";
    const r = await post(`/api/orders/${ticket.ticket_id || ticket.id}/confirm`,
      { confirmation: $("#tkConfirm").value });
    if (r.ok) { closeModal(); fetchState(); }
    else { msg.innerHTML = `<span class="bad">${GLYPH.fail} ${esc(r.data.error || "Not placed")}</span>`; }
  });
  $(".scrim").addEventListener("click", closeModal);
  $("#tkConfirm").focus();
}

function closeModal() { $("#modalRoot").innerHTML = ""; }

/* type-ENABLE friction dialog (1h) */
function enableDialog() {
  $("#modalRoot").innerHTML = `
    <div class="scrim"></div>
    <div class="modal-wrap" role="dialog" aria-modal="true" aria-labelledby="enTitle">
      <div class="modal"><div class="topbar"></div><div class="inner">
        <h2 id="enTitle" class="h" style="font-size:22px">Turn on order entry?</h2>
        <p class="prov" style="margin-top:8px">This only makes order entry possible. Every individual order
          still needs its own confirmation, and nothing is ever placed automatically.</p>
        <div class="callout warn" style="margin:var(--s4) 0">
          ${GLYPH.advisory} Your broker session decides whether orders are paper or real.
          If the connected account is a live account, real money can be spent.</div>
        <div style="display:flex;gap:var(--s2);flex-wrap:wrap">
          <label class="sr-only" for="enInput">Type ENABLE to confirm</label>
          <input id="enInput" placeholder="type ENABLE" size="12" autocomplete="off">
          <button class="btn btn-primary" id="enGo">Turn on</button>
          <button class="btn" id="enCancel">Cancel</button>
        </div>
        <div class="prov" style="margin-top:10px" id="enMsg"></div>
      </div></div></div>`;
  $("#enCancel").addEventListener("click", closeModal);
  $(".scrim").addEventListener("click", closeModal);
  $("#enGo").addEventListener("click", async () => {
    const r = await post("/api/execution/toggle", { enabled: true, confirmation: $("#enInput").value });
    if (r.ok) { closeModal(); fetchState(); }
    else $("#enMsg").innerHTML = `<span class="bad">${GLYPH.fail} ${esc(r.data.error || "failed")}</span>`;
  });
  $("#enInput").focus();
}

function legalDialog() {
  $("#modalRoot").innerHTML = `
    <div class="scrim"></div>
    <div class="modal-wrap" role="dialog" aria-modal="true" aria-labelledby="lgTitle">
      <div class="modal"><div class="topbar"></div><div class="inner">
        <h2 id="lgTitle" class="h" style="font-size:22px">Full notice</h2>
        <p style="margin-top:12px;font-size:14px">${esc(S.disclaimer || "")}</p>
        <p style="margin-top:12px;font-size:14px">Outputs are automated research summaries, not financial
          advice and not recommendations to buy or sell any security. Markets involve risk of loss.
          Data may be delayed, incomplete or wrong. Verify everything independently and make your own decisions.</p>
        <button class="btn" id="lgClose" style="margin-top:var(--s4)">Close</button>
      </div></div></div>`;
  $("#lgClose").addEventListener("click", closeModal);
  $(".scrim").addEventListener("click", closeModal);
}

/* ---------- render + events ---------- */
const TITLES = { today: "Today", watchlist: "Watchlist", ideas: "Ideas", journal: "Journal", detail: "Idea" , paper: "Book" };

function go(v) { view = v; render(); window.scrollTo(0, 0); }

function render() {
  if (S.config_error) {
    $("#viewRoot").innerHTML = `<div class="callout bad"><b>${GLYPH.fail} Configuration problem</b>
      <div style="margin-top:8px;font-size:13px">${esc(S.config_error)}</div></div>`;
    return;
  }
  renderModePlate(); renderProviders(); setNav();

  $("#viewTitle").textContent = TITLES[view] || "Today";
  const scan = S.scan_progress;
  $("#viewMeta").textContent = S.scanning && scan
    ? `Scanning ${scan.done} of ${scan.total} · ${scan.current_ticker || ""}`
    : `${(S.watchlist_symbols || []).length} tickers · ${(S.scan?.watchlist || []).filter(w => w.setup_count > 0).length} with setups · ${ideas().length} ideas`;
  $("#scanBtn").disabled = !!S.scanning;

  if (view === "paper") {
    // Rendered from its own endpoint: the book is live state, not part of the
    // scan snapshot the rest of these views read from.
    loadPaper();
    return;
  }
  const html = { today: viewToday, watchlist: viewWatchlist, ideas: viewIdeas, journal: viewJournal, detail: viewDetail }[view]();
  // The notice leads the view: a page that has deliberately stopped updating
  // must say so, or it is indistinguishable from one that has broken.
  $("#viewRoot").innerHTML = marketClosedNotice() + html;
  if (view === "today") refreshSchedule();
  bindView();
}

function bindView() {
  bindTriage();
  document.querySelectorAll("[data-goto]").forEach(b => b.addEventListener("click", () => go(b.dataset.goto)));
  document.querySelectorAll("[data-runscan]").forEach(b => b.addEventListener("click", runScan));
  document.querySelectorAll("[data-chip]").forEach(b => b.addEventListener("click", () => { ideaFilter = b.dataset.chip; render(); }));
  document.querySelectorAll("[data-sort]").forEach(b => b.addEventListener("click", () => { ideaSort = b.dataset.sort; render(); }));
  $("#ideaSearch")?.addEventListener("input", e => { ideaSearch = e.target.value; render(); $("#ideaSearch").focus(); });

  document.querySelectorAll("[data-decide]").forEach(b => b.addEventListener("click", async () => {
    await post(`/api/ideas/${openIdeaId}/decision`, { decision: b.dataset.decide });
    fetchState();
  }));
  document.querySelectorAll("[data-outcome]").forEach(b => b.addEventListener("click", async () => {
    const msg = $("#outcomeMsg");
    const r = await post(`/api/ideas/${openIdeaId}/outcome`, {
      outcome: b.dataset.outcome,
      price: $("#exitPrice")?.value.trim() || null,
      entry: $("#entryUsed")?.value.trim() || null,
    });
    if (!r.ok) {
      // A rejected exit price must say so. Silently swallowing it would leave
      // the user believing a P&L was recorded when nothing was.
      if (msg) msg.innerHTML = `<span class="bad">${GLYPH.fail} ${esc(r.data.error || "Could not save that outcome.")}</span>`;
      return;
    }
    const warnings = r.data.warnings || [];
    if (msg && warnings.length) {
      msg.innerHTML = warnings.map(w => `<div class="warn">${GLYPH.advisory} ${esc(w)}</div>`).join("");
      setTimeout(fetchState, 4000);   // let them read it before the re-render
      return;
    }
    fetchState();
  }));
  document.querySelectorAll("[data-prepare]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Re-checking risk…";
    const r = await post(`/api/ideas/${b.dataset.prepare}/prepare-order`);
    if (r.ok) showTicket(r.data);
    else {
      b.disabled = false; b.textContent = "Prepare order ticket…";
      b.insertAdjacentHTML("afterend",
        `<div class="prov bad" style="margin-top:8px">${GLYPH.fail} ${esc(r.data.error || "refused")}</div>`);
    }
  }));
  document.querySelectorAll("[data-openticket]").forEach(b => b.addEventListener("click", () => {
    const t = (S.orders || []).find(o => o.id === +b.dataset.openticket);
    if (t) showTicket(t);
  }));

  $("#cancelScan")?.addEventListener("click", async () => { await post("/api/scan/cancel"); fetchState(); });
  $("#wlAdd")?.addEventListener("click", addSymbol);
  $("#wlInput")?.addEventListener("keydown", e => {
    // Enter only submits when no suggestion is highlighted (the typeahead
    // handles Enter itself in that case).
    if (e.key === "Enter" && !e.defaultPrevented) addSymbol();
  });
  attachTypeahead($("#wlInput"), r => { addSymbol(r.symbol); });
  if ($("#uniCount")) {
    fetch("/api/symbols?q=").then(r => r.json())
      .then(d => { const el = $("#uniCount"); if (el && d.universe_size) el.textContent = ` (${d.universe_size.toLocaleString()} symbols)`; })
      .catch(() => { });
  }
  document.querySelectorAll("[data-wlremove]").forEach(b => b.addEventListener("click", async () => {
    await post("/api/watchlist/remove", { ticker: b.dataset.wlremove }); fetchState();
  }));
}

async function addSymbol(symbol) {
  const t = (symbol || $("#wlInput").value).trim();
  if (!t) return;
  $("#wlMsg").textContent = "checking…";
  const r = await post("/api/watchlist/add", { ticker: t });
  if (r.ok) {
    $("#wlInput").value = "";
    $("#wlMsg").textContent = `added ${r.data.ticker}`;
    fetchState();
  } else {
    $("#wlMsg").textContent = r.data.error || "failed";
  }
}

async function runScan() {
  $("#scanBtn").disabled = true;
  await post("/api/scan");
  setTimeout(fetchState, 800);
}

/* ---------- typeahead over the full tradable universe ----------
   Local symbol search only — costs nothing, never triggers a scan or an AI call. */
function attachTypeahead(input, onPick) {
  if (!input || input.dataset.acBound) return;
  input.dataset.acBound = "1";
  input.setAttribute("autocomplete", "off");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");

  const wrap = document.createElement("span");
  wrap.className = "ac-wrap";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const menu = document.createElement("div");
  menu.className = "ac-menu hidden";
  menu.setAttribute("role", "listbox");
  wrap.appendChild(menu);

  let items = [], sel = -1, timer = null, seq = 0;

  const close = () => {
    menu.classList.add("hidden"); input.setAttribute("aria-expanded", "false");
    items = []; sel = -1;
  };
  const paint = () => {
    menu.innerHTML = items.length
      ? items.map((r, i) => `<button class="ac-item" role="option" data-i="${i}"
          aria-selected="${i === sel}">
          <span class="sym">${esc(r.symbol)}</span>
          <span class="nm">${esc(r.name)}</span>
          <span class="ty">${esc((r.type || "").replace("Common Stock", "Stock"))}</span></button>`).join("")
      : `<div class="ac-empty">No matching symbol.</div>`;
    menu.classList.remove("hidden");
    input.setAttribute("aria-expanded", "true");
    menu.querySelectorAll(".ac-item").forEach(b =>
      b.addEventListener("mousedown", e => { e.preventDefault(); pick(+b.dataset.i); }));
  };
  const pick = i => {
    const r = items[i]; if (!r) return;
    input.value = r.symbol; close();
    if (onPick) onPick(r);
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 1) return close();
    // debounce: don't fire a request on every keystroke
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const r = await fetch("/api/symbols?q=" + encodeURIComponent(q));
        const data = await r.json();
        if (mine !== seq) return;           // a newer keystroke already won
        items = data.results || []; sel = -1; paint();
      } catch (_) { close(); }
    }, 160);
  });

  input.addEventListener("keydown", e => {
    if (menu.classList.contains("hidden")) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = Math.max(0, Math.min(items.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1)));
      paint();
      menu.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && sel >= 0) {
      e.preventDefault(); pick(sel);
    } else if (e.key === "Escape") { close(); }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
}

/* ---------- global chrome events ---------- */
document.querySelectorAll("[data-view]").forEach(b =>
  b.addEventListener("click", () => go(b.dataset.view)));
$("#scanBtn").addEventListener("click", runScan);
$("#legalLink").addEventListener("click", e => { e.preventDefault(); legalDialog(); });
$("#analyzeBtn").addEventListener("click", analyze);
$("#tickerInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.defaultPrevented) analyze();
});
attachTypeahead($("#tickerInput"));   // pick fills the box; you still press Analyze

async function analyze() {
  const t = $("#tickerInput").value.trim();
  if (!t) return;
  const meta = $("#viewMeta");
  meta.textContent = `Analyzing ${t.toUpperCase()}… (news, fundamentals and thesis take a minute)`;
  const r = await post("/api/analyze/" + encodeURIComponent(t));
  if (r.ok) {
    $("#tickerInput").value = "";
    openIdeaId = r.data.idea_id;
    await fetchState();
    go("detail");
  } else {
    meta.innerHTML = `<span class="bad">${GLYPH.fail} ${esc(r.data.error || "failed")}</span>`;
  }
}

$("#themeBtn").addEventListener("click", () => {
  const light = document.documentElement.dataset.theme === "light";
  document.documentElement.dataset.theme = light ? "dark" : "light";
  $("#themeBtn").textContent = light ? "Light mode" : "Dark mode";
  try { localStorage.setItem("ta-theme", document.documentElement.dataset.theme); } catch (_) { }
});
try {
  const saved = localStorage.getItem("ta-theme");
  if (saved) {
    document.documentElement.dataset.theme = saved;
    $("#themeBtn").textContent = saved === "light" ? "Dark mode" : "Light mode";
  }
} catch (_) { }

/* keyboard: j/k move, Enter opens, a/r/m decide. Order confirm has NO shortcut. */
let cursor = 0;
document.addEventListener("keydown", e => {
  if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.key === "Escape") return closeModal();
  const rows = [...document.querySelectorAll("[data-idea]")];
  if (e.key === "j" || e.key === "k") {
    if (!rows.length) return;
    cursor = Math.max(0, Math.min(rows.length - 1, cursor + (e.key === "j" ? 1 : -1)));
    rows[cursor].focus();
    e.preventDefault();
  } else if (e.key === "Enter" && document.activeElement?.dataset.idea) {
    openIdeaId = +document.activeElement.dataset.idea; go("detail");
  } else if (view === "detail" && "arm".includes(e.key) && openIdeaId) {
    const map = { a: "approved", r: "rejected", m: "needs_research" };
    post(`/api/ideas/${openIdeaId}/decision`, { decision: map[e.key] }).then(fetchState);
  }
});

fetchState();


/* ============================ PAPER BOOK ============================
   The rules trading forward with nobody intervening — separate from the
   journal, which records ideas a human decided on. Kept apart deliberately:
   mixed together you could no longer tell a good month from good judgement. */

/* ---------- the automatic trading day ----------
   Scheduling lives in the dashboard PROCESS because macOS refuses cron and
   launchd any access to ~/Desktop. That trade-off has to be visible: this
   only runs while the dashboard is running, and a panel that quietly says
   nothing would hide exactly that. */

async function refreshSchedule() {
  const host = document.getElementById("scheduleCard");
  if (!host) return;
  let d;
  try { d = await (await fetch("/api/schedule")).json(); }
  catch (e) { host.innerHTML = '<div class="label">Automatic runs</div><div class="prov">Unavailable.</div>'; return; }

  const when = iso => {
    if (!iso) return "—";
    const t = new Date(iso);
    return t.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  };
  const jobs = (d.jobs || []).slice().sort((a, b) => a.next_fire < b.next_fire ? -1 : 1);
  const next = jobs[0];

  host.innerHTML = `
    <div class="label">Automatic runs</div>
    ${d.running
      ? `<div class="callout warn" style="margin-top:8px"><b>${GLYPH.running} Running now — ${esc(d.running)}</b>
           <div class="prov" style="margin-top:4px">A full session walks every holding, so this takes a while.</div></div>`
      : `<div style="margin-top:8px;font-size:13px">Next: <b>${esc(next ? next.mode : "—")}</b>
           at ${esc(when(next && next.next_fire))}
           <div class="prov">${esc(next ? next.why : "")}</div></div>`}
    <div style="margin-top:10px">
      ${jobs.map(j => `<div class="prov">${esc(j.at)} · ${esc(j.mode)} — ${esc(when(j.next_fire))}</div>`).join("")}
    </div>
    ${(d.last || []).length ? `<div style="margin-top:12px;border-top:1px solid var(--line);padding-top:8px">
      <div class="label" style="margin-bottom:4px">Recent</div>
      ${d.last.slice(0, 3).map(r => `<div class="prov">
        <span class="${r.ok ? "ok" : "bad"}">${r.ok ? GLYPH.ok : GLYPH.fail}</span>
        ${esc(r.mode)} · ${esc(when(r.finished_at))} · ${r.seconds}s
        <div style="opacity:.75">${esc((r.detail || "").slice(0, 110))}</div>
      </div>`).join("")}
    </div>` : ""}
    <button class="btn btn-sm" style="margin-top:10px;width:100%"
      onclick="runScheduledNow('both')" ${d.running ? "disabled" : ""}>Run now</button>
    <div class="prov" style="margin-top:8px">Runs only while this dashboard is open —
      macOS blocks cron and launchd from reaching this folder.</div>`;
}

async function runScheduledNow(mode) {
  try {
    await fetch("/api/schedule/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  } catch (e) { /* status panel reports the outcome */ }
  setTimeout(refreshSchedule, 800);
}

/* ---------- daily P&L and the trade journal ----------
   Two questions the book could not answer: what did today make, and why did
   each trade do what it did. Both are derived from what the strategy recorded
   before the outcome was known — nothing here is written after the fact. */

function dailyCard(J) {
  const rows = (J.daily || []);
  const money = v => (v == null ? "—" : Number(v).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const sign = v => (v > 0 ? "up" : v < 0 ? "down" : "");
  const plus = v => (v > 0 ? "+" : "");

  if (!rows.length) {
    return `<div class="card"><div class="label">Daily P&amp;L</div>
      <p class="meta">No days recorded yet. A row is written at the end of every
        session.</p></div>`;
  }
  const body = rows.map(d => `
    <tr style="border-bottom:1px solid rgba(128,128,128,.18)">
      <td><b>${esc(d.date)}</b></td>
      <td class="num ${sign(d.change)}" style="text-align:right;font-variant-numeric:tabular-nums">
        <b>${plus(d.change)}${money(d.change)}</b>
        <div class="meta" style="font-size:11px">${plus(d.change_pct)}${Number(d.change_pct || 0).toFixed(3)}%</div></td>
      <td class="num ${sign(d.realised)}" style="text-align:right;font-variant-numeric:tabular-nums">${plus(d.realised)}${money(d.realised)}</td>
      <td class="num ${sign(d.unrealised_change)}" style="text-align:right;font-variant-numeric:tabular-nums">${plus(d.unrealised_change)}${money(d.unrealised_change)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(d.costs)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(d.equity)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${d.opened ?? 0} / ${d.closed ?? 0}</td>
      <td class="meta">${esc(d.price_source || "—")}</td>
    </tr>`).join("");

  return `<div class="card">
    <div class="label">Daily P&amp;L</div>
    <p class="meta" style="margin:6px 0 12px">Realised is money banked by trades that
      closed that day. Unrealised is everything else the day did — the drift on
      positions still open. A day can be up while every trade it closed lost money,
      which is why the two are never added together here.</p>
    <table class="tbl" style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="text-align:left;opacity:.6">
        <th>Date</th>
        <th class="num" style="text-align:right">Day P&amp;L</th>
        <th class="num" style="text-align:right">Realised</th>
        <th class="num" style="text-align:right">Unrealised</th>
        <th class="num" style="text-align:right">Costs</th>
        <th class="num" style="text-align:right">Equity</th>
        <th class="num" style="text-align:right">Opened/Closed</th>
        <th>Priced by</th>
      </tr></thead><tbody>${body}</tbody></table>
  </div>`;
}

function breakdownCard(J) {
  const b = J.breakdown || {};
  const t = J.totals || {};
  const money = v => (v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
  const sign = v => (v > 0 ? "up" : v < 0 ? "down" : "");

  const table = (title, rows, hint) => `
    <div style="flex:1;min-width:260px">
      <div class="label" style="margin-bottom:4px">${title}</div>
      <div class="prov" style="margin-bottom:6px">${hint}</div>
      <table class="tbl" style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="text-align:left;opacity:.6"><th></th>
          <th class="num" style="text-align:right">Trades</th>
          <th class="num" style="text-align:right">Win%</th>
          <th class="num" style="text-align:right">P&amp;L</th>
          <th class="num" style="text-align:right">Avg R</th></tr></thead>
        <tbody>${(rows || []).map(r => `<tr>
          <td>${esc(r.key)}</td>
          <td class="num" style="text-align:right">${r.trades}</td>
          <td class="num" style="text-align:right">${r.win_rate_pct == null ? "—" : r.win_rate_pct + "%"}</td>
          <td class="num ${sign(r.pnl)}" style="text-align:right">${money(r.pnl)}</td>
          <td class="num" style="text-align:right">${r.avg_r == null ? "—" : r.avg_r}</td>
        </tr>`).join("") || '<tr><td colspan="5" class="meta">No closed trades.</td></tr>'}</tbody>
      </table>
    </div>`;

  return `<div class="card">
    <div class="label">Where the money is made and lost</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;margin:12px 0 18px">
      <div><div class="label">Closed</div><b>${t.closed ?? 0}</b></div>
      <div><div class="label">Realised</div><b class="${sign(t.realised_pnl)}">${money(t.realised_pnl)}</b></div>
      <div><div class="label">Win rate</div><b>${t.win_rate_pct == null ? "—" : t.win_rate_pct + "%"}</b></div>
      <div><div class="label">Avg win</div><b class="up">${money(t.avg_win)}</b></div>
      <div><div class="label">Avg loss</div><b class="down">${money(t.avg_loss)}</b></div>
      <div><div class="label">Profit factor</div><b>${t.profit_factor ?? "—"}</b></div>
    </div>
    ${(J.observations || []).map(o => `<div class="callout dashed" style="margin-bottom:8px"><span class="meta">${esc(o)}</span></div>`).join("")}
    <div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:14px">
      ${table("By strategy", b.by_strategy, "Which rule set is paying")}
      ${table("By market regime", b.by_regime, "Conditions the rules suit")}
    </div>
    <div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:18px">
      ${table("By exit", b.by_exit, "Stop, target, or the holding limit")}
      ${table("By segment", b.by_segment, "Asset class")}
    </div>
  </div>`;
}

function journalCard(J) {
  const entries = J.entries || [];
  if (!entries.length) {
    return `<div class="card">
      <div class="label">Trade journal</div>
      <p class="meta">No trades have closed yet. Each closed trade gets an entry here
        recording why it was opened — quoted from before the outcome was known — and
        why it made or lost money.</p></div>`;
  }
  const money = v => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
  return `<div class="card">
    <div class="label">Trade journal</div>
    <p class="meta" style="margin:6px 0 14px">Entry reasons are quoted from what the
      strategy recorded before the trade was opened. Outcomes are mechanical — which
      level was touched. Nothing here is narrated after the fact.</p>
    ${entries.map(e => `
      <div style="border-top:1px solid var(--line);padding:14px 0">
        <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
          <b style="font-size:16px">${esc(e.ticker)}</b>
          <span class="pill">${esc((e.direction || "").toUpperCase())}</span>
          <span class="pill">${esc(e.strategy || "")}</span>
          <b class="${e.won ? "up" : "down"}" style="margin-left:auto;font-size:15px">
            ${e.pnl > 0 ? "+" : ""}${money(e.pnl)}${e.r_multiple == null ? "" : ` · ${e.r_multiple}R`}</b>
        </div>
        <div class="prov" style="margin:4px 0 10px">
          ${esc(e.entry_date || "")} → ${esc(e.exit_date || "")} ·
          ${esc(e.outcome_title)} · priced by ${esc(e.price_source || "—")}</div>
        <div style="display:flex;gap:22px;flex-wrap:wrap">
          <div style="flex:1;min-width:250px">
            <div class="label" style="margin-bottom:4px">Why it was opened</div>
            <ul style="padding-left:16px">${(e.why_entered || []).map(x =>
              `<li style="font-size:12.5px;margin-bottom:4px">${esc(x)}</li>`).join("")}</ul>
            ${(e.measurements || []).length ? `<div class="prov" style="margin-top:6px">
              ${e.measurements.map(esc).join(" · ")}</div>` : ""}
          </div>
          <div style="flex:1;min-width:250px">
            <div class="label" style="margin-bottom:4px">Why this outcome</div>
            <ul style="padding-left:16px">${(e.why_this_outcome || []).map(x =>
              `<li style="font-size:12.5px;margin-bottom:4px">${esc(x)}</li>`).join("")}</ul>
          </div>
        </div>
      </div>`).join("")}
  </div>`;
}

/* ---------- archived books ----------
   A reset archives rather than deletes, so a track record cannot be quietly
   restarted after a bad run. That guarantee only means something if the old
   books can actually be opened — an archive nobody can read is
   indistinguishable from a deletion. */

async function toggleArchives() {
  const host = document.getElementById("archiveRoot");
  if (!host) return;
  if (host.dataset.open === "1") {
    host.dataset.open = "0";
    host.innerHTML = "";
    return;
  }
  host.dataset.open = "1";
  host.innerHTML = '<div class="card"><div class="label">Loading archived books…</div></div>';
  try {
    const d = await (await fetch("/api/paper/archives")).json();
    host.innerHTML = archivePanel(d);
  } catch (e) {
    host.innerHTML = '<div class="card"><b class="bad">Could not load archives.</b></div>';
  }
}

function archivePanel(d) {
  const money = v => (v == null ? "—" : Number(v).toLocaleString(undefined,
    { maximumFractionDigits: 0 }));
  const pct = v => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "%");
  const cls = v => (v > 0 ? "up" : v < 0 ? "down" : "");

  if (!d.count) {
    return `<div class="card">
      <div class="label">Archived books</div>
      <p class="meta">None yet. <code>python run.py paper --reset</code> archives the
        current book before starting a new one — it never deletes.</p>
    </div>`;
  }

  const rows = d.archives.map(a => a.error
    ? `<tr><td colspan="8" class="bad">${esc(a.file)} — ${esc(a.error)}</td></tr>`
    : `<tr style="border-bottom:1px solid rgba(128,128,128,.18)">
        <td><b>${esc((a.archived_at || "").replace("T", " ").replace("Z", ""))}</b>
            <div class="meta" style="font-size:11px">${esc(a.file)}</div></td>
        <td class="meta">${esc((a.started_at || "").slice(0, 10))}</td>
        <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${a.days ?? "—"}</td>
        <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(a.starting_equity)}</td>
        <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(a.equity)}</td>
        <td class="num ${cls(a.return_pct)}" style="text-align:right;font-variant-numeric:tabular-nums">${pct(a.return_pct)}</td>
        <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${a.open_positions ?? "—"} / ${a.closed_trades ?? "—"}</td>
        <td><button class="btn btn-sm" onclick="openArchive('${esc(a.file)}')">Open</button></td>
      </tr>`).join("");

  return `<div class="card">
    <div class="label">Archived books — ${d.count}</div>
    <p class="meta" style="margin:6px 0 12px">Every reset is kept. Nothing here can be
      overwritten by a later run, which is what makes the live book's history worth
      anything.</p>
    <table class="tbl" style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="text-align:left;opacity:.6">
        <th>Archived</th><th>Started</th>
        <th class="num" style="text-align:right">Sessions</th>
        <th class="num" style="text-align:right">Start</th>
        <th class="num" style="text-align:right">End</th>
        <th class="num" style="text-align:right">Return</th>
        <th class="num" style="text-align:right">Open/Closed</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table>
    <div id="archiveDetail"></div>
  </div>`;
}

async function openArchive(file) {
  const host = document.getElementById("archiveDetail");
  if (!host) return;
  host.innerHTML = '<div class="meta" style="margin-top:14px">Loading…</div>';
  try {
    const d = await (await fetch("/api/paper/archives/" + encodeURIComponent(file))).json();
    if (d.error) { host.innerHTML = `<div class="bad">${esc(d.error)}</div>`; return; }
    const s = d.summary || {};
    const pos = (d.positions || []).map(p => `<tr>
        <td><b>${esc(p.ticker)}</b></td><td>${esc(p.strategy || "")}</td>
        <td class="num" style="text-align:right">${Number(p.shares).toLocaleString()}</td>
        <td class="num" style="text-align:right">${Number(p.entry_price).toFixed(2)}</td>
        <td class="num" style="text-align:right">${Number(p.last_price).toFixed(2)}</td>
        <td class="meta">${esc(p.entry_date || "")}</td>
        <td class="meta">${esc(p.price_source || "—")}</td>
      </tr>`).join("") || '<tr><td colspan="7" class="meta">No open positions.</td></tr>';
    const closed = (d.closed || []).map(c => `<tr>
        <td><b>${esc(c.ticker)}</b></td><td>${esc(c.exit_reason || "")}</td>
        <td class="num" style="text-align:right">${Number(c.pnl || 0).toFixed(2)}</td>
        <td class="num" style="text-align:right">${c.r_multiple == null ? "—" : c.r_multiple + "R"}</td>
        <td class="meta">${esc(c.entry_date || "")} → ${esc(c.exit_date || "")}</td>
      </tr>`).join("") || '<tr><td colspan="5" class="meta">Nothing closed.</td></tr>';

    host.innerHTML = `<div style="margin-top:18px;border-top:1px solid var(--line);padding-top:14px">
      <div class="label">${esc(file)}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px;margin:12px 0">
        <div><div class="label">Equity</div><b>${Number(s.equity || 0).toLocaleString()} ${esc(s.base_currency || "")}</b></div>
        <div><div class="label">Return</div><b>${s.return_pct == null ? "—" : s.return_pct + "%"}</b></div>
        <div><div class="label">Sessions</div><b>${s.days ?? "—"}</b></div>
        <div><div class="label">Max DD</div><b>${s.max_drawdown_pct ?? "—"}%</b></div>
        <div><div class="label">Priced by</div><b>${esc((d.price_sources || []).join(", ") || "—")}</b></div>
      </div>
      <div class="label" style="margin-top:10px">Open positions at archive</div>
      <table class="tbl" style="width:100%;border-collapse:collapse;font-size:12px"><thead>
        <tr style="text-align:left;opacity:.6"><th>Instrument</th><th>Strategy</th>
        <th class="num" style="text-align:right">Units</th>
        <th class="num" style="text-align:right">Entry</th>
        <th class="num" style="text-align:right">Last</th><th>Opened</th><th>Source</th></tr>
      </thead><tbody>${pos}</tbody></table>
      <div class="label" style="margin-top:14px">Closed trades</div>
      <table class="tbl" style="width:100%;border-collapse:collapse;font-size:12px"><thead>
        <tr style="text-align:left;opacity:.6"><th>Instrument</th><th>Exit</th>
        <th class="num" style="text-align:right">P&amp;L</th>
        <th class="num" style="text-align:right">R</th><th>Held</th></tr>
      </thead><tbody>${closed}</tbody></table>
      <div class="label" style="margin-top:14px">Session log</div>
      ${(d.sessions || []).map(x => `<div class="meta">${esc(x.date)} — ${esc(x.note || "")}</div>`).join("")
        || '<div class="meta">No sessions.</div>'}
    </div>`;
  } catch (e) {
    host.innerHTML = '<div class="bad">Could not open that archive.</div>';
  }
}

/* ---------- live P&L ----------
   The book's own marks are daily closes and must not move when a page
   refreshes — they are the record of what the rules achieved. This panel
   answers the other question, "what is it worth right now", and is a view
   only: nothing here is written back to the book. */

let livePoll = null;

function livePanel(L) {
  if (!L || !L.started) return "";
  if (!L.available) {
    return `<div class="card">
      <div class="label">Live P&amp;L</div>
      <p class="meta">${esc(L.note || "Live prices are unavailable right now.")}</p>
    </div>`;
  }
  const s = L.summary || {};
  const sign = v => (v > 0 ? "up" : v < 0 ? "down" : "");
  const money = v => (v == null ? "—" : Number(v).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

  const breach = (L.breaches || []).length ? `
    <div class="callout warn" style="margin-top:12px">
      <b>${L.breaches.length} position(s) have reached an exit level right now:</b>
      ${L.breaches.map(b => `<div>${esc(b.ticker)} — through its ${esc(b.kind)}
        at ${money(b.level)} (live ${money(b.live_price)})</div>`).join("")}
      <div class="prov" style="margin-top:6px">The book takes exits on DAILY bars,
        so these will not be actioned until a session runs after the close.</div>
    </div>` : "";

  return `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
      <div>
        <div class="label">Live P&amp;L — today</div>
        <div style="font-size:1.9rem;font-variant-numeric:tabular-nums" class="${sign(s.today_pnl)}">
          ${s.today_pnl > 0 ? "+" : ""}${money(s.today_pnl)}
          <span class="meta">${esc(s.base_currency || "")}</span>
          <span style="font-size:1rem">${s.today_pct > 0 ? "+" : ""}${Number(s.today_pct || 0).toFixed(3)}%</span>
        </div>
      </div>
      <button class="btn btn-sm" onclick="refreshLive(true)">Refresh now</button>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:16px 24px;margin-top:16px">
      <div><div class="label">Equity now</div><b>${money(s.equity)}</b></div>
      <div><div class="label">Open P&amp;L</div>
        <b class="${sign(s.open_pnl)}">${s.open_pnl > 0 ? "+" : ""}${money(s.open_pnl)}</b></div>
      <div><div class="label">Cash</div><b>${money(s.cash)}</b></div>
      <div><div class="label">Gross notional</div><b>${money(s.gross_exposure)}</b></div>
      <div><div class="label">Priced live</div><b>${L.priced}/${L.priced + L.unpriced}</b></div>
    </div>
    ${breach}
    <div class="prov" style="margin-top:12px">${esc(L.delay_note || "")}
      Updated ${esc((L.as_of || "").replace("T", " ").replace("+00:00", " UTC"))}.</div>
  </div>`;
}

async function refreshLive(force) {
  const host = document.getElementById("liveRoot");
  if (!host) return;
  try {
    const L = await (await fetch("/api/paper/live" + (force ? "?force=1" : ""))).json();
    host.innerHTML = livePanel(L);
    // Live price and today's move, written onto the rows already on screen, so
    // the table does not have to be rebuilt underneath the reader.
    (L.positions || []).forEach(p => {
      const cell = document.querySelector(`[data-live="${p.ticker}"]`);
      if (!cell) return;
      const cls = p.today_pct > 0 ? "up" : p.today_pct < 0 ? "down" : "";
      cell.innerHTML = p.has_live
        ? `<b class="${cls}">${Number(p.live_price).toFixed(2)}</b>
           <div class="meta" style="font-size:11px">${p.today_pct > 0 ? "+" : ""}${Number(p.today_pct).toFixed(2)}% today</div>`
        : '<span class="meta">—</span>';
    });
  } catch (e) { /* the book stays on screen without the overlay */ }
}

function startLivePolling() {
  if (livePoll) { clearInterval(livePoll); livePoll = null; }

  // Never poll a closed market. Every refresh opens a socket to the broker and
  // walks every holding, and outside trading hours it returns the same closing
  // bar every time — so the cost is real and the information gained is zero.
  if (S.any_market_open === false) return;

  // Bars are five minutes wide and arrive ~10-15 min late, so polling faster
  // than this would just re-fetch the same bar and reopen a socket to do it.
  livePoll = setInterval(() => {
    if (view !== "paper" || S.any_market_open === false) {
      clearInterval(livePoll); livePoll = null; return;
    }
    refreshLive(false);
  }, 60000);
}

/* Verdict as a coloured word. SELL is red because it is the one that needs
   acting on today; HOLD is deliberately quiet so a book of holds reads as calm
   rather than as a wall of alerts. */
const VERDICT_CLS = { BUY: 'up', SELL: 'down', HOLD: '', WAIT: 'warn', AVOID: 'muted' };

function verdictPill(v) {
  const why = [v.headline, ...(v.because || [])].filter(Boolean).join(' — ');
  return `<b class="${VERDICT_CLS[v.action] || ''}" title="${esc(why)}">${v.action}</b>`;
}

/* One line at the top of the book saying whether the numbers below can be
   trusted right now. Only ever ONE line: a row of warnings gets read as
   decoration, and the point is that this one is not. */
function bookBanner(d) {
  const b = d.banner || {};
  const tone = b.level === 'error' ? 'down' : b.level === 'warn' ? 'warn' : 'up';
  const mkt = Object.entries(d.markets || {})
    .map(([k, m]) => `${k} ${m.open ? 'OPEN' : 'closed'}${m.open ? '' : ' · last ' + m.last_session}`)
    .join('  ·  ');
  const src = (d.price_sources || []).join(', ') || 'unknown';
  return `<div class="card banner is-${esc(b.level || 'ok')}">
    <div><b class="${tone}">${esc(b.message || '')}</b></div>
    <div class="meta" style="margin-top:6px">
      Priced by <b>${esc(src)}</b>${d.licensed ? ' (licensed)' : ''}
      · book last marked ${d.as_of ? esc(String(d.as_of).replace('T', ' ').replace('+00:00', ' UTC')) : '—'}
    </div>
    <div class="meta">${esc(mkt)}</div>
  </div>`;
}

/* What the licensed feed can price, and what a subscription would unlock.
   Rendered from the last measured report rather than probed on page load —
   the probe takes about a second per instrument. */
function coverageCard(c) {
  if (!c || !c.available) {
    return `<div class="card">
      <div class="label">Tradable coverage</div>
      <p class="meta">Not measured yet. Until it is, instruments IBKR cannot price
        may be quietly priced by the yfinance fallback instead.</p>
      <button class="btn btn-sm" onclick="refreshCoverage()">Measure coverage</button>
    </div>`;
  }
  const n = c.counts || {};
  const groups = (c.groups || []).map(g => `
    <div style="margin-top:10px">
      <div><b class="warn">${esc(g.bundle)}</b> <span class="meta">— ${g.count} instrument(s)</span></div>
      <div class="meta" style="margin:2px 0">${g.symbols.map(esc).join(', ')}</div>
      <div class="prov">${esc(g.action)}</div>
    </div>`).join('');

  return `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
      <div class="label">Tradable coverage</div>
      <button class="btn btn-sm" onclick="refreshCoverage()">Re-measure</button>
    </div>
    <div style="margin-top:8px">
      <b class="ok" style="font-size:1.3rem">${n.tradable ?? '—'}</b>
      <span class="meta">of ${n.total ?? '—'} priced by the licensed IBKR feed</span>
    </div>
    ${groups || '<div class="meta" style="margin-top:8px">Everything on the watchlist is priced by the licensed feed.</div>'}
    <div class="prov" style="margin-top:12px">Measured ${esc(c.checked_at || '—')} · ${c.age_hours ?? '—'}h ago</div>
  </div>`;
}

async function refreshCoverage() {
  const root = document.getElementById('coverageRoot');
  if (root) root.innerHTML = '<div class="card"><div class="label">Measuring coverage…</div>'
    + '<p class="meta">About a second per instrument. This runs against the account '
    + 'logged into IB Gateway right now.</p></div>';
  try {
    const r = await fetch('/api/coverage/refresh', { method: 'POST' });
    const c = await r.json();
    if (root) root.innerHTML = c.error
      ? `<div class="card"><b class="bad">Could not measure coverage.</b><p class="meta">${esc(c.error)}</p></div>`
      : coverageCard(c);
  } catch (e) {
    if (root) root.innerHTML = '<div class="card"><b class="bad">Could not measure coverage.</b></div>';
  }
}

async function loadPaper() {
  const root = document.getElementById('viewRoot');
  root.innerHTML = '<div class="card"><div class="label">Loading the book…</div></div>';
  let d, V = {}, COV = null;
  try {
    d = await (await fetch('/api/paper')).json();
  } catch (e) {
    root.innerHTML = '<div class="card"><b>Could not load the book.</b></div>';
    return;
  }
  try { COV = await (await fetch('/api/coverage')).json(); } catch (e) { /* optional */ }
  let J = null;
  try { J = await (await fetch('/api/paper/journal')).json(); } catch (e) { /* optional */ }
  // Hold-or-close for each open position. Read separately and tolerated when
  // it fails: a verdict is an opinion about the book, and losing the opinion
  // must never take the book itself off the screen.
  try {
    const vd = await (await fetch('/api/paper/verdicts')).json();
    (vd.verdicts || []).forEach(v => { V[v.ticker] = v; });
  } catch (e) { /* positions still render without it */ }

  if (!d.started) {
    root.innerHTML = `${marketClosedNotice()}
    <div class="card">
      <b>No paper book yet.</b>
      <p class="meta">${d.message || ''}</p>
      <button class="btn btn-primary" onclick="runPaper(true)">Start trading</button>
      <button class="btn" onclick="toggleArchives()">Archived books</button>
    </div>
    <div id="archiveRoot" data-open="0"></div>
    <div id="coverageRoot">${coverageCard(COV)}</div>`;
    return;
  }

  const s = d.summary, ccy = s.base_currency;
  const money = (v) => (v == null ? '—' : Number(v).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 }));
  const pct = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%');
  const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');

  const rows = d.positions.map(p => {
    const v = V[p.ticker];
    // A price that is not current, or not from the licensed feed, is marked at
    // the number itself rather than in a footnote. The whole failure this view
    // had was a figure that looked authoritative with its provenance elsewhere.
    const srcWarn = p.price_source && p.price_source !== 'ibkr';
    const priceTitle = `${p.freshness || ''}${p.price_source ? ' · via ' + p.price_source : ''}`;
    return `
    <tr style="border-bottom:1px solid rgba(128,128,128,.18)">
      <td><b>${p.ticker}</b><div class="meta">${p.segment}</div></td>
      <td>${p.strategy}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(p.shares)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${Number(p.entry_price).toFixed(2)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums" title="${priceTitle}">
        ${Number(p.last_price).toFixed(2)}${p.mark_failed || !p.current ? ' <span class="warn">⚠</span>' : ''}
        <div class="meta" style="font-size:11px">${p.bar_date || '—'}${srcWarn ? ' · ' + p.price_source : ''}</div>
      </td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums"
          data-live="${esc(p.ticker)}"><span class="meta">…</span></td>
      <td class="num ${cls(p.move_pct)}" style="text-align:right;font-variant-numeric:tabular-nums">${pct(p.move_pct)}</td>
      <td class="num ${cls(p.unrealised)}" style="text-align:right;font-variant-numeric:tabular-nums">${money(p.unrealised)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${money(p.notional)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${Number(p.stop).toFixed(2)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${p.bars_held}</td>
      <td>${v ? verdictPill(v) : '—'}</td>
      <td><button class="btn btn-sm" onclick="showNews('${p.ticker}')">News</button></td>
    </tr>`;
  }).join('');

  const closed = (d.closed || []).map(c => `
    <tr style="border-bottom:1px solid rgba(128,128,128,.18)">
      <td><b>${c.ticker}</b></td><td>${c.strategy || ''}</td>
      <td>${c.exit_reason || ''}</td>
      <td class="num ${cls(c.pnl)}" style="text-align:right;font-variant-numeric:tabular-nums">${money(c.pnl)}</td>
      <td class="num" style="text-align:right;font-variant-numeric:tabular-nums">${c.r_multiple == null ? '—' : c.r_multiple + 'R'}</td>
      <td class="meta">${c.entry_date} → ${c.exit_date}</td>
    </tr>`).join('') || '<tr><td colspan="6" class="meta">Nothing closed yet.</td></tr>';

  root.innerHTML = `
    ${marketClosedNotice()}
    ${bookBanner(d)}
    <div id="liveRoot"></div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
        <div>
          <div class="label">Equity</div>
          <div style="font-size:1.9rem;font-variant-numeric:tabular-nums">
            ${money(s.equity)} <span class="meta">${ccy}</span>
            <span class="${cls(s.return_pct)}" style="font-size:1rem">${pct(s.return_pct)}</span>
          </div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="runPaper(false)">Run session</button>
          <button class="btn btn-primary" onclick="runPaper(true)">Force rebalance</button>
          <button class="btn" onclick="toggleArchives()">Archived books</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:18px 26px;margin-top:18px">
        <div><div class="label">Cash</div><b>${money(s.cash)}</b></div>
        <div><div class="label">Exposure</div><b>${s.exposure_pct}%</b></div>
        <div><div class="label">Gross notional</div><b>${money(d.gross_exposure)}</b></div>
        <div><div class="label">Open</div><b>${s.open_positions}</b></div>
        <div><div class="label">Closed</div><b>${s.closed_trades}</b></div>
        <div><div class="label">Win rate</div><b>${s.win_rate_pct == null ? '—' : s.win_rate_pct + '%'}</b></div>
        <div><div class="label">Max drawdown</div><b>${s.max_drawdown_pct}%</b></div>
        <div><div class="label">Sessions</div><b>${s.days}</b></div>
      </div>
    </div>

    <div class="card">
      <div class="label">Open positions</div>
      <table class="tbl" style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;opacity:.6">
        <th>Instrument</th><th>Strategy</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Units</th>
        <th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Entry</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Session</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Live</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Move</th>
        <th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Unrealised</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Notional</th>
        <th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Stop</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">Days</th><th>Verdict</th><th></th>
      </tr></thead><tbody>${rows || '<tr><td colspan="13" class="meta">No open positions.</td></tr>'}</tbody></table>
      <div id="newsPanel"></div>
    </div>

    <div class="card">
      <div class="label">Recently closed</div>
      <table class="tbl" style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;opacity:.6">
        <th>Instrument</th><th>Strategy</th><th>Exit</th>
        <th class="num" style="text-align:right;font-variant-numeric:tabular-nums">P&L</th><th class="num" style="text-align:right;font-variant-numeric:tabular-nums">R</th><th>Held</th>
      </tr></thead><tbody>${closed}</tbody></table>
    </div>

    <div class="card">
      <div class="label">Session log</div>
      ${(d.sessions || []).map(x => {
        // These fields were read here from the start and never written, so
        // every row rendered as a bare note. The feed name matters most: it is
        // what says whether a session's numbers are licensed.
        const bits = [];
        if (x.price_source) bits.push(`priced by <b>${esc(x.price_source)}</b>`);
        if (x.universe != null) bits.push(`${x.universe} instruments`);
        if (x.candidates != null) bits.push(`${x.candidates} candidates`);
        if (x.opened != null || x.closed != null)
          bits.push(`${x.opened ?? 0} opened / ${x.closed ?? 0} closed`);
        return `<div style="margin-bottom:7px">
          <div class="meta">${esc(x.date)}${x.rebalanced ? ' · <b>REBALANCE</b>' : ''} — ${esc(x.note || '')}</div>
          ${bits.length ? `<div class="prov">${bits.join(' · ')}</div>` : ''}
        </div>`;
      }).join('') || '<div class="meta">No sessions recorded.</div>'}
    </div>

    ${J ? dailyCard(J) : ''}
    ${J ? breakdownCard(J) : ''}
    ${J ? journalCard(J) : ''}

    <div id="archiveRoot" data-open="0"></div>
    <div id="coverageRoot">${coverageCard(COV)}</div>`;

  const badge = document.getElementById('nb-paper');
  if (badge) badge.textContent = s.open_positions || '';

  // Painted after the table exists, then kept warm on a timer while a market is
  // trading. With everything closed the live panel would repeat the closing
  // marks already in the table, at the cost of a broker socket and a walk
  // through every holding — so it is not fetched at all.
  if (S.any_market_open === false) {
    const host = document.getElementById("liveRoot");
    if (host) host.innerHTML = `<div class="card">
      <div class="label">Live P&amp;L</div>
      <p class="meta">Markets are closed. The prices in the table are the last
        close and cannot move until the next session, so nothing is being
        fetched and nothing is refreshing.
        <button class="btn btn-sm" style="margin-left:6px" onclick="refreshLive(true)">Fetch anyway</button>
      </p>
    </div>`;
    document.querySelectorAll("[data-live]").forEach(cell => {
      cell.innerHTML = '<span class="meta">—</span>';
    });
  } else {
    refreshLive(false);
  }
  startLivePolling();
}

async function runPaper(rebalance) {
  const root = document.getElementById('viewRoot');
  root.insertAdjacentHTML('afterbegin',
    '<div class="card" id="paperBusy"><b>Running a session…</b>' +
    '<p class="meta">A rebalance re-ranks the whole universe and can take a few ' +
    'minutes on IBKR data. Nothing is ordered — there is no broker in this path.</p></div>');
  try {
    const r = await fetch('/api/paper/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rebalance: !!rebalance }),
    });
    const out = await r.json();
    if (out.error) alert('Session failed: ' + out.error);
  } catch (e) {
    alert('Session failed: ' + e);
  }
  await loadPaper();
}


/* News, shown beside the position that holds it. Context for a human reading
   the book — no strategy consumes it and no position is sized by it. */
async function showNews(ticker) {
  const panel = document.getElementById('newsPanel');
  if (!panel) return;
  panel.innerHTML = `<div class="meta" style="margin-top:12px">Loading headlines for ${ticker}…</div>`;
  let d;
  try {
    d = await (await fetch('/api/news/' + encodeURIComponent(ticker))).json();
  } catch (e) {
    panel.innerHTML = '<div class="meta">Could not load news.</div>';
    return;
  }
  const items = (d.headlines || []).map(h =>
    `<div style="padding:7px 0;border-bottom:1px solid rgba(128,128,128,.15)">
       <div style="font-size:13px">${h.headline || ''}</div>
       <div class="meta">${(h.time || '').slice(0, 16)} · ${h.provider || ''}</div>
     </div>`).join('');
  panel.innerHTML = `
    <div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(128,128,128,.25)">
      <div class="label">${ticker} — headlines${d.providers ? ' · ' + d.providers.join(', ') : ''}</div>
      ${items || `<div class="meta">${d.error || 'No headlines returned.'}</div>`}
      <div class="meta" style="margin-top:10px">
        Context only. Nothing here feeds a signal or sizes a position.
      </div>
    </div>`;
}
