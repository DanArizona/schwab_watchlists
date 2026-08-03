# schwab_watchlists

Generate, filter, review, and optionally submit symbol lists derived from Charles Schwab market data to a ThinkOrSwim watchlist.

> **Development status:** Work in progress.
> The current implementation supports Schwab Market Movers, source-neutral candidate filtering, output/audit files, and guarded submission to the ThinkOrSwim **Default Watchlist** through `mb_tools` and `ToS_scanner`.

## Project purpose

This project is intended to build useful ThinkOrSwim watchlists from programmatically generated symbol candidates.

The initial data source is the Charles Schwab Market Data API. Symbols returned by the Schwab Movers endpoint are:

1. retrieved from Schwab;
2. normalized into a source-neutral candidate model;
3. ordered and filtered locally;
4. displayed and written to output files;
5. optionally sent to the ThinkOrSwim Default Watchlist.

The design is deliberately source-neutral. Future symbol sources may include other Schwab endpoints, saved files, databases, scanners, and locally generated market-analysis results.

This project does **not** place trades or submit orders.

## Current status

The following capabilities are working:

* Secure Schwab API authentication through `mb_tools`
* Basic Schwab API quote probe
* Schwab Market Movers retrieval
* Deterministic local ordering of returned Movers records
* Source-neutral `SymbolCandidate` representation
* Price, volume, percentage-change, and result-count filters
* Explicit handling of missing filter fields
* Accepted and rejected candidate reporting
* Raw-response, symbol-list, and run-record output files
* Direct Watchlist submission from command-line symbols or a text file
* Movers-to-Watchlist integration
* Dry-run submission by default
* Explicit safeguards before live submission
* Automated tests for models, filters, pipelines, outputs, source conversion, and Watchlist submission
* Saved Schwab Movers response replay without API authentication
* Watchlist dry-run from replayed Movers data

Run the current test suite with:

```cmd
python -m pytest -q
```

## System architecture

The current data path is:

```text
Charles Schwab Market Data API
        |
        v
schwab_movers_source.py
        |
        v
SymbolCandidate objects
        |
        v
candidate_pipeline.py
        |
        +--> accepted candidates
        |
        +--> rejected candidates and reasons
        |
        v
candidate_outputs.py
        |
        +--> raw JSON response
        +--> accepted-symbol text file
        +--> pipeline run record
        |
        v
watchlist_submission.py
        |
        v
mb-scan-command
        |
        v
ToS_scanner on the ThinkOrSwim computer
        |
        v
ThinkOrSwim Default Watchlist
```

The Schwab API client and the ThinkOrSwim automation do not need to run on the same computer.

In the current development environment:

* **MasterBot** accesses the Schwab API and generates symbol lists.
* **El-Cheapo** runs ThinkOrSwim and `ToS_scanner`.
* Commands are transferred through a shared scan-control directory.

## Repository layout

| File                      | Purpose                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------- |
| `schwab_api_probe.py`     | Tests secure Schwab authentication and basic API access.                                    |
| `wl_schwab_movers.py`     | Main Schwab Movers CLI. Retrieves, filters, reports, saves, and optionally submits symbols. |
| `wl_submit.py`            | Standalone CLI for previewing or submitting a supplied symbol list.                         |
| `schwab_movers_source.py` | Retrieves Movers data and converts records into source-neutral candidates.                  |
| `candidate_model.py`      | Defines `SymbolCandidate` and market-session concepts.                                      |
| `candidate_filters.py`    | Defines filter settings, decisions, and filtering behavior.                                 |
| `candidate_pipeline.py`   | Runs an ordered candidate collection through the filter layer.                              |
| `candidate_outputs.py`    | Writes raw data, accepted symbols, and structured run records.                              |
| `watchlist_submission.py` | Builds and optionally executes guarded `mb-scan-command` submissions.                       |
| `tests/`                  | Automated unit tests.                                                                       |
| `output/`                 | Generated API responses, symbol lists, and run records.                                     |
| `watchlist_plan.py`       | Loads and validates frozen Watchlist dry-run plans.                                         |
| `wl_apply_plan.py`        | Previews or submits an exact reviewed Watchlist plan.                                       |
| `watchlist_plan_index.py` | Discovers generated Movers Watchlist plans and linked successful applications.              |
| `wl_list_plans.py`        | Lists generated plans, their application status, and optional legacy records.                |

## Requirements

The project is currently developed with:

* Windows 11
* Python 3.12
* Charles Schwab developer API credentials
* `mb_tools` with Schwab support
* `schwabdev`
* `pytest` for development testing
* `ToS_scanner` for ThinkOrSwim Watchlist automation
* ThinkOrSwim desktop for live Watchlist modification

The current workflow assumes that these `mb_tools` commands are available on `PATH`:

```text
mb-schwab-auth
mb-scan-command
mb-scan-status
```

## Schwab configuration

Schwab credentials and token information are stored in an encrypted `.ecfg` file managed by `mb_tools`.

Do not commit `.ecfg` files, token databases, API keys, application secrets, or passwords.

The encrypted configuration path is resolved in this order:

1. the `--ecfg` command-line option;
2. `MB_SCHWAB_ECFG`;
3. `%MB_VAULT%\secure_schwabdev.ecfg`;
4. `secure_schwabdev.ecfg` in the current directory.

Example Windows environment settings:

```cmd
setx MB_VAULT "C:\Users\yourname\MBV"
setx MB_SCHWAB_ECFG "C:\Users\yourname\MBV\secure_schwabdev.ecfg"
```

`MB_SCHWAB_ECFG` is optional when the file is stored as:

```text
%MB_VAULT%\secure_schwabdev.ecfg
```

Use the `mb_tools` authentication command to create, authorize, or refresh the secure Schwab configuration:

```cmd
mb-schwab-auth
```

An explicit configuration path may also be supplied:

```cmd
mb-schwab-auth --ecfg C:\path\to\secure_schwabdev.ecfg
```

## Scanner configuration

Watchlist submissions are sent through `mb-scan-command`.

The scanner command root can be supplied through:

* the `MB_SCAN_CONTROL` environment variable; or
* the `--root` command-line option.

Example:

```cmd
setx MB_SCAN_CONTROL "\\El-Cheapo\SCANCTRL"
```

Before a live Watchlist submission:

1. ThinkOrSwim must be running on the scanner computer.
2. The expected ThinkOrSwim Watchlist window must be open.
3. `ToS_scanner` must be running.
4. The network command root must be accessible.
5. The scanner must be healthy, idle, running, and not paused.

The application performs this readiness check automatically before publishing a live command. You can also verify the scanner manually:

```cmd
mb-scan-status --root "\\El-Cheapo\SCANCTRL"
```

When `MB_SCAN_CONTROL` is already configured, the shorter form may be used:

```cmd
mb-scan-status
```

The required state is:

```text
Scanner status : HEALTHY
Loop state     : idle
Running        : yes
Paused         : no
```

## Quick start

The examples below use Windows Command Prompt syntax.

### 1. Run the tests

```cmd
python -m pytest -q
```

### 2. Test Schwab API access

```cmd
python schwab_api_probe.py
```

The probe currently requests a small set of quote symbols and writes the raw response to the `output` directory.

### 3. Retrieve Schwab Movers

```cmd
python wl_schwab_movers.py
```

The default Movers request uses:

```text
Market    : NASDAQ
Sort      : PERCENT_CHANGE_UP
Frequency : 5
```

No Watchlist command is prepared or published unless a Watchlist mode is explicitly requested.

### 4. Retrieve and filter Movers

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --min-price 0.50 ^
    --max-price 500 ^
    --min-volume 100000000 ^
    --min-percent-change 1 ^
    --limit 5
```

This example accepts at most five records meeting all of these conditions:

* price of at least `$0.50`;
* price no greater than `$500`;
* volume of at least `100,000,000`;
* percentage change of at least `1%`.

Percentage filter values are expressed in **percentage points**:

```text
1    means 1%
5    means 5%
-10  means -10%
```

### 5. Replay a saved Movers response

A previously saved raw Schwab Movers response can be processed without
contacting Schwab or entering the encrypted-configuration password:

```cmd
python wl_schwab_movers.py ^
    --replay output\2026-07-31-21-30-53-movers-nasdaq-percent_change_up-raw.json ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --min-price 0.50 ^
    --min-volume 100000000 ^
    --min-percent-change 1 ^
    --limit 5
```

Replay mode uses the current filter and local-ordering implementation.

A Watchlist operation may also be previewed:

```cmd
python wl_schwab_movers.py ^
    --replay output\2026-07-31-21-30-53-movers-nasdaq-percent_change_up-raw.json ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --limit 5 ^
    --mode add
```

Replay data cannot be submitted live. The `--replay` and `--submit`
options are mutually exclusive.

## Movers options

### Market or index

Supported values currently include:

```text
$DJI
$COMPX
$SPX
NYSE
NASDAQ
OTCBB
INDEX_ALL
EQUITY_ALL
OPTION_ALL
OPTION_PUT
OPTION_CALL
```

Example:

```cmd
python wl_schwab_movers.py --market NYSE
```

### Local ordering

Supported ordering values are:

```text
VOLUME
TRADES
PERCENT_CHANGE_UP
PERCENT_CHANGE_DOWN
```

Example:

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort VOLUME
```

Returned records are ordered locally before filtering and result limiting. This provides deterministic output even when upstream behavior is inconsistent.

### Frequency

Supported Movers frequency values are:

```text
0
1
5
10
30
60
```

Example:

```cmd
python wl_schwab_movers.py --frequency 10
```

### Filters

The current scalar filters are:

```text
--min-price
--max-price
--min-volume
--min-percent-change
--max-percent-change
--limit
```

Example for declining symbols:

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_DOWN ^
    --max-percent-change -5 ^
    --min-volume 1000000 ^
    --limit 10
```

### Missing fields

When a configured filter requires a value that is missing from a candidate, the default behavior is to reject that candidate.

```cmd
python wl_schwab_movers.py ^
    --min-volume 1000000 ^
    --missing-field-policy reject
```

Missing values may instead be allowed:

```cmd
python wl_schwab_movers.py ^
    --min-volume 1000000 ^
    --missing-field-policy allow
```

Use `allow` carefully: it permits a candidate to pass a filter whose required value is unavailable.

## Output files

Generated files are written to `output/` unless another directory is selected with `--output-dir`.

A Movers run creates:

```text
YYYY-MM-DD-HH-MM-SS-movers-<market>-<sort>-raw.json
YYYY-MM-DD-HH-MM-SS-movers-<market>-<sort>-symbols.txt
YYYY-MM-DD-HH-MM-SS-movers-<market>-<sort>-run.json
```

The files contain:

### Raw response

The complete JSON response returned by Schwab.

### Symbols file

One accepted symbol per line, in final pipeline order.

### Run record

Structured metadata including:

* generation time;
* pipeline source;
* evaluation time;
* input, accepted, and rejected counts;
* accepted symbols;
* filter settings;
* rejection reasons;
* market and sort settings;
* request URL and HTTP status;
* requested Watchlist action;
* related output-file paths.

Watchlist previews and live submissions create a separate file:

```text
YYYY-MM-DD-HH-MM-SS-wl-add-run.json
```

or:

```text
YYYY-MM-DD-HH-MM-SS-wl-replace-run.json
```

Generated output files are local development artifacts and are not intended to be committed.

## Direct Watchlist submission

`wl_submit.py` accepts symbols directly from the command line, from a UTF-8 text file, or from both.

Symbols are:

* converted to uppercase;
* split on spaces and commas;
* deduplicated;
* retained in first-seen order.

### Dry-run add

```cmd
python wl_submit.py ^
    --mode add ^
    --symbols AMD NVDA PLTR
```

This displays the command and creates a run record, but does not publish anything.

### Dry-run replace

```cmd
python wl_submit.py ^
    --mode replace ^
    --file test_symbols.txt
```

A replace operation would replace the current symbols in the ThinkOrSwim Default Watchlist. Always review the dry run first.

### Explicit scanner root

```cmd
python wl_submit.py ^
    --mode add ^
    --symbols AMD NVDA PLTR ^
    --root \\El-Cheapo\SCANCTRL
```

## Movers-to-Watchlist workflow

### API and output only

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --limit 5
```

This performs no Watchlist operation.

### Preview adding accepted Movers

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --min-price 0.50 ^
    --min-volume 1000000 ^
    --limit 5 ^
    --mode add
```

Because `--submit` is absent, this is a dry run.

### Preview replacing the Watchlist

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort VOLUME ^
    --min-price 1 ^
    --limit 10 ^
    --mode replace
```

Review the accepted symbols and generated command carefully.

## Apply a reviewed Watchlist plan

A Movers dry run creates a frozen Watchlist run record containing the
exact reviewed mode and symbols.

For example:

```text
output\2026-08-03-12-00-37-wl-replace-run.json
```

Preview that saved plan without contacting Schwab:

```cmd
python wl_apply_plan.py ^
    --plan output\2026-08-03-12-00-37-wl-replace-run.json ^
    --root "\\El-Cheapo\SCANCTRL"
```

After reviewing the frozen symbols again, submit that exact plan:

```cmd
python wl_apply_plan.py ^
    --plan output\2026-08-03-12-00-37-wl-replace-run.json ^
    --submit ^
    --root "\\El-Cheapo\SCANCTRL"
```

The apply command:

- accepts only an unsubmitted dry-run record;
- validates the saved mode and symbol list;
- ignores the saved executable command;
- rebuilds the command from the validated data;
- performs the normal scanner-readiness preflight;
- never contacts the Schwab API;
- creates a new submission run record.

This provides a repeatable review-and-submit workflow without retyping
symbols and without making a second market-data request.


## List generated Watchlist plans

List the ten most recent Watchlist plans generated by
`wl_schwab_movers.py`:

```cmd
python wl_list_plans.py
```

By default, the command includes only records identified as:

```text
record_origin = schwab_movers
```

Direct dry runs created by `wl_submit.py` are not generated Movers plans.
Records created by `wl_apply_plan.py` are also excluded because they
preview or apply an existing plan rather than create a new one.

Each generated plan is shown as:

- `REVIEWED` when no linked successful application exists;
- `APPLIED` when a successful `wl_apply_plan.py --submit` run links back
  to that plan.

Show only generated plans that have not been applied:

```cmd
python wl_list_plans.py --pending-only
```

Show only replacement plans:

```cmd
python wl_list_plans.py --mode replace
```

Change the number displayed:

```cmd
python wl_list_plans.py --limit 20
```

Older records created before `record_origin` was introduced are
unclassified. Include those historical records with:

```cmd
python wl_list_plans.py --include-legacy
```

Because legacy records do not identify their creation path, the legacy
list may include dry runs created directly by `wl_submit.py` as well as
plans generated by `wl_schwab_movers.py`.

An application is considered successful only when its linked run record
has:

```text
submitted   = true
return_code = 0
```

Historical operations submitted directly through `wl_submit.py` cannot
be associated retroactively with a plan unless their run records contain
a `source_plan_file`.

### Watchlist record origins

Each new Watchlist run record contains a `record_origin` field describing
how it was created:

- `schwab_movers` — generated by `wl_schwab_movers.py`;
- `direct_submission` — created by `wl_submit.py`;
- `plan_preview` — dry-run preview created by `wl_apply_plan.py`;
- `plan_application` — live submission created by `wl_apply_plan.py`.

The distinction is:

```text
wl_schwab_movers.py
    creates a new generated plan

wl_submit.py
    previews or submits supplied symbols directly

wl_apply_plan.py
    previews or applies an existing frozen plan
```


## Live Watchlist submission

Live publication requires both:

```text
--mode add|replace
```

and:

```text
--submit
```

Example live add:

```cmd
python wl_schwab_movers.py ^
    --market NASDAQ ^
    --sort PERCENT_CHANGE_UP ^
    --min-price 0.50 ^
    --min-volume 1000000 ^
    --limit 5 ^
    --mode add ^
    --submit
```

Example live direct submission:

```cmd
python wl_submit.py ^
    --mode add ^
    --symbols AMD NVDA PLTR ^
    --submit
```

A live replace is intentionally explicit:

```cmd
python wl_submit.py ^
    --mode replace ^
    --file approved_symbols.txt ^
    --submit
```

## Submission safeguards

The current implementation uses several layers of protection:

1. No Watchlist operation is selected by default.
2. Selecting `--mode add` or `--mode replace` without `--submit` produces only a dry run.
3. `--submit` is rejected unless a mode is also supplied.
4. A Movers Watchlist operation is rejected when there are no accepted symbols.
5. Symbols are normalized and deduplicated before command construction.
6. Preview and live operations create JSON run records.
7. The underlying `mb-scan-command` exit code is checked.
8. Live submission waits for scanner processing when `--wait` is greater than zero.
9. Live submission performs a scanner-readiness preflight before `mb-scan-command` is executed.
10. The preflight requires a current `HEALTHY` heartbeat, an idle loop, `running=true`, and `paused=false`.
11. A failed preflight prevents executable lookup and command publication.

The `replace` operation is potentially destructive because it replaces the current Default Watchlist symbol list. Export or otherwise preserve the existing Watchlist before the first live replace test.

## Empty Movers responses

The Schwab Movers endpoint may return no records.

An empty response is handled safely:

```text
API records      : 0
Accepted records : 0
Rejected records : 0
```

The program still writes its normal output files.

When a Watchlist mode was requested, an empty accepted-symbol list causes the operation to stop before any command is prepared or published.

## Exit behavior

The command-line programs use nonzero exit codes for invalid arguments, missing configuration, authentication/configuration failures, API failures, missing executables, submission failures, and unsuccessful scanner results.

A successful dry run returns zero because no live action was requested.

## Development

Run all tests:

```cmd
python -m pytest -q
```

Run one test module:

```cmd
python -m pytest tests\test_watchlist_submission.py -q
```

Run with verbose output:

```cmd
python -m pytest -vv
```

Inspect pending changes before committing:

```cmd
git status --short --untracked-files=all
git diff
```

### Replay integration fixture

`tests/fixtures/schwab_movers_sample.json` contains a small synthetic
Schwab Movers response used by the automated replay workflow tests.

The fixture contains no credentials, account information, tokens, or
captured personal data.

The replay integration test exercises:

* saved-response loading;
* local record ordering;
* candidate conversion;
* filtering and result limiting;
* candidate output generation;
* Watchlist dry-run preparation;
* Watchlist run-record generation;
* rejection of live submission from replayed data.

## Design principles

### Safe by default

Retrieval, filtering, and output generation should be usable without changing ThinkOrSwim.

Any external modification requires a clear, explicit action.

### Source-neutral candidates

Filtering and ranking should not depend on a Schwab-specific response format.

Each source adapter converts its records into `SymbolCandidate` objects before the common pipeline processes them.

### Explainable decisions

Rejected candidates retain human-readable rejection reasons.

The same information can be shown in a terminal, GUI, report, or audit file.

### Reproducible output

Each run produces timestamped inputs, accepted symbols, settings, and decision metadata.

### Separation of concerns

API retrieval, candidate conversion, filtering, file output, and Watchlist submission are separate modules.

This allows future command-line tools, desktop interfaces, and web interfaces to reuse the same underlying logic.

## Planned development

The following items are planned or under consideration. They are **not yet implemented** unless stated elsewhere.

### Near-term

* Repeatable Movers integration tests using captured raw JSON
* First carefully controlled live Movers-to-Watchlist test
* Improved CLI descriptions and examples
* Additional validation of symbol formats
* Better handling and reporting of empty or unusual API responses
* Consolidated logging

### Additional candidate sources

* CSV and text-file sources
* Database queries
* Other Schwab market-data endpoints
* Schwab quote-based candidate generation
* Locally generated scanner results
* Network-provided symbol lists
* Combinations of multiple candidate sources

### Enrichment and analysis

* Quote enrichment
* Fundamentals
* Historical price data
* OHLCV series
* Configurable aggregation periods
* Premarket, regular-session, after-hours, overnight, extended-session, and combined-session data
* Regular-close reference values
* Session-specific price and volume calculations
* More advanced ranking and scoring
* Multi-stage filters
* Reusable filter presets

### User interfaces

* PySide6 desktop interface
* Dash or other browser-based interface on MasterBot
* Filter controls and saved configurations
* Candidate review before submission
* Accepted/rejected tables
* Submission previews
* Scanner-health display
* Run-history and replay tools

### Operations

* Scheduled generation
* Market-session-aware execution
* Scanner readiness checks
* Notifications
* Failure recovery
* Retention and archival of output records
* Configuration profiles for different Watchlist strategies

## Important limitations

* Schwab Movers returns a limited upstream result set. Local filtering cannot recover symbols that Schwab did not return.
* Local sorting orders only the records included in the API response.
* ThinkOrSwim Watchlist modification depends on GUI automation and therefore depends on the expected windows, layout, and application state.
* Network submission depends on the configured scanner command root being available.
* This project currently targets the ThinkOrSwim **Default Watchlist** used by the associated scanner automation.
* Market-session interpretation and OHLCV enrichment are not yet implemented.
* The project is under active development and interfaces may change.

## Security

Never commit:

* Schwab application keys or secrets;
* encrypted configuration passwords;
* decrypted credentials;
* token database files;
* `.ecfg` files;
* personal account information;
* local environment files containing secrets.

Review `.gitignore` before adding new credential or output formats.

## Disclaimer

This is an independent personal software project.

It is not affiliated with, endorsed by, or supported by Charles Schwab, Schwab Developer, ThinkOrSwim, or any associated organization.

The software is provided for development and experimentation. It does not provide financial advice. Review all generated symbols and commands before using them, especially before a live Watchlist replacement.
