"""Pluggable trading strategy library.

Each strategy is a self-contained module implementing the Strategy interface in
base.py. The router (router.py) reads the market regime for a ticker and runs
only the strategies that apply to it, so contradictory rule sets never fight.

To add a strategy: write a module here, subclass Strategy, and register it in
registry.py. Nothing else in the codebase needs to change.
"""
