"""One module per sidebar destination.

Pages own their widgets and their own ``render(snap)``; the window owns the
recorder service, the worker jobs and the snapshot, and fans each new snapshot
out to every page. Pages hold a reference back to the window for actions such as
starting a recording, because those must go through the window's job plumbing.
"""
from .about import AboutPage
from .exports import ExportsPage
from .incidents import IncidentsPage
from .live import LivePage
from .overview import OverviewPage
from .settings import SettingsPage

PAGES = [OverviewPage, LivePage, IncidentsPage, ExportsPage, SettingsPage, AboutPage]

__all__ = ["PAGES", "AboutPage", "ExportsPage", "IncidentsPage", "LivePage",
           "OverviewPage", "SettingsPage"]
