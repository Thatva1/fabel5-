"""Build the PUBLIC site: a static export with no way to act on anything.

WHY THIS IS A SEPARATE THING RATHER THAN THE APP WITH FLAGS OFF

The dashboard has fourteen state-changing endpoints and no authentication of
any kind, three of which reach the broker: toggle execution, prepare an order
ticket, confirm an order. It binds to 127.0.0.1 and rejects any Host header
that is not localhost, and both of those are deliberate.

Serving that app publicly with the dangerous routes disabled by a setting would
put order entry one configuration mistake away from the open internet. The
typed-ticker confirmation is no defence there either, because the ticker is
printed on the page.

So the public site contains none of it. There is no order code here to disable,
no broker client to misconfigure, and no endpoint to call — the output is
files. That is the same argument the paper book already makes about itself: not
a flag that could be turned on, but an absence of the code that would do it.

WHAT IT CONTAINS

Equity, daily P&L, the trade journal and the strategy breakdown — the record of
what the rules did. Open positions are OPTIONAL and off by default: publishing
live holdings tells the world what you are in before you are out of it, which
matters more as the book grows.
"""
