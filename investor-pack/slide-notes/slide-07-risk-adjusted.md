# Slide 7 — Risk-adjusted

*Presenter's notes. Not for distribution.*

## What this slide claims
On return-per-unit-of-risk, the strategies beat holding — and the strongest
result has an objection we agree with.

## What the ratios mean, in plain terms
- **Sharpe** — return per unit of total volatility. Higher is better.
- **Sortino** — same, but only counts downside volatility. Being up a lot is not
  a risk.
- **Calmar** — annual return divided by worst drawdown. The most intuitive:
  "how much do I earn per unit of maximum pain".

`long_reversal` scores 1.30 / 1.96 / 0.89 against buy-and-hold's 1.03 / 1.43 /
0.63 — better on all three.

## The objection, which you should raise yourself
`long_reversal` buys stocks that have fallen for three years. The decade tested
is one where exactly those names recovered hardest. **386 trades in one
favourable regime is not an established edge.** It is a hypothesis with
supporting evidence, and slide 10 makes testing it priority one.

## The second box — the most interesting finding
Running all twelve strategies together produced the **deepest drawdown in the
whole study** (40.31%), worse than four of its own components. Four decorrelated
strategies achieved nearly the same return at 23.67%.

Diversification running backwards is unusual. With a quant-literate investor,
this is the result most likely to start a real technical conversation — it
signals you measured rather than assumed.

## What you will be asked
**"Is Calmar 0.89 vs 0.63 actually meaningful with 386 trades?"** — *"Honestly,
it's suggestive rather than conclusive. That's why the walk-forward test is the
first thing the money pays for."*
