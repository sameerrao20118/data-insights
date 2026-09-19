"""One module per dashboard page (R22). Each exposes render(); PAGES is the
dispatch table dashboard/app.py renders from and the suite parametrizes over."""

from dashboard.tabs import aws, digests, explore, how_it_works, ml, onboard, overview, proofs, status, technique

PAGES = {
    "Overview": overview.render,
    "Explore a source": explore.render,
    "Onboard a source": onboard.render,
    "ML opportunities": ml.render,
    "How it works": how_it_works.render,
    "AWS target architecture": aws.render,
    "Verification proofs": proofs.render,
    "Digests": digests.render,
    "Technique reference": technique.render,
    "Status": status.render,
}
