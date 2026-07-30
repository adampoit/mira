"""Dashboard route modules."""

from importlib import import_module

# Loaded dynamically to avoid a static api -> routers -> handlers -> api cycle.
_ = import_module("mira.dashboard.routers.reviews")
