"""One module per dashboard page (R22). Each exposes render(); PAGES is the
dispatch table dashboard/app.py renders from and the suite parametrizes over.

PAGE_GROUPS orders the same pages into four bands for the sidebar. Ten flat
peers in load order gave a new user no way to tell that "Explore a source"
is the product and the other nine are supporting material -- the entry
point was a guess. Grouping is presentation only: PAGES stays the single
dispatch table, so nothing about page rendering or the suite's
parametrization changes."""

from dashboard.tabs import aws, digests, explore, how_it_works, ml, onboard, overview, proofs, status, technique

PAGES = {
    "Explore a source": explore.render,
    "Digests": digests.render,
    "Onboard a source": onboard.render,
    "ML opportunities": ml.render,
    "Overview": overview.render,
    "How it works": how_it_works.render,
    "Technique reference": technique.render,
    "Verification proofs": proofs.render,
    "AWS target architecture": aws.render,
    "Status": status.render,
}

# (group label, pages in it). Order here is the sidebar order; every key
# must exist in PAGES, and every PAGES key must appear exactly once --
# tests/test_dashboard_pages_render.py enforces both, so a page added
# without a home shows up as a test failure rather than silently
# disappearing from the sidebar.
PAGE_GROUPS = [
    ("Work", ["Explore a source", "Digests"]),
    ("Set up", ["Onboard a source", "ML opportunities"]),
    ("Understand", ["Overview", "How it works", "Technique reference"]),
    ("Evidence", ["Verification proofs", "AWS target architecture", "Status"]),
]
