# Email templates

Replace everything in `[SQUARE BRACKETS]` before sending. Nothing else should
need changing.

**Two rules that apply to every email here.**

1. **Never state or imply a return you expect to achieve.** Describing what a
   backtest did historically is reporting. Suggesting it predicts what an
   investment will do is a claim regulators treat very differently, and it is
   the single easiest way to turn a fundraising conversation into a legal one.
2. **Do not attach the deck to a cold first email.** Send it after they reply.
   It reads as a document prepared for them rather than a mailshot, and you keep
   control of the framing.

---

## 1. Cold outreach — first contact

> **Subject:** Systematic trading research — our backtest lost to the index, and
> we can prove why

Hi [NAME],

I'm building [COMPANY], a systematic trading research platform. I'm raising a
[PRE-SEED / SEED] round and would value 20 minutes of your time.

The short version of where we are: we tested 12 published strategies across 528
instruments over nearly nine years. **Buy-and-hold beat every one of them on
absolute return.** Our best configuration returned about 3.5 percentage points a
year less, with roughly 15 points less drawdown.

I'm leading with that because it's the first thing you'd find, and because the
thing I'm actually building is the reason I know it. Most trading research can't
tell you when its own data is stale, mixed, or overfitted. Ours reports all
three, per number, and the limitations section of our study was written by us
rather than found by a reviewer.

If a research tool that argues with its own conclusions is interesting to you,
I'd like to show you what it found — including the parts that didn't work.

Would [DAY] or [DAY] suit for a short call?

Best,
[YOUR NAME]
[PHONE] · [LINK]

*Research and decision-support software. Not financial advice, and not an offer
or solicitation to buy or sell any security.*

---

## 2. Warm introduction — forwarded by a mutual contact

> **Subject:** Intro from [REFERRER] — [COMPANY]

Hi [NAME],

[REFERRER] suggested I get in touch.

I'm building [COMPANY]: systematic trading research designed to be checked
rather than trusted. Every price the system produces records which data feed
produced it and which trading session it belongs to, so a stale number is
visible as a stale number instead of looking like a working one.

We've run a full study — 12 strategies from published papers, 528 instruments,
8.94 years, 62,319 signals. I'll be upfront that holding the index outperformed
our strategies on absolute return over that decade. Where we did better was
drawdown: roughly 60% of the worst peak-to-trough fall for a comparable return.
Whether that trade is worth making is a genuine question, and I'd like your view
on it.

I'm raising [ROUND] to fund the test that could invalidate the whole thing — a
proper walk-forward across market regimes our strategies would dislike.

Are you free for [DURATION] in the next couple of weeks?

Best,
[YOUR NAME]

*Not financial advice, and not an offer or solicitation.*

---

## 3. Follow-up with the deck attached — after they reply

> **Subject:** Re: [ORIGINAL SUBJECT] — materials

Hi [NAME],

Thanks for coming back to me. The deck is attached, along with the full backtest
record.

Three things worth flagging before you open it:

- **Slide 6 is the headline and it is not a win.** Buy-and-hold returned 23.68%
  a year against our best strategy's 20.18%. Our advantage is drawdown — 22.62%
  against 37.76% — and better Sharpe, Sortino and Calmar.
- **Slide 8 lists five reasons our own numbers may not hold**, including that our
  out-of-sample test did not work as intended. Every one of those came from our
  own research.
- **We have no live track record.** The paper book has closed zero trades. What
  runs today proves the plumbing is honest, not that the strategy works.

Everything is reproducible from the repository. If any figure looks wrong I'd
genuinely like to know, and I can walk you through the file it came from.

Attached:
1. Investor deck (12 slides)
2. Backtest results — full record including limitations

Best,
[YOUR NAME]

*Research and decision-support software. Nothing was executed automatically.
Not financial advice, and not an offer or solicitation to buy or sell any
security. Past behaviour is not predictive.*

---

## 4. After the meeting

> **Subject:** Thank you — [COMPANY] follow-ups

Hi [NAME],

Thanks for your time today. Notes on what you raised:

**[QUESTION 1]** — [ANSWER, or: "I don't have a good answer yet. Here's how I'd
find out: [APPROACH]."]

**[QUESTION 2]** — [ANSWER]

You asked about [TOPIC]. [SPECIFIC RESPONSE, with the file or figure it comes
from.]

Next step from my side is [COMMITMENT WITH A DATE]. I'll send the result either
way, including if it goes against us.

Best,
[YOUR NAME]

---

## 5. Declining to answer something you don't know

Keep this one to hand. It is worth more than a confident guess.

> That's a fair question and I don't have a defensible answer yet. What I can
> tell you is what we'd need to measure to answer it: [SPECIFIC TEST]. That's
> [ON / NOT ON] the roadmap on slide 10. I'd rather tell you that than give you
> a number I can't stand behind.

---

## Phrases to avoid

| Do not write | Why | Write instead |
|---|---|---|
| "returns of 20% a year" | Reads as a forward promise | "returned 20.18% a year in a backtest over 2016–2026" |
| "low risk" / "safe" | Unqualifiable, and legally loaded | "22.62% maximum drawdown in the period tested" |
| "proven strategy" | Our own study says it is not | "tested over one decade; the out-of-sample test is still outstanding" |
| "guaranteed" / "consistent" | Never true of markets | — |
| "beats the market" | Ours did not, on return | "less drawdown than holding, for slightly less return" |
| "AI-powered trading" | Invites the wrong assumption | "systematic strategies from published research; the model writes explanations, not signals" |

---

## One thing to have ready before any meeting

**"What do I own?"** This pack contains no equity, valuation, or deal terms, and
you should not improvise them in the room. Either have a figure you can justify,
or say the raise is being scoped and you are gathering technical feedback first —
which is a legitimate and credible reason to be meeting.

If you are taking money from people in the UK, get advice on financial-promotion
rules before sending any of this at scale. Communications inviting investment
are regulated, and the exemptions for high-net-worth and sophisticated investors
have specific requirements. That is a conversation for a solicitor, not for this
document.
