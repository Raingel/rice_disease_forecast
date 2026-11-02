# Phase 4 — Upstream Model Registry (metadata-only foundation)

**Safety boundary:** this PR establishes a central, versioned model
metadata document and a fail-closed validator. It **does not** change
existing prediction runners, forecast CSV files, GitHub schedules,
website PHP API, existing frontend, or published models.

## Registry endpoint and compatibility

`registry/models.json` lives in the forecast source repository. Its
GitHub raw path will eventually be:

```text
https://raw.githubusercontent.com/Raingel/rice_disease_forecast/refs/heads/main/registry/models.json
```

The document follows the website API v2 prototype's **registry
schema_version `2.0`** and `authority: upstream` envelope. Fields
include `registry_version`, `provenance`, and ten `models`, each with
`model_id`, `disease_id`, `display_name`, `model_type`,
`primary_field`, `value_type`, `unit`, `model_version`, `public`,
`capabilities`, `risk_profiles`, and `source`.

The website currently uses its own isolated **non-authoritative
bootstrap registry**. Having an upstream authoritative document
does **not** automatically switch the site to it. The server-side
`RegistryProvider` must be explicitly configured and validated later
by the website Codex, in isolated staging, before any production change.

## Current ten model identities

| Model | Disease/category | Public | Data/value meaning |
| --- | --- | --- | --- |
| BlastGRU-TW | rice_blast | yes | legacy_score; not necessarily calibrated probability |
| BlastTF | rice_blast | yes | legacy_score |
| BlastLSTLS | rice_blast | yes | legacy_score |
| BlastGAT | rice_blast | yes | legacy_score; separate website transformation and seasonal threshold |
| BlastDT2 | rice_blast | yes | legacy_score; observed smaller website map coverage |
| BLASTAM | rice_blast | yes | legacy_score; rule-based |
| BLBTSLS | bacterial_leaf_blight | yes | legacy_score |
| planthopper | planthopper_migration | yes | migration_index; external HYSPLIT planthopper grid |
| EPIRICE_BLB | bacterial_leaf_blight | **no** | new_infection_flow; not percent risk |
| EPIRICE_LB | rice_blast | **no** | new_infection_flow; not percent risk |

The eight public names and two private mechanistic examples mirror
the existing site's audited bootstrap metadata. The model display names
and PHP risk profile identifiers are retained for compatibility.
`model_version: "unknown"` denotes version **not confirmed per active
file**, not an assertion that all models use the same implementation.
`unit: "unknown"` or
`"legacy_percent_or_fraction_ambiguous"` deliberately avoids inventing
scientific dimensions. EPIRICE Python v2's `value_type="infection_flow"`
is a per-record *internal* term; the website registry's existing
`new_infection_flow` is the external metadata convention. Both refer to
raw new-infection flow, **not** a normalized probability. The registry
identifies `source.primary_field="rateinf_new_infections"`.

These metadata mappings are **not** a scientific validation of the
model scores, risk thresholds, calibration or disease incidence.

## Presentation and source policy

- Homepage disease grid: existing profile with 0.2 and 0.4 splits.
- Existing warning map: distinct 0.5 and 0.7 splits, not identical to
  the homepage. BlastGAT has a **separate transformation** profile.
- Seasonal high-day count: 0.5 for standard ML models, 0.23 for
  BlastGAT; planthopper positive `>0` with separate semantics.
- EPIRICE is private and has no approved map/season risk profile.
  Registering it cannot make its infection flow a public warning map.
- The public `planthopper` value originates from a different
  source repository (`Raingel/HYSPLIT-Planthopper-Forecast`) and its
  units cannot be inferred from a filename.
- Different models may have different station/date coverage, and
  Registry metadata must not imply uniform forecasts across models.

The `risk_profiles` field contains **profile IDs**, not thresholds:
the prototype website's approved risk-profile definitions remain
website-side until an explicitly reviewed synchronisation process
is designed. This PR deliberately does not copy or change website
risk rules.

## Validation API

```python
from models.model_registry import (
    load_registry, get_model, visible_models, validate_registry
)

registry = load_registry()
models = visible_models(registry)  # excludes two private EPIRICE models
one = get_model(registry, "BlastGAT")
```

The validator rejects duplicate IDs, unsafe keys, invalid schemas,
missing metadata, unapproved risk profiles, invalid publication
flags, inconsistent CSV mappings, and accidental exposure of
mechanistic models. Test-only fake models can be registered without
hard-coding another identifier into the Python loader, but **this
does not schedule their execution or publish their CSV**.

## Future integration gates (not in this PR)

1. Verify on the server that the upstream JSON is accepted by the
   actual PHP `Registry::decode` and the approved profile definitions.
2. Exercise `RegistryProvider` remote fetch, cached last-good,
   fallback, upstream identity, public/private filtering, stale age
   and failure behavior in isolated staging.
3. Implement a separate reviewed migration of
   `recent_forecast_organizer.py` and `crop_season_avg.py` from
   hard-coded model lists to Registry-driven lists **only after**
   v1 compatibility tests and coverage parity are proven.
4. Record actual per-model version/source provenance from published
   artifact headers or run manifests (not guesses).
5. Implement Python-to-website v2 data provider with a reviewed
   Output Contract and explicit scientific risk profile for any new
   mechanistic model; keep `EPIRICE_SB` unregistered until its
   source, schema, units and science are reviewed.
6. Explicit user approval remains necessary for any production
   website API or frontend switch.

**CI:**
```bash
python -m unittest discover -s tests -p 'test_model_registry.py' -v
python -m unittest discover -s tests -p 'test_public_contract_v1.py'
```

Registry changes trigger a separate PR-only validation workflow.
No workflow in this PR writes predictions, runs scheduled forecasts,
starts web servers or modifies a production environment.
