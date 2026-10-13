"""The main agent and its country sub-versions.

One main version holds every feature and every default (config.yaml). Each
country is a SUB-VERSION of it: the main version plus that market's facts
(currency, share list, index, which feed prices it) plus whatever has been
tuned for that country alone. A sub-version starts with no tuning at all, so no
country begins with an advantage; it only diverges from the main version when a
change has been tested on its own market and applied to it alone.

    versions.py  what each sub-version is, its money, and running it
    tuning.py    the per-country tuning file, its change log and undo
    learner.py   the daily learning agent that proposes, tests and applies
"""
