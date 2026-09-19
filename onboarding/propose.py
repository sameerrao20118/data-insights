"""
Phase 5a schema onboarding -- entry point. Profiles a directory of CSVs,
generates a contract, proposes a binding via local Ollama, writes
everything to onboarding/proposals/<name>/ for human review. Registers
NOTHING -- config/ is untouched until `python -m onboarding.accept <name>`.

Run: python -m onboarding.propose <data_dir> --name <schema_name>
"""

from __future__ import annotations

import argparse

from agents.model_factory import ModelConfig, get_model
from onboarding.binding_proposer import propose_binding
from onboarding.concept_proposer import propose_concepts
from onboarding.entity_contract_generator import contract_yaml_text
from onboarding.ml_profiler import assess
from onboarding.profiler import profile_directory
from onboarding.proposal_writer import profile_yaml_text, write_proposal


def run(data_dir: str, name: str, model=None) -> str:
    profiles = profile_directory(data_dir)
    if not profiles:
        raise SystemExit(f"No *.csv files found directly under {data_dir}")

    model = model or get_model(ModelConfig(mode="local"))
    proposals = propose_binding(profiles, model)
    contract_yaml = contract_yaml_text(profiles, name)
    contract_ref = f"config/entities_{name}.yaml"

    # R4: a table that mapped onto no concept but has eligible measures
    # gets a PROPOSED concept for review -- never a silent "unavailable".
    eligibility = assess(data_dir, profiles)
    concept_proposals = propose_concepts(profiles, proposals, eligibility, model)
    # R8: the profile too, so accept -> run needs no hand edits.
    profile_yaml = profile_yaml_text(name, data_dir, contract_ref)

    out_dir = write_proposal(name, contract_ref, proposals, contract_yaml,
                             concept_proposals=concept_proposals, profile_yaml=profile_yaml)
    return out_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", help="directory of *.csv files to profile")
    parser.add_argument("--name", required=True, help="schema name, e.g. 'acme_bank'")
    args = parser.parse_args()

    out_dir = run(args.data_dir, args.name)
    print(f"Wrote proposal to {out_dir}/")
    print("  binding.proposed.yaml, entities.proposed.yaml, concepts.proposed.yaml, profile.proposed.yaml, review.md")
    print(f"Review it, then: python -m onboarding.accept {args.name}")


if __name__ == "__main__":
    main()
