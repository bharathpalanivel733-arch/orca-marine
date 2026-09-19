"""Pytest fixture registration for the alert tests.

The fixtures and builders live in ``_alert_fixtures`` rather than here. Test modules import
the builders directly by that name, which keeps the module basename unique across the
repository — two test directories that both define a module called ``conftest`` or ``tests``
collide in a single-rootdir pytest run, and the failure reads as a missing module rather
than as a name clash.
"""

from _alert_fixtures import now, subscriber, trawler, vallam

__all__ = ["now", "subscriber", "trawler", "vallam"]
