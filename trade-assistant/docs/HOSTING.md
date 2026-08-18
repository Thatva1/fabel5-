# Putting the site on your domain

**What gets published is `public/` — static files only.** No application, no
broker connection, no order code. Regenerated after every scheduled run.

---

## Why the dashboard itself is not what gets hosted

The dashboard has **fourteen state-changing endpoints and no authentication of
any kind.** Three of them reach the broker:

| Endpoint | What it does |
|---|---|
| `POST /api/execution/toggle` | Turns order entry on |
| `POST /api/ideas/<id>/prepare-order` | Builds a live order ticket |
| `POST /api/orders/<id>/confirm` | **Places the order** |

It binds to `127.0.0.1` and rejects any `Host` header that is not localhost.
Both are deliberate.

Serving that app publicly with the dangerous routes switched off by a setting
would put order entry **one configuration mistake away from the open
internet**. The typed-ticker confirmation is not a defence there either,
because the ticker is printed on the page — it stops an accidental click, not
someone who wants in.

So the public site contains none of it. There is no order code to disable, no
broker client to misconfigure, no endpoint to call. That is the same argument
the paper book already makes about itself: *not a flag that could be turned on,
but an absence of the code that would do it.*

---

## Build it

```bash
python run.py publish                 # writes public/index.html + data.json
python run.py publish --out /tmp/site # somewhere else
python run.py publish --positions     # ALSO publish live holdings (see below)
```

It rebuilds automatically after every scheduled run while the dashboard is open
(`publish.enabled: true` in config.yaml).

### Think before using `--positions`

Publishing live holdings tells anyone reading the page what you are in **before
you are out of it**. The closed-trade record publishes either way, and that is
the part that actually evidences the strategy. Off by default.

---

## Deploy

The output is two static files. Any of these work; none needs a server process.

### Cloudflare Pages — free, HTTPS, custom domain

```bash
npx wrangler pages deploy public --project-name trade-assistant
```

Then point your domain at it in the Cloudflare dashboard.

### Netlify

```bash
npx netlify deploy --dir=public --prod
```

### GitHub Pages

Commit `public/` to a `gh-pages` branch, or point Pages at the folder.

### Your own server

```bash
rsync -av --delete public/ user@yourhost:/var/www/yourdomain/
```

**Never** serve it with `python run.py serve` on a public interface. That runs
the full dashboard, which is the thing this whole document exists to keep off
the internet.

---

## Keeping it current

The scheduler rebuilds `public/` after each run, but **it does not deploy** —
deploying touches your hosting account and should be a deliberate act. Add the
deploy to your own routine, or run it by hand after a session.

To automate it, put the deploy command in a script you own and run it when you
choose. Do not put hosting credentials into this repository.

---

## Before you send the link to anyone

- [ ] Open `public/index.html` and read it as a stranger would
- [ ] Confirm the **"Simulated results"** notice is the first thing on the page
- [ ] Confirm no account number, API key or broker detail appears —
      `grep -icE "api[_-]?key|secret|token|password|DU[0-9]" public/*`
- [ ] Decide deliberately about `--positions`
- [ ] Remember the return figure is **USD**, the strategy's own currency; if the
      audience is UK, say which number is which

The page carries `<meta name="robots" content="noindex">`, so it will not be
indexed by search engines unless you remove that.

---

## What this page is not

It reports a **paper book**. No order has ever been placed by this system. Past
behaviour is not predictive, and simulated results carry the well-known
limitation that they are produced with hindsight over a known period.

If you intend to use this page to solicit investment in the UK, the
financial-promotion rules apply to how and to whom it may be communicated. That
is a conversation for a solicitor before the link goes out, not after.
