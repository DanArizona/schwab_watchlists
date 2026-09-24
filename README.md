# schwab_watchlists

MasterBot Watchlist producers, coordinator integration, and live ThinkOrSwim materialization.

> **Development status:** Active proof of concept.
>
> The current primary development path combines:
>
> * Overnight Volume (`BASE_SET`);
> * Nasdaq LUDP/M volatility halts (`ENSURE_PRESENT`);
> * `mb_watchlist_coordinator` canonical Watchlist state;
> * outbound ThinkOrSwim display publication;
> * immutable API OV and Focus decision evidence.
>
> Earlier Schwab Market Movers and direct Watchlist-submission workflows remain available.

## Purpose

`schwab_watchlists` is the application/integration layer between MasterBot Watchlist producers and downstream Watchlist adapters.

The project began as a way to generate ThinkOrSwim Watchlists from Charles Schwab Market Movers data.

It has since evolved into the live application host for the MasterBot Watchlist coordinator proof of concept.

The central design is now:

```text
Overnight Volume
    |
    +--> BASE_SET
              |
              v
       WatchlistCoordinator
              ^
              |
    +--> ENSURE_PRESENT
    |
Nasdaq LUDP/M halts
              |
              v
      Canonical Watchlist
              |
              v
       ThinkOrSwim adapter
```

The producers do not directly decide whether ThinkOrSwim should ADD or REPLACE symbols.

They express intent.

`mb_watchlist_coordinator` owns the canonical desired state, and the ThinkOrSwim adapter reconciles toward that state.

This project does **not** place trades or submit orders.

---

# Current proof-of-concept architecture

## MasterBot

MasterBot currently runs:

* `schwab_watchlists`;
* `mb_watchlist_coordinator`;
* `mb_market_data`;
* `mb_tools`;
* Schwab API authentication and quote acquisition;
* Nasdaq halt polling;
* Watchlist coordinator logic;
* ThinkOrSwim display-command publication.

## El-Cheapo

El-Cheapo currently runs:

* ThinkOrSwim desktop;
* `scan_main_v2p0dev0.py`;
* `scan_command_loop.py`;
* ThinkOrSwim GUI automation;
* display-only Watchlist replacement.

For the current proof of concept, `scan_main_v2p0dev0.py` and `scan_command_loop.py` are separate operator-started processes.

A future production architecture is expected to merge their responsibilities into one scanner service/process.

---

# Primary data flow

The current opening path is:

```text
opening schema-v2 Uni r0
        |
        v
Schwab five-minute extended-hours candles
        |
        v
complete hashed API OV evidence
        |
        v
API OV selection / ranking
                  |
                  v
schema-v2 Focus r1 / BASE_SET
                  |
                  v
       WatchlistCoordinator
                  |
                  v
        CanonicalWatchlist
                  ^
                  |
                  |
Nasdaq Trade Halt RSS
        |
        v
 NasdaqHaltMonitor
        |
        v
new LUDP/M symbols
        |
        v
ProducerIntent(ENSURE_PRESENT)
                  |
                  v
       WatchlistCoordinator
                  |
                  v
       optional outbound ToS display
       (submitted, not read back)
```

---

# Overnight Volume producer

Current-day `OV_DECISION` is calculated by `mb_market_data`, not ToS. For each
opening-Uni symbol, it sums Schwab five-minute extended-hours candle volume
whose candle start satisfies:

```text
00:00 <= candle start < 09:00 ET
```

The 09:00 production cutoff was adopted after the September 24 retrospective
study improved top-40 agreement with `OV_FINAL` from 33/40 at 08:25 to 38/40
at 09:00 while retaining 30 minutes for acquisition, review, publication, and
poller startup. Focus remains 40 symbols while Hot-promotion evidence and
additional sessions accumulate.

The current pipeline:

1. verifies the exact opening r0 proposal;
2. acquires one explicit API result for every opening-Uni symbol;
3. preserves per-symbol status and raw selected candles;
4. requires complete coverage, zero failures, and completion before 09:30;
5. ranks `OV_DECISION` descending with symbol ascending as the tiebreaker;
6. selects the explicit top N and writes the Focus-r1 decision bundle.

Example:

```text
complete opening Uni
        |
        v
production-eligible API OV bundle
        |
        v
rank by OV_DECISION
        |
        v
top 10
        |
        v
BASE_SET
```

The current Focus producer lives in:

```text
src/schwab_watchlists/api_ov_focus_production.py
```

Legacy ToS-derived OV modules remain for historical tests and evidence. They
are not used by `run_ov_focus_production.py`.

## Important current limitation

Current-day API OV is implemented, but the historical feature store and
3/5/10/30-session comparison statistics remain future work.

Planned OV work includes:

* historical overnight-volume storage;
* 3-day, 5-day, 10-day, and 30-day medians;
* 3-day, 5-day, 10-day, and 30-day maxima;
* relative overnight-volume measures;
* unusual-volume ratios;
* persistence metrics;
* market-cap / shares-outstanding filters;
* near-open volume metrics;
* richer ranking and scoring;
* richer multi-session ranking and scoring.

---

## Daily OV-to-Focus production

`run_ov_focus_production.py` turns a production-eligible `mb_market_data` API
OV bundle into the first schema-v2 Focus revision for a trading session.

The command:

1. loads the already accepted schema-v2 opening `r0` proposal;
2. verifies source hashes, session, decision window, acquisition timing, and
   exact opening-Uni coverage;
3. rejects partial, failed, late, or smoke-test API bundles;
4. ranks the Uni symbols by API `OV_DECISION` descending, then symbol
   ascending;
5. selects the explicit top-N Focus `BASE_SET`;
6. writes a complete decision ledger, the ranked
   Focus list, a hashed manifest, and `sampling_hierarchy_r1.json`.

It intentionally does **not** publish `r1`. Publication remains a separate
operator acceptance step in `mb_market_data`.

Prerequisites:

* run before 09:30 ET on the target session date;
* use the opening `r0` proposal that was already published for that session;
* use a production-eligible API OV manifest created from that exact r0;
* ensure the API bundle covers exactly the complete opening Uni;
* choose the Focus limit explicitly. The tool has no hidden default.

Example from the `schwab_watchlists` repository root:

```cmd
set SESSION_DATE=2026-09-21
set FOCUS_LIMIT=40
set MARKET_DATA_ROOT=C:\Users\danla\Documents\github\mb_market_data
set API_OV_MANIFEST=%MARKET_DATA_ROOT%\output\api_overnight_volume\RUN_TIMESTAMP-session-%SESSION_DATE%\manifest.json

python run_ov_focus_production.py ^
  --api-ov-manifest "%API_OV_MANIFEST%" ^
  --opening-proposal "%MARKET_DATA_ROOT%\output\daily_universe_production\%SESSION_DATE%-from-2026-09-18\opening_hierarchy_r0.json" ^
  --limit %FOCUS_LIMIT%
```

A successful run reports `API OV Focus production: PASS` and writes:

```text
output\ov_focus_production\YYYY-MM-DD-HH-MM-SS\
    focus_decision_ledger.csv
    focus_symbols.csv
    sampling_hierarchy_r1.json
    manifest.json
```

The bundle is fail-closed. A hash mismatch, missing/extra Uni symbol, wrong
session or decision window, non-production source, late run, invalid hierarchy,
or pre-existing output directory prevents a publishable result. This command
does not authenticate to Schwab and does not read ToS.

After reviewing the counts and ledger, publish the generated proposal from
the `mb_market_data` repository:

```cmd
python probes\publish_sampling_hierarchy.py ^
  "output\quote_observation_journal_v2\%SESSION_DATE%.sqlite3" ^
  "C:\Users\danla\Documents\github\schwab_watchlists\output\ov_focus_production\RUN_TIMESTAMP\sampling_hierarchy_r1.json"
```

Then use the `mb_market_data` audit and replay tools to verify that `r0` and
`r1` are both present before starting the schema-v2 Focus poller.

---

# Nasdaq LUDP/M producer

Nasdaq volatility halts are obtained through `mb_market_data`.

The current reason codes are:

```text
LUDP
M
```

New halt symbols are converted into:

```text
ProducerIntent(ENSURE_PRESENT)
```

The coordinator therefore adds halt-driven symbols on top of the Overnight Volume `BASE_SET`.

The primary bridge lives in:

```text
src/schwab_watchlists/ludp_coordinator.py
```

The current monitor deliberately separates:

```text
detected
```

from:

```text
acknowledged
```

A halt is not acknowledged merely because the Nasdaq feed contained it.

The symbol is acknowledged only after downstream authoritative coordinator
and journal membership succeeds. ToS display state is not an acknowledgment
source.

If reconciliation fails, the halt remains pending for a later attempt.

The first successful Nasdaq poll establishes a startup baseline so that historical halts already present in the feed are not replayed as new events.

---

# Combined OV + LUDP coordinator runner

The current combined proof-of-concept runner is:

```cmd
python run_ludp_coordinator.py
```

Despite the historical filename, the runner now supports both:

* Overnight Volume startup `BASE_SET`;
* Nasdaq LUDP/M `ENSURE_PRESENT`.

## Live OV startup

Example:

```cmd
python run_ludp_coordinator.py ^
    --ov-watchlist C:\path\to\fresh-WL.csv ^
    --ov-limit 10 ^
    --live ^
    --polls 0 ^
    --wait 90
```

The supplied Watchlist CSV must represent the current ET trading session and contain:

```text
Symbol
OV_DECISION
```

The startup sequence is:

```text
read OV Watchlist
    |
    v
authenticate Schwab
    |
    v
fetch live quotes
    |
    v
rank OV candidates
    |
    v
BASE_SET
    |
    v
reconcile ToS
    |
    v
start Nasdaq polling
```

`--polls 0` means continuous Nasdaq polling.

## Frozen baseline startup

For controlled testing, the runner can instead bootstrap from an existing Watchlist CSV:

```cmd
python run_ludp_coordinator.py ^
    C:\path\to\baseline-WL.csv ^
    --live ^
    --polls 1
```

In this mode the symbols in the supplied CSV become the startup `BASE_SET`.

Exactly one startup source must be supplied:

```text
baseline CSV
```

or:

```text
--ov-watchlist
```

but not both.

## Useful options

```text
--ov-watchlist PATH
--ov-limit N
--ecfg PATH
--schwab-timeout SECONDS
--live
--polls N
--interval SECONDS
--timeout SECONDS
--wait SECONDS
--output-dir PATH
```

The Nasdaq polling interval currently has a minimum of 60 seconds.

---

# ThinkOrSwim display-only publication

ToS is an optional outbound display adapter. The accepted Focus roster may be
submitted with `replace_wl_symbols`; it is not observed, reconciled, or read
back. No ToS CSV is required for OV calculation, Focus selection, or adapter
verification.

In display-only mode the El-Cheapo command loop:

* persistently suspends scheduled exports;
* rejects `export_wl`, `add_wl_symbols`, and `resume_exports`;
* permits lifecycle commands and `replace_wl_symbols`;
* reports a completed replacement as submitted/unverified.

Example after the Focus bundle has been accepted:

```cmd
powershell -NoProfile -Command "$s=@((Import-Csv '%OV_R1_DIR%\focus_symbols.csv').symbol); & mb-scan-command replace_wl_symbols --symbols $s --wait 120; exit $LASTEXITCODE"
mb-scan-status
```

Do not request an export afterward. ToS display state cannot block canonical
membership, r1 publication, API polling, or journal acceptance.

The older observation, reconciliation, verification-export, transport, and
outbox-recovery modules remain in the repository as historical POC code. They
are not part of the display-only operating path.

---

# Legacy Watchlist evidence recovery

This retained POC utility is not used by the display-only adapter path. It
allowed historical verification evidence to survive transport failures.

The recovery command is:

```cmd
mb-wl-recovery
```

Implementation:

```text
src/schwab_watchlists/tos_outbox_recovery.py
```

## One recovery pass

```cmd
mb-wl-recovery --once
```

## Continuous recovery

```cmd
mb-wl-recovery
```

Default behavior repeatedly scans the El-Cheapo verification outbox and transports missing evidence to MasterBot.

Example startup:

```text
Watchlist outbox : \\El-Cheapo\SCANCTRL\outgoing\watchlist_verify
Destination      : C:\...\SCANS\watchlist_verify
Poll interval    : 5.0 seconds
Recovery loop running. Press Ctrl+C to stop.
```

The recovery path is designed to be:

* independent of ThinkOrSwim;
* independent of GUI locks;
* idempotent;
* safe when the live executor and recovery worker race to transport the same file.

If an identical final destination already exists, delivery is treated as successful.

A conflicting destination is not overwritten.

Temporary transport files use caller-unique names and are atomically renamed into place.

---

# Current MasterBot / El-Cheapo operating model

## El-Cheapo

For the current display-only adapter operation, manually start:

```text
scan_main_v2p0dev0.py
scan_command_loop.py
```

ThinkOrSwim must also be open with the expected Watchlist window/layout.

## MasterBot

Useful commands include:

```cmd
mb-scan-status
```

```cmd
mb-wl-recovery
```

and the combined coordinator runner:

```cmd
python run_ludp_coordinator.py ...
```

## Important status limitation

At present, `mb-scan-status` primarily reflects the `scan_command_loop.py` heartbeat and its logical scanner state.

It does not independently prove that `scan_main_v2p0dev0.py` is alive.

For the POC, the operator manually verifies that both El-Cheapo programs are running.

This is intentionally deferred rather than adding temporary lifecycle infrastructure that is expected to disappear when the two El-Cheapo processes are eventually merged.

---

# Schwab authentication

Schwab API access uses the encrypted configuration support provided by `mb_tools`.

The configuration path is resolved in this order where supported:

1. explicit `--ecfg`;
2. `MB_SCHWAB_ECFG`;
3. `%MB_VAULT%\secure_schwabdev.ecfg`;
4. `secure_schwabdev.ecfg` in the current directory.

Typical environment configuration:

```cmd
setx MB_VAULT "C:\Users\yourname\MBV"
```

Optionally:

```cmd
setx MB_SCHWAB_ECFG "C:\Users\yourname\MBV\secure_schwabdev.ecfg"
```

Authentication/authorization can also be managed through:

```cmd
mb-schwab-auth
```

If the Schwab refresh token has expired, the live client may require browser authorization before continuing.

Never commit:

* Schwab application secrets;
* decrypted credentials;
* passwords;
* token databases;
* `.ecfg` files;
* personal account information.

---

# Environment variables

Important variables currently include:

```text
MB_SCAN_CONTROL
MB_SCANS
MB_VAULT
MB_SCHWAB_ECFG
```

Typical MasterBot values:

```text
MB_SCAN_CONTROL = \\El-Cheapo\SCANCTRL
MB_SCANS        = local MasterBot scan/evidence directory
MB_VAULT        = local secure configuration directory
```

`MB_SCHWAB_ECFG` is optional when the encrypted file lives at:

```text
%MB_VAULT%\secure_schwabdev.ecfg
```

---

# Older Schwab Movers workflow

The original Schwab Movers functionality remains available.

The path is:

```text
Schwab Movers API
        |
        v
schwab_movers_source.py
        |
        v
SymbolCandidate
        |
        v
candidate_pipeline.py
        |
        +--> accepted
        |
        +--> rejected + reasons
        |
        v
candidate_outputs.py
        |
        v
optional Watchlist submission
```

Primary commands include:

```cmd
python wl_schwab_movers.py
```

and:

```cmd
python wl_submit.py
```

These tools support source filtering, dry-run behavior, output records, and guarded direct Watchlist submission.

They predate the coordinator architecture and remain useful for diagnostics, source exploration, and controlled manual workflows.

Longer term, symbol-producing strategies should generally express coordinator intent rather than directly deciding downstream Watchlist mutation.

---

# Direct Watchlist submission

`wl_submit.py` can still preview or directly submit supplied symbols.

Example dry-run:

```cmd
python wl_submit.py ^
    --mode add ^
    --symbols AMD NVDA PLTR
```

Live publication requires explicit submission:

```cmd
python wl_submit.py ^
    --mode add ^
    --symbols AMD NVDA PLTR ^
    --submit
```

A replace operation is potentially destructive:

```cmd
python wl_submit.py ^
    --mode replace ^
    --file approved_symbols.txt ^
    --submit
```

For coordinator-driven workflows, prefer the protected coordinator executor rather than direct submission.

---

# Repository layout

The repository is currently in a staged migration from top-level application modules into `src/schwab_watchlists`.

Important current files include:

```text
schwab_watchlists/
├── run_ludp_coordinator.py
├── run_nasdaq_halt_watchlist.py
├── run_watchlist_controller.py
├── run_watchlist_cycle.py
├── run_scheduled_watchlist_cycle.py
├── wl_schwab_movers.py
├── wl_submit.py
├── candidate_model.py
├── candidate_filters.py
├── candidate_pipeline.py
├── candidate_outputs.py
├── watchlist_submission.py
├── scanner_export_coordination.py
├── scanner_preflight.py
├── src/
│   └── schwab_watchlists/
│       ├── ludp_coordinator.py
│       ├── ov_coordinator.py
│       ├── tos_coordinator_executor.py
│       ├── tos_outbox_recovery.py
│       └── tos_watchlist_transport.py
├── tests/
├── output/
├── README.md
└── pyproject.toml
```

Several `probe_*.py` files may exist locally as development diagnostics and may intentionally remain untracked.

---

# Installation

Python 3.12 or newer is required.

Development install:

```cmd
python -m pip install -e .
```

The environment used for live operation also requires the associated MasterBot packages and Schwab API dependencies.

Current core related projects include:

```text
mb_watchlist_coordinator
mb_market_data
mb_tools
ToS_scanner
```

After installation, verify available console commands as needed:

```cmd
where mb-wl-recovery
```

---

# Tests

Run the complete suite:

```cmd
pytest -q tests
```

or:

```cmd
python -m pytest -q tests
```

The tests cover both older candidate-generation workflows and newer coordinator/ToS integration components.

Important areas include:

* candidate filtering;
* Movers replay;
* direct submission safeguards;
* LUDP intent generation;
* LUDP pending/acknowledgment behavior;
* OV ranking and `BASE_SET` generation;
* legacy ToS reconciliation and protected-observation behavior;
* live executor behavior;
* transport retries;
* concurrency-safe evidence delivery;
* outbox backlog recovery.

---

# Current POC milestone

The current proof-of-concept objective is:

```text
OV producer ───────── BASE_SET ────────┐
                                       |
                                       v
                              CanonicalWatchlist
                                       ^
                                       |
LUDP/M producer ── ENSURE_PRESENT ─────┘
                                       |
                                       v
                                      ToS
```

The earlier ToS-derived Overnight Volume side was demonstrated live. The new
API-only opening path is implemented and awaiting its first live session:

```text
complete opening Uni
        +
Schwab five-minute candles
        |
        v
production-eligible API OV evidence
        |
        v
top-N selection
        |
        v
schema-v2 Focus r1 / BASE_SET
        |
        v
optional ToS display submission
(unverified; no readback)
```

The next OV milestone is a live API-only opening. The remaining combined POC
milestone is a genuinely new Nasdaq LUDP/M event reflected in authoritative
canonical/journal membership; ToS readback is not required.

---

# Post-POC upgrade backlog

The project intentionally maintains a distinction between:

```text
POC-critical work
```

and:

```text
production hardening / extension
```

Important post-POC work includes:

## Overnight Volume

* historical data store;
* 3/5/10/30-day medians;
* 3/5/10/30-day maxima;
* relative and unusual-volume metrics;
* persistence metrics;
* premarket / near-open volume;
* market-cap and shares-outstanding filters;
* richer ranking functions.

## Coordinator policy

* maximum Watchlist size;
* richer producer priority;
* conflict-resolution policy;
* update timing policy;
* user-facing journaling/audit trail;
* durable restart state;
* recovery of in-flight transactions.

## El-Cheapo scanner architecture

Long term:

```text
scan_command_loop.py
        +
scan_main_v2p0dev0.py
        |
        v
one scanner service/process
```

The desired result is:

* one process lifecycle;
* one authoritative status;
* one GUI-action arbiter;
* one command interface;
* one heartbeat.

## Additional downstream adapters

A likely future architecture is:

```text
                CanonicalWatchlist
                 /      |       \
                v       v        v
              ToS    Schwab    future
```

A direct Schwab API adapter is a likely first additional adapter.

Other market-data/platform integrations, including a future Massive/Polygon-based adapter, may be considered later.

Adapter workers should eventually operate independently so that failure or delay in one downstream platform does not block another.

## Trading-day orchestration

Future orchestration may include:

* premarket OV universe preparation;
* timed OV `BASE_SET` generation;
* market-open transition;
* continuous halt monitoring;
* adapter health supervision;
* end-of-day intent expiration;
* retention and archival;
* restart handling;
* notifications.

For the POC, manual startup of the component processes is acceptable.

---

# Design principles

## Canonical state over downstream state

ThinkOrSwim is an adapter, not the source of truth.

## Producers express intent

OV says:

```text
these symbols form the baseline
```

LUDP says:

```text
ensure these symbols are present
```

Neither producer decides the GUI operation.

## Authoritative membership verification

Success is established from canonical hierarchy and journal state. ToS is an
unverified display projection and is not read back.

## Display outcomes remain unverified

A completed ToS command is reported as submitted/unverified. It does not alter
the authoritative decision or trigger CSV verification.

## GUI work and network transport are separate

A network problem should not keep ThinkOrSwim suspended.

## Recovery is independent

Evidence recovery should not require ThinkOrSwim or GUI automation.

## Preserve the POC boundary

Do not prematurely harden infrastructure that is likely to be replaced after the architecture is validated.

---

# Related projects

## mb_watchlist_coordinator

Owns:

* producer intents;
* canonical revisions;
* lifecycle;
* reconciliation;
* transactions;
* verification;
* adapter state and health.

## mb_market_data

Owns reusable market-data acquisition, including:

* Nasdaq halt data;
* Schwab quotes;
* Schwab price-history probes;
* current-day API OV evidence;
* legacy ToS decision snapshots;
* future historical Overnight Volume infrastructure.

## ToS_scanner

Runs on El-Cheapo and owns display-only ThinkOrSwim GUI automation, outbound
Watchlist replacement, and scanner command handling. Export/readback is
disabled in the current operating mode.

## mb_tools

Provides shared MasterBot utilities including:

* secure Schwab configuration;
* `mb-schwab-auth`;
* `mb-scan-command`;
* `mb-scan-status`;
* common environment/configuration support.

---

# Important limitations

* The project is still a proof of concept.
* ThinkOrSwim mutation depends on GUI automation.
* The API-only OV opening path has not yet completed its first live session.
* `mb-scan-status` does not currently independently prove that both El-Cheapo scanner processes are alive.
* Full restart recovery is not complete.
* Current source-priority and Watchlist-size policy are intentionally simple.
* The combined OV + genuinely new LUDP/M live event path is still the final POC validation milestone.
* Interfaces may change substantially during development.

---

# Security

Never commit:

* API secrets;
* passwords;
* `.ecfg` files;
* token databases;
* decrypted credentials;
* private account information;
* environment files containing secrets.

Review `.gitignore` before adding new generated-data or credential formats.

---

# Disclaimer

This is an independent personal software project.

It is not affiliated with, endorsed by, or supported by Charles Schwab, Schwab Developer, ThinkOrSwim, Nasdaq, or any other market-data provider.

The software is intended for development and experimentation.

It does not provide financial advice and does not place trades.
