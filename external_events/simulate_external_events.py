"""
Synthetic EXOGENOUS event generator — the second event category defined in
docs/objective.md. Where data_generator/generate_data.py simulates a
client's own transaction behavior (endogenous events), this simulates
things happening in the world around them: rate policy, tenders, energy
prices, sanctions, regulation, geopolitical disruption, natural disasters.

Every event_type below is tagged with the real, free, public source that
would eventually replace this simulation for that type -- see
EVENT_TYPE_CATALOG and external_events/README.md. Nothing here claims to
model real 2023-2025 events; dates/values are illustrative and synthetic,
same disclosure as the rest of this project's data.

Output: external_events/output/external_events.csv
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta
import os

SEED = 4242
START_DATE = date(2023, 1, 1)
END_DATE = date(2025, 12, 31)
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")

rng = np.random.default_rng(SEED)

# Must match data_generator/generate_data.py's clients.csv values exactly,
# or the sector/country join in detection_engine/external_macro_event.py
# silently matches nothing.
SECTORS = [
    "Manufacturing", "Wholesale & Retail Trade", "Construction",
    "Transportation & Logistics", "Professional & Business Services",
    "Information & Communication", "Hospitality & Food Service",
    "Health & Social Care", "Real Estate",
    "Public Administration / Institutional", "Agriculture & Food Production",
    "Energy & Utilities",
]
COUNTRIES = ["DE", "FR", "NL", "BE", "ES", "IT", "PL", "AT", "PT", "IE"]

# event_type -> (simulated source label, REAL future source, sectors it can
# plausibly affect [None = any/all], typical severity range, typical
# direction distribution [prob of "negative"])
EVENT_TYPE_CATALOG = {
    "rate_policy_change": {
        "source_name": "ECB Governing Council decision (simulated)",
        "real_source": "ECB SDMX API (Statistical Data and Metadata eXchange)",
        "sectors": None,  # EU-wide, all sectors
        "country_scope": "EU-wide",
        "severity_range": (2, 4),
        "p_negative": 0.4,
    },
    "public_tender_award": {
        "source_name": "TED award notice (simulated)",
        "real_source": "TED (Tenders Electronic Daily) API",
        "sectors": ["Construction", "Transportation & Logistics", "Manufacturing",
                    "Information & Communication", "Public Administration / Institutional"],
        "country_scope": "single_country",
        "severity_range": (2, 5),
        "p_negative": 0.05,  # a tender award is almost always an opportunity
    },
    "commodity_energy_shock": {
        "source_name": "Eurostat energy price index update (simulated)",
        "real_source": "Eurostat / ECB energy statistics",
        "sectors": ["Energy & Utilities", "Manufacturing", "Transportation & Logistics",
                    "Agriculture & Food Production"],
        "country_scope": "EU-wide",
        "severity_range": (3, 5),
        "p_negative": 0.65,
    },
    "sanctions_regulatory_change": {
        "source_name": "EU sanctions / export control update (simulated)",
        "real_source": "EU consolidated sanctions list / OpenSanctions",
        "sectors": None,
        "country_scope": "single_country",
        "severity_range": (2, 5),
        "p_negative": 0.7,
    },
    "eu_regulatory_change": {
        "source_name": "EUR-Lex regulatory notice (simulated)",
        "real_source": "EUR-Lex",
        "sectors": ["Manufacturing", "Energy & Utilities", "Transportation & Logistics",
                    "Agriculture & Food Production"],
        "country_scope": "EU-wide",
        "severity_range": (1, 4),
        "p_negative": 0.55,
    },
    "geopolitical_disruption": {
        "source_name": "GDELT global event digest (simulated)",
        "real_source": "GDELT Project (Global Database of Events, Language, and Tone)",
        "sectors": ["Transportation & Logistics", "Energy & Utilities",
                    "Wholesale & Retail Trade", "Manufacturing"],
        "country_scope": "single_country",
        "severity_range": (3, 5),
        "p_negative": 0.85,
    },
    "natural_disaster": {
        "source_name": "EM-DAT disaster record (simulated)",
        "real_source": "EM-DAT International Disaster Database",
        "sectors": ["Agriculture & Food Production", "Construction", "Real Estate"],
        "country_scope": "single_country",
        "severity_range": (3, 5),
        "p_negative": 0.95,
    },
}

HEADLINE_TEMPLATES = {
    "rate_policy_change": "ECB {action} policy rate by {bps} bps",
    "public_tender_award": "{country} public tender awarded in {sector} sector",
    "commodity_energy_shock": "Energy prices {direction_word} sharply across {scope}",
    "sanctions_regulatory_change": "EU {action} trade restrictions affecting {country}",
    "eu_regulatory_change": "New EU regulation affecting {sector} sector",
    "geopolitical_disruption": "Regional instability disrupts trade routes via {country}",
    "natural_disaster": "Severe weather event impacts {sector} sector in {country}",
}


def _sample_event(event_id: int, event_date_: date, event_type: str) -> dict:
    spec = EVENT_TYPE_CATALOG[event_type]
    sector = rng.choice(spec["sectors"]) if spec["sectors"] else None
    country = rng.choice(COUNTRIES) if spec["country_scope"] == "single_country" else None
    severity = int(rng.integers(spec["severity_range"][0], spec["severity_range"][1] + 1))
    direction = "negative" if rng.random() < spec["p_negative"] else "positive"

    # action word MUST be derived from direction, not sampled independently
    # of it -- otherwise a headline can say "cut" while direction says
    # "negative", contradicting itself (a real bug found and fixed during
    # this build, before the category tagging below started relying on
    # 'direction' being trustworthy)
    if event_type == "rate_policy_change":
        action = "cut" if direction == "positive" else "raised"
    elif event_type == "sanctions_regulatory_change":
        action = "eased" if direction == "positive" else "tightened"
    else:
        action = ""

    headline = HEADLINE_TEMPLATES[event_type].format(
        action=action,
        bps=int(rng.choice([25, 50, 75])),
        country=country or "an EU member state",
        sector=sector or "multiple sectors",
        direction_word="fall" if direction == "positive" else "rise",
        scope=country or "the EU",
    )

    return {
        "event_id": f"EVT{event_id:05d}",
        "event_date": event_date_.isoformat(),
        "event_type": event_type,
        "source_name": spec["source_name"],
        "real_source_type": spec["real_source"],
        "affected_country": country if country else "",
        "affected_sector": sector if sector else "",
        "direction": direction,
        "severity": severity,
        "headline": headline,
        "description": f"Synthetic event for POC simulation. {headline}. "
                       f"Not derived from any real 2023-2025 occurrence.",
    }


# Hand-authored scenario events -- specific, dated, narrative-rich, used by
# external_events/demo_scenario.py to demonstrate the pipeline concretely.
SCENARIO_EVENTS = [
    (date(2023, 8, 15), "commodity_energy_shock", "Energy & Utilities", None, "negative", 5,
     "European natural gas benchmark price spikes 40% amid supply disruption",
     "Synthetic scenario event. A sustained spike in European gas benchmark prices raises "
     "input costs across energy-intensive sectors and increases hedging demand for exposed "
     "manufacturers and utilities. Not derived from any real occurrence."),
    (date(2024, 3, 5), "public_tender_award", "Construction", "PL", "positive", 4,
     "Poland awards large public infrastructure tender in the construction sector",
     "Synthetic scenario event. A national infrastructure tender is awarded, creating "
     "working-capital and trade-finance needs for the winning sector's suppliers. Not "
     "derived from any real occurrence."),
    (date(2024, 6, 20), "geopolitical_disruption", "Transportation & Logistics", "IT", "negative", 5,
     "Mediterranean shipping route disruption raises freight costs and delivery risk",
     "Synthetic scenario event. Extended transit times and elevated freight/insurance costs "
     "affect logistics-dependent clients trading through the affected corridor. Not derived "
     "from any real occurrence."),
    (date(2024, 11, 10), "rate_policy_change", None, None, "positive", 3,
     "ECB cuts policy rate by 25 bps amid easing inflation",
     "Synthetic scenario event. A rate cut reduces loan servicing costs and typically "
     "narrows deposit yields, shifting relative attractiveness between credit and treasury "
     "products EU-wide. Not derived from any real occurrence."),
    (date(2025, 2, 18), "natural_disaster", "Agriculture & Food Production", "ES", "negative", 4,
     "Severe drought conditions affect agricultural output in southern Spain",
     "Synthetic scenario event. Reduced yields and higher input costs strain working "
     "capital for agriculture-sector clients in the affected region. Not derived from any "
     "real occurrence."),
    (date(2025, 7, 2), "sanctions_regulatory_change", None, None, "negative", 4,
     "EU tightens export controls affecting trade with a non-EU partner country",
     "Synthetic scenario event. Clients with counterparty exposure to the affected "
     "jurisdiction face elevated compliance and settlement friction. Not derived from any "
     "real occurrence."),
]


def gen_scenario_events(start_id: int) -> list[dict]:
    rows = []
    for i, (d, etype, sector, country, direction, severity, headline, desc) in enumerate(SCENARIO_EVENTS):
        rows.append({
            "event_id": f"EVT{start_id + i:05d}",
            "event_date": d.isoformat(),
            "event_type": etype,
            "source_name": EVENT_TYPE_CATALOG[etype]["source_name"],
            "real_source_type": EVENT_TYPE_CATALOG[etype]["real_source"],
            "affected_country": country or "",
            "affected_sector": sector or "",
            "direction": direction,
            "severity": severity,
            "headline": headline,
            "description": desc,
        })
    return rows


def gen_routine_events(start_id: int, n: int) -> list[dict]:
    rows = []
    event_types = list(EVENT_TYPE_CATALOG.keys())
    total_days = (END_DATE - START_DATE).days
    for i in range(n):
        d = START_DATE + timedelta(days=int(rng.integers(0, total_days)))
        etype = rng.choice(event_types)
        rows.append(_sample_event(start_id + i, d, etype))
    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    scenario_rows = gen_scenario_events(start_id=1)
    routine_rows = gen_routine_events(start_id=1 + len(scenario_rows), n=90)
    df = pd.DataFrame(scenario_rows + routine_rows).sort_values("event_date").reset_index(drop=True)
    out_path = os.path.join(OUT_DIR, "external_events.csv")
    df.to_csv(out_path, index=False)
    print(f"Scenario events: {len(scenario_rows)}")
    print(f"Routine events:  {len(routine_rows)}")
    print(f"Total:           {len(df)}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
