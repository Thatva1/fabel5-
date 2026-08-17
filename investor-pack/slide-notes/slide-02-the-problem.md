# Slide 2 — The problem

*Presenter's notes. Not for distribution.*

## What this slide claims
That trading research systems routinely present broken or unverifiable data as
though it were sound, and that this is hard to detect precisely because a
failing system looks like a working one.

## Where these come from
All three were found **in this codebase** and are named later in the deck:

1. **Stale prices shown as live** — the dashboard displayed a stored price with
   no date beside it. On a Monday morning it showed Friday's close, which was
   correct, but there was no way to tell that from a dead feed.
2. **Mixed data sources** — `is_available()` returned a config flag rather than
   testing the connection, so with the broker gateway shut the system displayed
   its "licensed data" badge while serving free scraped data underneath.
3. **In-sample tuning** — our own study says the settings were chosen knowing
   how the decade turned out. See slide 8.

## Why this slide is second
It sets up the standard the rest of the deck is judged against. If you claim
these failures matter, you must then show your own — which is slide 8.

## What you will be asked
**"Doesn't everyone know this?"** — *"Everyone knows it can happen. Very few
systems can tell you, per number, whether it did. That's the difference."*
