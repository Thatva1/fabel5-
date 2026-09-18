/* Investor deck generator.
 *
 * Every number here is traceable to reports/share/results-1m.csv or to a
 * verified run of the live system. Nothing is estimated, rounded up, or
 * illustrative. The limitations slide is not a disclaimer — it is the reason
 * the rest of the deck is believable.
 */
const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";            // 13.3 x 7.5
pres.author = "Trade Assistant";
pres.title = "Trade Assistant — investor briefing";

const INK = "0F1533";       // deep background
const NAVY = "1E2761";      // dominant
const ICE = "CADCFC";       // supporting
const WHITE = "FFFFFF";
const LIGHT = "F7F9FC";     // light slide ground
const MUTED = "5A6B8C";
const AMBER = "C8871B";     // caveats, limitations
const GREEN = "2C7A5A";     // verified / wins
const RED = "A33B32";       // losses

const H = "Cambria";        // safe-list serif for headers
const B = "Calibri";        // safe-list sans for body

const W = 13.3, HT = 7.5;

/* --- helpers ------------------------------------------------------------ */

function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: INK };
  return s;
}

function lightSlide(title, kicker) {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: 0.7, y: 0.42, w: 11.9, h: 0.28, fontFace: B, fontSize: 12,
      color: MUTED, charSpacing: 2, bold: true, margin: 0,
    });
  }
  s.addText(title, {
    x: 0.7, y: kicker ? 0.72 : 0.55, w: 11.9, h: 0.75, fontFace: H,
    fontSize: 34, bold: true, color: NAVY, margin: 0,
  });
  return s;
}

// A content block: subtle tint + shadow, never an edge stripe.
function card(s, { x, y, w, h, fill }) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.06,
    fill: { color: fill || WHITE },
    line: { color: "E2E8F2", width: 1 },
    shadow: { type: "outer", angle: 90, blur: 8, offset: 1, color: "9AA8C0", opacity: 0.18 },
  });
}

// The repeated motif: a number in a filled circle.
function numberBadge(s, n, x, y, color) {
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: 0.46, h: 0.46, fill: { color: color || NAVY }, line: { color: color || NAVY },
  });
  s.addText(String(n), {
    x, y, w: 0.46, h: 0.46, align: "center", valign: "middle",
    fontFace: B, fontSize: 15, bold: true, color: WHITE, margin: 0,
  });
}

function stat(s, { x, y, w, value, label, color, size }) {
  s.addText(value, {
    x, y, w, h: 0.78, fontFace: H, fontSize: size || 40, bold: true,
    color: color || NAVY, margin: 0,
  });
  s.addText(label, {
    x, y: y + 0.76, w, h: 0.5, fontFace: B, fontSize: 11.5, color: MUTED, margin: 0,
  });
}

function footnote(s, text) {
  s.addText(text, {
    x: 0.7, y: 6.92, w: 11.9, h: 0.32, fontFace: B, fontSize: 9.5,
    color: MUTED, italic: true, margin: 0,
  });
}

/* --- 1. Title ----------------------------------------------------------- */
{
  const s = darkSlide();
  s.addText("Trade Assistant", {
    x: 0.9, y: 2.25, w: 11, h: 0.95, fontFace: H, fontSize: 52, bold: true, color: WHITE, margin: 0,
  });
  s.addText("A systematic research platform that refuses to overstate what it knows", {
    x: 0.9, y: 3.25, w: 10.4, h: 0.6, fontFace: B, fontSize: 19, color: ICE, margin: 0,
  });
  s.addShape(pres.ShapeType.line, {
    x: 0.9, y: 4.15, w: 2.2, h: 0, line: { color: AMBER, width: 2.5 },
  });
  s.addText([
    { text: "34 strategies from published papers   ·   528 instruments   ·   8.94 years", options: { breakLine: true } },
    { text: "62,319 signals   ·   725 automated tests   ·   live on licensed broker data", options: {} },
  ], {
    x: 0.9, y: 4.5, w: 11, h: 0.9, fontFace: B, fontSize: 13.5, color: "9FB2D8", lineSpacing: 22, margin: 0,
  });
  s.addText("Investor briefing  ·  6 September 2026  ·  Research only — not financial advice", {
    x: 0.9, y: 6.6, w: 11, h: 0.35, fontFace: B, fontSize: 10.5, color: MUTED, margin: 0,
  });
  s.addNotes("Open by saying what the company is: research infrastructure for systematic trading. The single differentiator is that the system is built to be checkable — it reports what it does not know. Do not open with returns; the returns slide is deliberately later and is not a win.");
}

/* --- 2. The problem ----------------------------------------------------- */
{
  const s = lightSlide("Most trading research quietly lies about its own data", "The problem");
  const items = [
    ["A feed goes down. The dashboard keeps showing the last price it had.",
     "Nothing on screen distinguishes a live quote from a three-day-old one. Decisions get made on both."],
    ["Data arrives from whichever source answered.",
     "Licensed broker data and free scraped data get mixed in one table, with no record of which produced which number."],
    ["A backtest is tuned until it looks good.",
     "The settings that produced the headline figure were chosen knowing how the decade turned out."],
  ];
  let y = 2.0;
  items.forEach((it, i) => {
    card(s, { x: 0.7, y, w: 11.9, h: 1.32 });
    numberBadge(s, i + 1, 1.0, y + 0.42, i === 2 ? AMBER : NAVY);
    s.addText(it[0], {
      x: 1.66, y: y + 0.22, w: 10.6, h: 0.42, fontFace: B, fontSize: 15.5, bold: true, color: NAVY, margin: 0,
    });
    s.addText(it[1], {
      x: 1.66, y: y + 0.66, w: 10.6, h: 0.5, fontFace: B, fontSize: 12.5, color: MUTED, margin: 0,
    });
    y += 1.5;
  });
  s.addText("Each failure looks exactly like a working system. That is what makes them expensive.", {
    x: 0.7, y: 6.45, w: 11.9, h: 0.4, fontFace: B, fontSize: 14, bold: true, italic: true, color: AMBER, margin: 0,
  });
  s.addNotes("These three are not hypothetical — all three were found and fixed in this codebase, and the deck names them later. Point 3 is the one professional allocators care most about.");
}

/* --- 3. What was built -------------------------------------------------- */
{
  const s = lightSlide("A research stack where every number carries its origin", "What we built");
  const cols = [
    ["Provenance", "Every price records which feed produced it and which trading session it belongs to. A book priced from a mixture cannot claim to be licensed.", GREEN],
    ["Freshness", "Staleness is judged against each market's own trading calendar, not the clock. A Friday close read on Monday is correct, and the system says so.", NAVY],
    ["Coverage", "The system measures what the licensed feed can actually price, and names the subscription that would unlock the rest.", NAVY],
    ["Refusal", "Where data is missing, the system reports a gap rather than substituting a plausible number.", AMBER],
  ];
  let x = 0.7;
  cols.forEach((c, i) => {
    card(s, { x, y: 2.0, w: 2.87, h: 3.5 });
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.28, y: 2.3, w: 0.5, h: 0.5, fill: { color: c[2] }, line: { color: c[2] } });
    s.addText(String(i + 1), { x: x + 0.28, y: 2.3, w: 0.5, h: 0.5, align: "center", valign: "middle", fontFace: B, fontSize: 15, bold: true, color: WHITE, margin: 0 });
    s.addText(c[0], { x: x + 0.28, y: 2.95, w: 2.3, h: 0.4, fontFace: H, fontSize: 18, bold: true, color: NAVY, margin: 0 });
    s.addText(c[1], { x: x + 0.28, y: 3.4, w: 2.35, h: 1.9, fontFace: B, fontSize: 11.5, color: MUTED, margin: 0 });
    x += 3.0;
  });
  s.addText("Six independent safety gates sit between a research idea and any order. None can be bypassed in code; one requires a human to type the ticker.", {
    x: 0.7, y: 5.8, w: 11.9, h: 0.6, fontFace: B, fontSize: 13, color: NAVY, margin: 0,
  });
  s.addNotes("The four columns are the product. Refusal is the hardest to build and the easiest to explain: a system that says 'I don't know' is worth more than one that guesses convincingly.");
}

/* --- 4. Live proof ------------------------------------------------------ */
{
  const s = lightSlide("Running live, on licensed broker data", "Verified 17 August 2026");
  card(s, { x: 0.7, y: 1.95, w: 11.9, h: 1.85 });
  stat(s, { x: 1.1, y: 2.25, w: 2.4, value: "90 / 98", label: "watchlist instruments priced by the licensed IBKR feed", color: GREEN });
  stat(s, { x: 4.0, y: 2.25, w: 2.4, value: "24", label: "live positions, every one provenance-stamped" });
  stat(s, { x: 6.9, y: 2.25, w: 2.4, value: "100%", label: "of marks from the licensed feed, verified per position", color: GREEN });
  stat(s, { x: 9.8, y: 2.25, w: 2.4, value: "725", label: "automated tests passing" });

  card(s, { x: 0.7, y: 4.05, w: 5.85, h: 2.4 });
  s.addText("Found by building it properly", { x: 1.0, y: 4.28, w: 5.2, h: 0.4, fontFace: H, fontSize: 17, bold: true, color: NAVY, margin: 0 });
  s.addText([
    { text: "The broker's own symbols for London listings and currency futures were mismatched — 5 instruments were invisible", options: { bullet: true, breakLine: true } },
    { text: "One fixed client ID meant a second process silently fell back to unlicensed data", options: { bullet: true, breakLine: true } },
    { text: "A demo holding shipped in config was being rendered as a real position", options: { bullet: true } },
  ], { x: 1.0, y: 4.7, w: 5.3, h: 1.6, fontFace: B, fontSize: 11.5, color: MUTED, paraSpaceAfter: 6, margin: 0 });

  card(s, { x: 6.75, y: 4.05, w: 5.85, h: 2.4 });
  s.addText("What the live book is not", { x: 7.05, y: 4.28, w: 5.2, h: 0.4, fontFace: H, fontSize: 17, bold: true, color: AMBER, margin: 0 });
  s.addText("It began on 15 August 2026. It is evidence that the system runs, prices honestly, and respects its risk limits. However, a few days of execution is not a statistically significant track record.", {
    x: 7.05, y: 4.72, w: 5.3, h: 1.5, fontFace: B, fontSize: 12.5, color: MUTED, margin: 0,
  });
  s.addNotes("If asked 'how has it done live' — answer directly: we have early live execution proving the pipes, but anyone quoting live performance after a few days is telling you something about themselves.");
}

/* --- 5. The study ------------------------------------------------------- */
{
  const s = lightSlide("What was tested", "The backtest");
  const facts = [
    ["528", "instruments\n500 US equities · 8 FX · 20 futures"],
    ["34", "strategies\neach from a published paper"],
    ["8.94", "years of daily bars"],
    ["62,319", "signals generated"],
  ];
  let x = 0.7;
  facts.forEach(f => {
    card(s, { x, y: 2.0, w: 2.87, h: 1.75 });
    s.addText(f[0], { x: x + 0.3, y: 2.2, w: 2.3, h: 0.72, fontFace: H, fontSize: 36, bold: true, color: NAVY, margin: 0 });
    s.addText(f[1], { x: x + 0.3, y: 2.95, w: 2.35, h: 0.7, fontFace: B, fontSize: 11.5, color: MUTED, margin: 0 });
    x += 3.0;
  });

  card(s, { x: 0.7, y: 4.0, w: 11.9, h: 2.45 });
  s.addText("Modelled honestly, on purpose", { x: 1.05, y: 4.24, w: 10.8, h: 0.4, fontFace: H, fontSize: 18, bold: true, color: NAVY, margin: 0 });
  s.addText([
    { text: "Commission and slippage charged on both sides of every trade", options: { bullet: true, breakLine: true } },
    { text: "Compounding equity, gross exposure cap, position and correlation limits — the same limits the live system enforces", options: { bullet: true, breakLine: true } },
    { text: "Where one bar covers both stop and target, the stop is always assumed — deliberately pessimistic", options: { bullet: true, breakLine: true } },
    { text: "Benchmarked against equal-weight buy-and-hold of the same 528 instruments, not a flattering index", options: { bullet: true } },
  ], { x: 1.05, y: 4.7, w: 10.8, h: 1.6, fontFace: B, fontSize: 12.5, color: MUTED, paraSpaceAfter: 7, margin: 0 });
  s.addNotes("The benchmark choice is the point. Comparing against the same instruments equal-weighted is the hardest fair test; most decks compare against something easier.");
}

/* --- 6. Results — the honest headline ----------------------------------- */
{
  const s = lightSlide("Buy-and-hold returned more. We lost less.", "Results");
  s.addText("Stated first because a reader who finds it themselves stops believing everything else.", {
    x: 0.7, y: 1.6, w: 11.9, h: 0.35, fontFace: B, fontSize: 13, italic: true, color: AMBER, margin: 0,
  });

  s.addChart(pres.ChartType.bar, [
    {
      name: "Annual return %",
      labels: ["Buy & hold", "Mean Reversion", "4-strategy blend", "Trend Following", "Macro Rotation"],
      values: [23.68, 20.18, 19.20, 17.55, 14.07],
    },
    {
      name: "Max drawdown %",
      labels: ["Buy & hold", "Mean Reversion", "4-strategy blend", "Trend Following", "Macro Rotation"],
      values: [37.76, 22.62, 23.67, 30.61, 28.94],
    },
  ], {
    x: 0.7, y: 2.1, w: 7.5, h: 4.2,
    barDir: "col", barGrouping: "clustered",
    chartColors: [NAVY, AMBER],
    showTitle: false,
    showLegend: true, legendPos: "t", legendFontSize: 11, legendColor: MUTED,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 9.5, dataLabelColor: MUTED,
    catAxisLabelColor: MUTED, catAxisLabelFontSize: 10,
    valAxisLabelColor: MUTED, valAxisLabelFontSize: 10, valAxisMaxVal: 45,
    valGridLine: { color: "E2E8F2", size: 1 },
    catGridLine: { style: "none" },
  });

  card(s, { x: 8.5, y: 2.1, w: 4.1, h: 4.2 });
  s.addText("The trade being offered", { x: 8.8, y: 2.32, w: 3.5, h: 0.4, fontFace: H, fontSize: 17, bold: true, color: NAVY, margin: 0 });
  s.addText([
    { text: "−3.5", options: { fontSize: 30, bold: true, color: RED, breakLine: true } },
    { text: "points of annual return given up", options: { fontSize: 11.5, color: MUTED, breakLine: true } },
  ], { x: 8.8, y: 2.85, w: 3.5, h: 1.0, fontFace: H, margin: 0 });
  s.addText([
    { text: "−15.1", options: { fontSize: 30, bold: true, color: GREEN, breakLine: true } },
    { text: "points off the worst peak-to-trough fall", options: { fontSize: 11.5, color: MUTED, breakLine: true } },
  ], { x: 8.8, y: 3.95, w: 3.5, h: 1.0, fontFace: H, margin: 0 });
  s.addText("Whether that is a good trade is a preference, not a fact — and it decides which line of the table matters to you.", {
    x: 8.8, y: 5.15, w: 3.5, h: 1.0, fontFace: B, fontSize: 11.5, italic: true, color: NAVY, margin: 0,
  });
  footnote(s, "Source: reports/share/results-1m.csv · £1,000,000 portfolio · 8.94 years · net of modelled commission and slippage.");
  s.addNotes("Do not soften this slide. The −3.5 / −15.1 framing is the actual proposition: same ballpark return for materially less pain. An allocator who can tolerate a 38% drawdown should buy the index, and saying so builds more credibility than it costs.");
}

/* --- 7. Risk-adjusted ---------------------------------------------------- */
{
  const s = lightSlide("On risk-adjusted measures, the strategies win", "Results");
  const rows = [
    ["", "Mean Reversion", "4-strategy blend", "Buy & hold"],
    ["Annual return", "20.18%", "19.20%", "23.68%"],
    ["Max drawdown", "22.62%", "23.67%", "37.76%"],
    ["Sharpe", "1.30", "1.12", "1.03"],
    ["Sortino", "1.96", "1.64", "1.46"],
    ["Calmar", "0.89", "0.81", "0.63"],
    ["Trades", "386", "581", "—"],
  ];
  s.addTable(rows.map((r, ri) => r.map((c, ci) => ({
    text: c,
    options: {
      fontFace: ri === 0 ? B : B,
      fontSize: ri === 0 ? 12 : 13,
      bold: ri === 0 || ci === 0,
      color: ri === 0 ? WHITE : (ci === 3 && ri > 0 ? MUTED : NAVY),
      fill: { color: ri === 0 ? NAVY : (ri % 2 ? WHITE : "EEF2F9") },
      align: ci === 0 ? "left" : "center",
      valign: "middle",
      margin: 6,
    },
  }))), {
    x: 0.7, y: 2.0, w: 7.6, colW: [2.2, 1.8, 1.9, 1.7], rowH: 0.42,
    border: { type: "solid", color: "E2E8F2", pt: 1 },
  });

  card(s, { x: 8.55, y: 2.0, w: 4.05, h: 3.05, fill: "FFF8EC" });
  s.addText("The objection we agree with", { x: 8.85, y: 2.22, w: 3.45, h: 0.4, fontFace: H, fontSize: 16, bold: true, color: AMBER, margin: 0 });
  s.addText("Mean Reversion buys three-year laggards, over a decade in which beaten-down names came roaring back — precisely the trade it is built to make. 386 trades in one favourable regime is a hypothesis with supporting evidence, not an established edge.", {
    x: 8.85, y: 2.68, w: 3.45, h: 2.2, fontFace: B, fontSize: 11.5, color: NAVY, margin: 0,
  });

  card(s, { x: 8.55, y: 5.25, w: 4.05, h: 1.35 });
  s.addText("Fewer strategies beat more", { x: 8.85, y: 5.42, w: 3.45, h: 0.35, fontFace: H, fontSize: 15, bold: true, color: NAVY, margin: 0 });
  s.addText("All twelve run together drew down 40.31%. Four decorrelated ones: 23.67%, for the same return.", {
    x: 8.85, y: 5.78, w: 3.45, h: 0.75, fontFace: B, fontSize: 11, color: MUTED, margin: 0,
  });
  footnote(s, "Sharpe, Sortino and Calmar are risk-adjusted return measures — higher is better. Source: reports/share/results-1m.csv.");
  s.addNotes("Section 4.2 of the study — combining everything made it worse — is the most interesting result and the one most likely to start a real conversation with a quant-literate investor.");
}

/* --- 8. Limitations ------------------------------------------------------ */
{
  const s = lightSlide("What this evidence cannot yet support", "Limitations");
  s.addText("Every one of these is in our own published research. None was found by a reviewer.", {
    x: 0.7, y: 1.6, w: 11.9, h: 0.35, fontFace: B, fontSize: 13, italic: true, color: MUTED, margin: 0,
  });
  const lims = [
    ["The out-of-sample test did not work", "Every strategy scored better out-of-sample than in-sample — backwards. The split lands near the 2022 bottom, so it is a regime split wearing an out-of-sample label. No strategy has yet met a period where its style was out of favour. This is the single biggest weakness.", AMBER],
    ["Survivorship bias", "Only instruments that still exist can be tested. This flatters every row — including buy-and-hold.", NAVY],
    ["Futures are mis-sized", "All 20 are modelled at full notional, with no margin or contract multiplier. Return and risk are both understated.", NAVY],
    ["One market, one decade", "US-listed instruments across roughly 2016–2026 — one of the strongest bull runs on record.", NAVY],
    ["Unseasoned live track record", "The system has only been executing live trades for a few days. Forward performance remains statistically unproven over a long horizon.", AMBER],
  ];
  let y = 2.12;
  lims.forEach((l, i) => {
    const tall = i === 0;
    card(s, { x: 0.7, y, w: 11.9, h: tall ? 1.2 : 0.82, fill: tall ? "FFF8EC" : WHITE });
    numberBadge(s, i + 1, 1.0, y + (tall ? 0.36 : 0.18), l[2]);
    s.addText(l[0], { x: 1.66, y: y + (tall ? 0.16 : 0.1), w: 10.5, h: 0.32, fontFace: B, fontSize: 14, bold: true, color: l[2] === AMBER ? AMBER : NAVY, margin: 0 });
    s.addText(l[1], { x: 1.66, y: y + (tall ? 0.5 : 0.38), w: 10.6, h: tall ? 0.64 : 0.4, fontFace: B, fontSize: 11, color: MUTED, margin: 0 });
    y += tall ? 1.34 : 0.96;
  });
  s.addNotes("Do not rush this slide. Presenting limitations you found yourself, before being asked, is the strongest available signal that the favourable numbers were measured the same way.");
}

/* --- 9. Why it is defensible --------------------------------------------- */
{
  const s = lightSlide("The asset is the measurement discipline", "Why this matters");
  card(s, { x: 0.7, y: 2.0, w: 5.85, h: 4.3 });
  s.addText("Hypotheses this project killed", { x: 1.0, y: 2.25, w: 5.2, h: 0.42, fontFace: H, fontSize: 19, bold: true, color: NAVY, margin: 0 });
  s.addText("— its own, with its own evidence", { x: 1.0, y: 2.66, w: 5.2, h: 0.3, fontFace: B, fontSize: 11.5, italic: true, color: MUTED, margin: 0 });
  s.addText([
    { text: "\"Costs destroy the edge\" — was UK stamp duty, not the rules", options: { bullet: true, breakLine: true } },
    { text: "\"Low volatility is the alpha\" — inside its own margin of error", options: { bullet: true, breakLine: true } },
    { text: "\"Winners are being capped\" — the trailing exit made it far worse", options: { bullet: true, breakLine: true } },
    { text: "\"We win on risk-adjusted return\" — as first stated, false", options: { bullet: true, breakLine: true } },
    { text: "\"More strategies diversify\" — all twelve drew down deepest", options: { bullet: true } },
  ], { x: 1.0, y: 3.1, w: 5.3, h: 3.0, fontFace: B, fontSize: 12.5, color: MUTED, paraSpaceAfter: 9, margin: 0 });

  card(s, { x: 6.75, y: 2.0, w: 5.85, h: 4.3, fill: NAVY });
  s.addText("Eight material bugs found and fixed", { x: 7.05, y: 2.25, w: 5.2, h: 0.42, fontFace: H, fontSize: 19, bold: true, color: WHITE, margin: 0 });
  s.addText("— two by the founder, reviewing the machine's work", { x: 7.05, y: 2.66, w: 5.2, h: 0.3, fontFace: B, fontSize: 11.5, italic: true, color: ICE, margin: 0 });
  s.addText([
    { text: "~2× accidental leverage inflating every headline return", options: { bullet: true, breakLine: true } },
    { text: "Drawdown divided by the final peak, understating every fall", options: { bullet: true, breakLine: true } },
    { text: "Six of twelve strategies structurally unable to pick FX or futures", options: { bullet: true, breakLine: true } },
    { text: "Rate-limited data cached as though it were a finding", options: { bullet: true, breakLine: true } },
    { text: "The same bet bought twice — spot FX and its own future", options: { bullet: true } },
  ], { x: 7.05, y: 3.1, w: 5.3, h: 3.0, fontFace: B, fontSize: 12.5, color: ICE, paraSpaceAfter: 9, margin: 0 });
  s.addNotes("This is the real pitch. Anyone can produce a backtest; the scarce skill is catching the errors that make backtests lie. Two of the eight were caught by the founder auditing generated code — that is the working method being funded.");
}

/* --- 10. Roadmap --------------------------------------------------------- */
{
  const s = lightSlide("What the next phase has to prove", "Roadmap");
  const steps = [
    ["A real out-of-sample test", "Walk-forward across regimes, including periods where each style was out of favour. Until this exists, no return figure here should be relied on.", AMBER],
    ["Honest futures sizing", "Margin and contract multipliers, so multi-asset results mean something.", NAVY],
    ["Forward track record", "Continuous paper trading on licensed data, published with closed trades and full provenance.", NAVY],
    ["Options data", "The only route to a defined-risk hedge. Nothing in the book is negatively correlated with anything else.", NAVY],
  ];
  let x = 0.7;
  steps.forEach((st, i) => {
    card(s, { x, y: 2.05, w: 2.87, h: 3.55, fill: i === 0 ? "FFF8EC" : WHITE });
    numberBadge(s, i + 1, x + 0.28, 2.32, st[2]);
    s.addText(st[0], { x: x + 0.28, y: 2.95, w: 2.35, h: 0.72, fontFace: H, fontSize: 16, bold: true, color: st[2] === AMBER ? AMBER : NAVY, margin: 0 });
    s.addText(st[1], { x: x + 0.28, y: 3.72, w: 2.35, h: 1.7, fontFace: B, fontSize: 11.5, color: MUTED, margin: 0 });
    x += 3.0;
  });
  s.addText("Priority one is the item that could invalidate the rest. That ordering is deliberate.", {
    x: 0.7, y: 5.9, w: 11.9, h: 0.4, fontFace: B, fontSize: 13.5, bold: true, italic: true, color: NAVY, margin: 0,
  });
  s.addNotes("Leading the roadmap with the test that could disprove the product is unusual and is the point. If the walk-forward fails, that is worth knowing before more money goes in, not after.");
}

/* --- 11. The ask --------------------------------------------------------- */
{
  const s = lightSlide("The ask", "Funding");
  card(s, { x: 0.7, y: 2.0, w: 11.9, h: 1.5, fill: NAVY });
  s.addText("£1,000,000 to scale the live system", {
    x: 1.1, y: 2.32, w: 11.1, h: 0.5, fontFace: H, fontSize: 27, bold: true, color: WHITE, margin: 0,
  });
  s.addText("The majority of capital is deployed to trading, keeping overhead deliberately lean.", {
    x: 1.1, y: 2.88, w: 11.1, h: 0.35, fontFace: B, fontSize: 12, italic: true, color: ICE, margin: 0,
  });

  const uses = [
    ["£900,000 — Exchange Investment", "Capital directly invested into the exchange to provide the primary trading capital for the system's live operation."],
    ["£100,000 — Official Work & Data", "Allocated to acquiring quicker, higher-quality data for bettering the system, and upgrading AI infrastructure to increase computation speed."],
  ];
  let y = 3.85;
  uses.forEach((u, i) => {
    card(s, { x: 0.7, y, w: 11.9, h: 1.0 });
    numberBadge(s, i + 1, 1.0, y + 0.27, NAVY);
    s.addText(u[0], { x: 1.75, y: y + 0.20, w: 3.8, h: 0.35, fontFace: B, fontSize: 14, bold: true, color: NAVY, margin: 0 });
    s.addText(u[1], { x: 5.6, y: y + 0.20, w: 6.8, h: 0.62, fontFace: B, fontSize: 12, color: MUTED, margin: 0 });
    y += 1.3;
  });
  s.addText("Milestone for the next round: proving live returns on deployed capital with verified execution.", {
    x: 0.7, y: 6.75, w: 11.9, h: 0.4, fontFace: B, fontSize: 12.5, bold: true, color: NAVY, margin: 0,
  });
  s.addNotes("The £1m ask is explicitly structured to maximize capital deployed (£900k) vs overhead (£100k). Emphasize that the operating budget goes directly to AI computation speed and better data feeds.");
}

/* --- 12. Close ----------------------------------------------------------- */
{
  const s = darkSlide();
  s.addText("What we can prove, and what we cannot", {
    x: 0.9, y: 1.5, w: 11.2, h: 0.75, fontFace: H, fontSize: 36, bold: true, color: WHITE, margin: 0,
  });
  card(s, { x: 0.9, y: 2.6, w: 5.5, h: 3.0, fill: "18213F" });
  s.addText("Proven", { x: 1.25, y: 2.85, w: 4.8, h: 0.4, fontFace: H, fontSize: 20, bold: true, color: "6FBF9B", margin: 0 });
  s.addText([
    { text: "The system runs live on licensed broker data", options: { bullet: true, breakLine: true } },
    { text: "Every price is traceable to a feed and a session", options: { bullet: true, breakLine: true } },
    { text: "Risk limits bind — 926 candidates refused in one session", options: { bullet: true, breakLine: true } },
    { text: "725 automated tests; eight material bugs found and fixed", options: { bullet: true } },
  ], { x: 1.25, y: 3.3, w: 4.9, h: 2.1, fontFace: B, fontSize: 12, color: ICE, paraSpaceAfter: 8, margin: 0 });

  card(s, { x: 6.9, y: 2.6, w: 5.5, h: 3.0, fill: "18213F" });
  s.addText("Not proven", { x: 7.25, y: 2.85, w: 4.8, h: 0.4, fontFace: H, fontSize: 20, bold: true, color: "E0A94A", margin: 0 });
  s.addText([
    { text: "That the strategies beat holding the index", options: { bullet: true, breakLine: true } },
    { text: "That the edge survives a regime it dislikes", options: { bullet: true, breakLine: true } },
    { text: "Any forward performance whatsoever", options: { bullet: true, breakLine: true } },
    { text: "That the multi-asset result survives honest futures sizing", options: { bullet: true } },
  ], { x: 7.25, y: 3.3, w: 4.9, h: 2.1, fontFace: B, fontSize: 12, color: ICE, paraSpaceAfter: 8, margin: 0 });

  s.addText("Every figure in this deck is reproducible from the repository.", {
    x: 0.9, y: 5.95, w: 11.2, h: 0.4, fontFace: B, fontSize: 14, italic: true, color: ICE, margin: 0,
  });
  s.addText("Research and decision-support software only. Nothing here was executed automatically, nothing here is financial advice, and this document is not an offer or solicitation to buy or sell any security. Past behaviour is not predictive.", {
    x: 0.9, y: 6.45, w: 11.2, h: 0.7, fontFace: B, fontSize: 9.5, color: MUTED, margin: 0,
  });
  s.addNotes("Close on the two-column split. Ending on what is not proven is the strongest note available — it tells the investor that everything in the left column was held to the same standard.");
}

pres.writeFile({ fileName: "/Users/thatvagowda/Desktop/fabel 5/investor-pack/Trade-Assistant-Investor-Deck.pptx" })
  .then(f => console.log("wrote", f));
