"""Operational CLI scripts for the market-edge-intraday research system.

Each module here is a thin command-line wrapper around documented entrypoints
in :mod:`app`. Heavy imports happen lazily inside ``main()`` so that importing a
script (e.g. for ``--help`` or tests) never fails just because a sibling module
is still being built.
"""
