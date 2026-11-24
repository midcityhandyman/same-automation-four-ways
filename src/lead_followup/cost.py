"""Cost model. Every number it reads is an ASSUMPTION, not a vendor price.

Formula, per platform, per month:

    units        = leads * ((1 - dup_rate) * units_full_path + dup_rate * units_duplicate_path)
                   + fixed_units_per_month
    platform_fee = base_fee + max(0, units - included_units) * overage_per_unit
    upkeep       = maintenance_hours * hourly_rate
    total        = platform_fee + upkeep

Unit counts come from counting steps in this repo's own outlines. Fees are
placeholders in cost_assumptions.json: replace them with your own quotes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASSUMPTIONS_PATH = Path(__file__).with_name("cost_assumptions.json")


@dataclass(frozen=True)
class Platform:
    name: str
    billing_unit: str
    units_full_path: float
    units_duplicate_path: float
    fixed_units_per_month: float
    base_fee: float
    included_units: float
    overage_per_unit: float
    maintenance_hours: float


@dataclass(frozen=True)
class CostLine:
    platform: str
    billing_unit: str
    units: float
    platform_fee: float
    upkeep: float
    total: float


def load_assumptions(path: Path = ASSUMPTIONS_PATH) -> tuple[dict[str, Any], list[Platform]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    platforms = [
        Platform(**{k: v for k, v in entry.items() if k != "notes"}) for entry in data["platforms"]
    ]
    return data["scenario"], platforms


def monthly_cost(platform: Platform, leads: float, dup_rate: float, hourly_rate: float) -> CostLine:
    if leads < 0 or not 0 <= dup_rate <= 1:
        raise ValueError("leads must be >= 0 and dup_rate between 0 and 1")
    per_lead = (1 - dup_rate) * platform.units_full_path + dup_rate * platform.units_duplicate_path
    units = leads * per_lead + platform.fixed_units_per_month
    fee = platform.base_fee + max(0.0, units - platform.included_units) * platform.overage_per_unit
    upkeep = platform.maintenance_hours * hourly_rate
    return CostLine(platform.name, platform.billing_unit, units, fee, upkeep, fee + upkeep)


def cost_table(leads: float | None = None, path: Path = ASSUMPTIONS_PATH) -> str:
    scenario, platforms = load_assumptions(path)
    leads = scenario["leads_per_month"] if leads is None else leads
    lines = [
        "ASSUMPTIONS ONLY. These are placeholder inputs, not vendor prices.",
        f"Inputs: {leads:g} leads/month, duplicate rate {scenario['duplicate_rate']:g}, "
        f"upkeep rate {scenario['hourly_rate']:g}/hour",
        "Formula: total = base_fee + max(0, units - included_units) * overage_per_unit "
        "+ maintenance_hours * hourly_rate",
        "",
        f"{'platform':<22}{'billing unit':<14}{'units':>9}{'platform fee':>14}{'upkeep':>9}{'total':>9}",
    ]
    for platform in platforms:
        line = monthly_cost(platform, leads, scenario["duplicate_rate"], scenario["hourly_rate"])
        lines.append(
            f"{line.platform:<22}{line.billing_unit:<14}{line.units:>9.0f}"
            f"{line.platform_fee:>14.2f}{line.upkeep:>9.2f}{line.total:>9.2f}"
        )
    return "\n".join(lines)
