# Safely serialize the daily forecast GitHub `main` writers

## Incident and root cause

The 2026-11-06 (JST) `Daily BLASTAM Forecast` run
`42123923577` completed its model calculation and station/seasonal
CSV generation. Its publishing step attempted a non-fast-forward
`git push` after another scheduled daily publisher had advanced
`main`. The run failed and did not publish its forecast commit.

The 2026-11-04 `Daily Rice Disease Forecast` run
`41722231899` previously had the same non-fast-forward push failure.
Both jobs recompute and write overlapping paths:
`rice_blast_prediction/recent_daily_by_station/*.csv`,
`rice_blast_prediction/recent_summary.csv`, and some
`rice_blast_prediction/data/*.csv`.

GitHub Actions workflows were separately scheduled and their ordinary
jobs could run concurrently. Waiting only at the final `git push`
step is too late: the second checkout may have started before the
first committed its output.

## Fix (two writers, one shared queue)

This PR changes **only** the `daily-forecast.yml` and
`blastam-forecast.yml` schedules, a new preflight script,
an offline regression test and documentation.

Both workflows have the *same*, repository-scoped top-level
`concurrency` group:

```yaml
concurrency:
  group: rice-forecast-main-writer
  cancel-in-progress: false
  queue: max
```

In GitHub Actions as of 2026, `queue: max` supports up to 100 pending
runs instead of silently cancelling an earlier pending job. The queue
serializes each **entire calculation and publication**; it does not
attempt to cancel a job that is already computing. The order in which
jobs acquire the queue depends on when they start waiting and is not
guaranteed to match scheduled time.

After the `actions/checkout` step and before installing dependencies,
downloading weather, or invoking any model, both writers run:

```bash
bash scripts/prepare_forecast_main_checkout.sh
```

The preflight fails closed if run from a non-`main` ref, or if it
detects uncommitted working-tree changes. It then `git fetch`es the
newest `origin/main`, checks out that exact commit, and verifies the
local SHA. This is necessary because queued workflow runs can retain
an event SHA older than the most recent published forecast.

Both workflows end in an explicit, **non-force**
`git push origin HEAD:refs/heads/main`. If a third-party writer, PR
merge or manual workflow changes main *after* synchronization, the
push will still reject rather than overwrite newer results. Such an
exception requires separate investigation; the PR intentionally
does not rebase generated outputs over newer published output files.

## Deliberately unchanged

- All schedule cron strings and workflow-dispatch inputs.
- Both model runner shell scripts and individual model commands.
- Published CSV model science, station coverage `legacy` default,
  Registry catalog `legacy` default, status semantics and risk thresholds.
- GitHub job permissions and the existing staged output path lists.
- The website PHP, HTML, JS, API, caches and data format.
- `Resumable weather archive from 2013`: this automation writes
  checkpoint data to the separate `weather-archive-2013` Git branch,
  not the forecast `main` branch, and retains its own concurrency group.
- Manual one-time historical migrations/backfills that may independently
  write `main`. Do not run those concurrently with a daily publisher
  without an additional reviewed locking policy.

## Offline verification

`tests/test_forecast_writer_serialization.py` checks the parsed
GitHub Actions YAML, identical group + `queue: max` / no cancellation,
unchanged cron times and existing model execution, plus the presence
of the fresh checkout **before** each pipeline.

In isolated temporary Git repositories it verifies:

1. A queued checkout whose original SHA is stale refreshes to the new
   remote `main` **before forecasting**.
2. A manual non-`main` trigger fails without modifying the checkout.
3. A dirty checkout fails without discarding uncommitted content.
4. A subsequent external main push causes a non-fast-forward rejection,
   preserving the remote commit and preventing a stale result overwrite.

This is a **simulation**, not a live scheduled run. Passing CI cannot
establish that tomorrow's actual runs have both published. After the PR
is merged, verify the next daily/BLASTAM Actions runs, remote commit
SHAs, and sampled station CSV outputs.

## Known independent issues

Two old one-shot workflows
`.github/workflows/era5-archive-once.yml` and
`.github/workflows/blastdt2-backfill.yml` still report invalid
workflow runs on pushes (separate repair). The checkout post-job
cleanup may also warn about an unregistered old BlastDT2 submodule;
this is **not** the same as a publishing failure and is outside this PR.
