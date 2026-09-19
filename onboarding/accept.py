"""
Phase 5a schema onboarding -- the ONLY place a proposal from
onboarding/propose.py can become real pipeline configuration. Validates
onboarding/proposals/<name>/binding.proposed.yaml (datainsights.semantic
.validate.validate_binding -- the same check every hand-written binding
in config/bindings/ is held to) and, only if clean, copies it plus the
generated entity contract into config/bindings/<name>.yaml and
config/entities_<name>.yaml.

This is the human-confirmation gate docs/generalization_plan.md's A2
pattern requires: the agent in onboarding/binding_proposer.py proposes,
this module's validation decides what's even eligible, and running this
script at all is the explicit human action -- nothing in this pipeline
calls it automatically.

Run: python -m onboarding.accept <name>
"""

from __future__ import annotations

import argparse
import os
import shutil

import yaml

from datainsights.semantic.binding import Binding
from datainsights.semantic.validate import validate_binding

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPOSALS_DIR = os.path.join(REPO_ROOT, "onboarding", "proposals")


class AcceptError(Exception):
    pass


def accept(name: str, *, repo_root: str = REPO_ROOT, force: bool = False) -> tuple[str, str]:
    """Returns (binding_path, contract_path) written under config/.
    Raises AcceptError (never writes anything) if the proposed binding
    doesn't validate, or if the target files already exist and
    force=False -- accepting never silently overwrites an existing
    schema."""
    proposal_dir = os.path.join(PROPOSALS_DIR, name)
    proposed_binding_path = os.path.join(proposal_dir, "binding.proposed.yaml")
    proposed_contract_path = os.path.join(proposal_dir, "entities.proposed.yaml")
    if not os.path.exists(proposed_binding_path):
        raise AcceptError(f"no proposal found at {proposed_binding_path} -- run "
                          f"`python -m onboarding.propose <data_dir> --name {name}` first")

    with open(proposed_binding_path) as f:
        raw = yaml.safe_load(f)

    # Validate against the PROPOSED contract copy, not the real
    # config/entities_<name>.yaml -- it doesn't exist yet (that's the
    # whole point of "propose before accept"). The binding's own
    # `contract_ref` already correctly names the future real path
    # (config/entities_<name>.yaml, per proposal_writer.py) -- that text
    # is copied verbatim below; only THIS validation pass points
    # somewhere temporary.
    raw_for_validation = dict(raw, contract_ref=proposed_contract_path)
    problems = validate_binding(Binding.model_validate(raw_for_validation), repo_root=".")
    if problems:
        raise AcceptError("proposed binding failed validation, nothing written:\n  " + "\n  ".join(problems))

    target_binding_path = os.path.join(repo_root, "config", "bindings", f"{name}.yaml")
    target_contract_path = os.path.join(repo_root, "config", f"entities_{name}.yaml")
    if not force:
        for existing in (target_binding_path, target_contract_path):
            if os.path.exists(existing):
                raise AcceptError(f"{existing} already exists -- pass force=True to overwrite deliberately")

    # R8: the proposed profile is validated as a real Profile before
    # anything is written -- a bad profile fails here, not at first run.
    proposed_profile_path = os.path.join(proposal_dir, "profile.proposed.yaml")
    target_profile_path = None
    if os.path.exists(proposed_profile_path):
        from datainsights.config import Profile

        with open(proposed_profile_path) as f:
            profile_raw = yaml.safe_load(f)
        try:
            profile_name = Profile.model_validate(profile_raw).profile
        except Exception as e:  # noqa: BLE001 -- re-raise typed, nothing written
            raise AcceptError(f"proposed profile failed validation, nothing written: {e}") from e
        target_profile_path = os.path.join(repo_root, "config", "profiles", f"{profile_name}.yaml")
        if not force and os.path.exists(target_profile_path):
            raise AcceptError(f"{target_profile_path} already exists -- pass force=True to overwrite deliberately")

    os.makedirs(os.path.dirname(target_binding_path), exist_ok=True)
    shutil.copy(proposed_binding_path, target_binding_path)
    shutil.copy(proposed_contract_path, target_contract_path)
    if target_profile_path:
        os.makedirs(os.path.dirname(target_profile_path), exist_ok=True)
        shutil.copy(proposed_profile_path, target_profile_path)
    return target_binding_path, target_contract_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--force", action="store_true", help="overwrite an existing binding/contract of this name")
    args = parser.parse_args()

    try:
        binding_path, contract_path = accept(args.name, force=args.force)
    except AcceptError as e:
        raise SystemExit(str(e))
    print(f"Accepted. Wrote:\n  {binding_path}\n  {contract_path}")
    print(f"Point a profile's source.binding at {args.name!r} to use it.")


if __name__ == "__main__":
    main()
