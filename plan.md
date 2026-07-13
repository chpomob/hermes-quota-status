---
spec: "quota-status-bar-v2"
version: "1.1"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

The v2 implementation already exists on this branch; this plan drives a fix
round that addresses every adversarial-review finding (C1–C8, M1–M5, N1–N2)
while re-verifying each spec requirement (R1–R20) with tests, so a dev loop
can execute the steps sequentially and land a clean, fully-covered build.

## Steps

### P1: Reject non-finite numbers in quota_api validation
- **Files:** [quota_api.py, test_quota_status.py]
- **Description:** Fix finding C7. Add `math.isfinite()` checks to
  `json_number()` (quota_api.py:166) and `json_number_or_none()`
  (quota_api.py:171) so NaN/±Infinity are rejected (raise / return `None`)
  instead of flowing into `_clamp_pct` (where NaN clamps to a falsely-healthy
  0%) and into `_normalize_deepseek_balance` (where NaN renders as a green
  `$NaN`). Audit `_clamp_pct`, `_clamp_fraction`, `_percent_or_none`, and
  `_json_nested_number_*` so no other numeric path admits non-finite values.
  This also hardens the DeepSeek balance provider (R4, R5, R6): keep
  `fetch_deepseek_balance()` targeting `https://api.deepseek.com/user/balance`
  with `Authorization: Bearer <DEEPSEEK_API_KEY>` and treat an absent key as
  provider-unavailable.
- **Dependencies:** []
- **Tests:** Unit tests feeding `NaN`, `Infinity`, `-Infinity` (as parsed JSON
  floats) through `json_number_or_none` and through a full DeepSeek balance
  normalization — assert rejection, no `$NaN`/`$inf` display, and no
  falsely-healthy percentage. Regression tests for AC5–AC8 (DeepSeek success
  segment, bearer header, endpoint URL, absent-key unavailability).
- **Risks:** Python's `json` module parses bare `NaN`/`Infinity` by default,
  so tests must exercise the post-parse validators, not just raw strings.
  Over-strict rejection could drop legitimate `0` or negative balances —
  reject only non-finite, not negative/zero.

### P2: GLM host fallback breadth, credential fallback, and auth accounting
- **Files:** [quota_api.py, test_quota_status.py]
- **Description:** Fix finding M5 and enforce R2/R3 exactly as written. In
  `fetch_glm_quota()` (quota_api.py:529), fall back from `api.z.ai` to
  `open.bigmodel.cn` whenever the primary host cannot produce a successful
  quota response, whatever the reason: connection error, timeout, DNS
  failure, any non-2xx HTTP status (including 401/403), or a
  malformed/unparseable body. Per AC26, if the secondary host then
  succeeds, its data is used for rendering and the cycle counts as a
  success. A GLM authentication failure (for the R15 counter) is recorded
  only when the overall fetch ends unsuccessfully AND at least one host
  rejected the credential with 401/403. Preserve R1/R2 semantics:
  credential priority is raw `GLM_API_KEY`, falling back to `ZHIPU_API_KEY`
  when `GLM_API_KEY` is unset OR set to an empty string, sent in the
  `Authorization` header with no bearer prefix; the quota-limit path
  `/api/monitor/usage/quota/limit` is hit on `api.z.ai` first.
- **Dependencies:** []
- **Tests:** AC2/AC25 (raw header, GLM_API_KEY priority), AC3 (no creds →
  unavailable), AC4 (z.ai first), AC26 driven through every primary-failure
  class: z.ai 503, timeout, 401, 403, and invalid-JSON body each cause a
  request to `open.bigmodel.cn`, and a subsequent bigmodel success renders
  bigmodel data with no auth-counter increment. Both hosts returning 401 →
  an auth-classified failure result; z.ai 500 + bigmodel 500 → a non-auth
  failure result. New R2 test: `GLM_API_KEY` present but set to the empty
  string with `ZHIPU_API_KEY` set → the request's `Authorization` header
  equals the `ZHIPU_API_KEY` value.
- **Risks:** Forwarding the same credential to the secondary host after a
  primary 401 is deliberate — R3/AC26 require fallback on any unsuccessful
  primary response; document this in code so it is not "fixed" back later.
  The auth-accounting rule (auth failure only when the whole fetch fails
  and some host said 401/403) must be encoded in the result shape consumed
  by `_store_provider_result`, or per-host rejections would double-count.
  Mocked urllib layers must count per-host requests accurately.

### P3: Distinguish Gemini API-key transport failures from auth failures
- **Files:** [quota_api.py, test_quota_status.py]
- **Description:** Fix finding C8. In `fetch_gemini_quota()`
  (quota_api.py:460, catch site near :474), stop collapsing every
  `models.list` failure into `key_valid=False`. Classify: HTTP 400/401/403 →
  invalid key (`key_valid=False`, auth failure); timeout, DNS failure,
  connection reset, HTTP 5xx → transport error (return `None` or a distinct
  `error_type` so the caller records a network error, keeps the retained
  cache, and does not render `Ge:KEY` or bump the auth counter).
- **Dependencies:** []
- **Tests:** Simulate `URLError`, `socket.timeout`, and HTTP 500 → assert no
  `key_valid=False` result, no `Ge:KEY` rendering, and no auth-counter
  increment; simulate HTTP 401/403 → assert `key_valid=False` and an
  auth-classified failure. Keep AC9's `Ge` short-code assertion green.
- **Risks:** The renderer and `_provider_result_auth_error_type` in
  `__init__.py` consume `key_valid`; changing the failure shape must not
  break `_store_provider_result` classification — coordinate the error-type
  string with the existing `_retry_ttl` types.

### P4: Fail-closed, provider-tagged, collision-safe Agy TUI scraper
- **Files:** [agy_quota.py, test_quota_status.py]
- **Description:** Fix findings C1, C3, C6 (producer side). In
  `_capture_usage()` (agy_quota.py:26, buggy region :49–54): parse
  fail-closed — a model block is only emitted when its usage-bar line
  matches the percentage regex; on a parse miss the block is skipped (never
  default `remaining_pct=100`). Tag each parsed block with its provider
  (Gemini/Claude/GPT) derived from the header line, and have
  `fetch_agy_quota()` expose that tag so callers can filter to Gemini-only
  pools. Replace the fixed `TMUX_SESSION = "hermes-agy-quota"` constant
  (agy_quota.py:18) with a per-invocation name suffixed by PID plus a random
  token, drop the unconditional startup `kill-session` of the shared name,
  and wrap the scrape so `kill-session` of the own session always runs in a
  `try/finally`.
- **Dependencies:** []
- **Tests:** Unit tests on `_capture_usage` with: a well-formed Gemini block
  (parsed, tagged `gemini`), a block whose bar line lacks a trailing `NN%`
  (skipped entirely — no fabricated 100% remaining), mixed Gemini+Claude+GPT
  scrollback (each tagged with its provider), and a stray line starting with
  "Claude" that is not a real block (no emission). Test that two
  concurrently-generated session names differ and that cleanup runs when
  `capture-pane` raises.
- **Risks:** Real Agy TUI output layout may vary between versions; keep the
  regexes anchored but tolerant of ANSI-stripped whitespace. Orphaned tmux
  sessions if the process is SIGKILLed — the unique name at least prevents
  cross-process kills; consider a best-effort sweep of stale
  `hermes-agy-quota-*` sessions older than the scrape timeout.

### P5: Demote the TUI scrape and filter Gemini data in orchestration
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix findings C5 and C6 (consumer side). Rework
  `_fetch_gemini_agy()` (__init__.py:421) and `_fetch_gemini()`
  (__init__.py:632) so the partial-viewport TUI scrape is no longer
  authoritative: prefer the structured Agy API and CloudCode OAuth paths,
  and use the scrape only as a best-effort supplement (or merge its
  Gemini-tagged entries with structured results) instead of returning
  immediately on any nonempty scrape (current short-circuit at
  __init__.py:338/:429). Using the provider tags from P4, drop Claude/GPT
  pool entries before grouping at __init__.py:430 so an exhausted Claude
  pool can never paint the `Ge` segment red.
- **Dependencies:** [P4]
- **Tests:** With both scrape and structured API mocked available, assert
  structured data wins/merges rather than scrape-only output; with scrape
  returning Gemini+Claude+GPT entries, assert the rendered `Ge` segment
  reflects only Gemini pools (healthy Gemini + exhausted GPT ⇒ green `Ge`);
  AC22 regression (exactly one Gemini segment, `Ge` short code).
- **Risks:** Merge logic can double-count the same pool reported by both
  sources — key merged entries by normalized model/pool label. Ordering
  change may alter which reset time is displayed; pin expectations in tests.

### P6: Correct authentication-failure classification in Gemini fetchers
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix finding C2. `_fetch_gemini_cloudcode()`
  (__init__.py:549–556) must stop collapsing auth failures to `None` (which
  the store path classifies as `missing_token`); return or raise a
  distinctly auth-typed result. `_agy_api_fetch_models()` (__init__.py:373,
  blanket catch near :413) must classify HTTP 401/403 as auth failures
  instead of returning `None`. In `_store_provider_result` /
  `_mark_provider_error` (__init__.py:732–789), `missing_token` must be
  neutral: it must neither increment nor call `_reset_auth_failures()`
  (current erasure at the :746/:761/:817 sites) — only a successful
  *authenticated* check resets the counter, per AC31. Define the
  provider-level aggregation rule explicitly: within one refresh cycle, a
  Gemini auth failure is recorded only when at least one credentialed path
  (Agy API or CloudCode) fails with an auth-classified error AND no path
  completes a successful authenticated fetch in that cycle; any successful
  authenticated path makes the cycle a success and resets the counter per
  AC31, and cycles whose only failures are non-auth (timeouts, missing
  token) leave the counter untouched. Delete or rewrite the existing test
  that locks in the contradictory reset-on-missing-token behavior.
- **Dependencies:** [P5]
- **Tests:** For Gemini: a 401 from the Agy API (with no other path
  succeeding) increments the auth counter; a CloudCode auth failure (ditto)
  increments it; an Agy API 401 combined with a successful CloudCode fetch
  in the same cycle → no increment, counter reset to zero, provider
  rendered; a missing token leaves the counter untouched (neither increment
  nor reset); a network timeout leaves it untouched; a successful
  authenticated fetch resets it to zero. Assert the old contradictory test
  is gone/replaced.
- **Risks:** The TUI scrape path is not an authenticated check — a scrape
  success must neither reset the auth counter nor mask a cycle where every
  credentialed path failed auth, or a suppressed provider could "recover"
  without valid credentials, violating R17. The aggregation needs the
  per-cycle outcome of each path visible in one place; if paths report
  independently to `_store_provider_result`, add a cycle-level collector
  rather than guessing from the last write.

### P7: Suppression semantics regression coverage (R15–R17)
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** With classification fixed in P6, verify and (where
  needed) adjust `_record_auth_failure` / `_reset_auth_failures` /
  `_provider_auth_suppressed` (__init__.py:660–670) and the refresh loop so
  that: counters are per-provider (R16), suppression triggers at 3
  consecutive auth failures (R15), non-auth failures neither increment nor
  reset (AC31), suppressed providers with configured credentials keep
  refreshing in the background (AC32), and a later successful authenticated
  check restores display eligibility (R17/AC21). Make counter access
  thread-safe: guard `_auth_failure_counts` mutations with a lock (reuse
  `_cache_lock` or a dedicated `_auth_lock`) so increments are atomic
  read-modify-writes and the renderer reads a consistent counter/cache
  view — this establishes the discipline P8's parallel workers rely on.
- **Dependencies:** [P6]
- **Tests:** AC19 (3rd consecutive auth failure omits the provider), AC20
  (one provider's suppression does not affect others), AC21 (recovery via a
  background success), AC31 (counter lifecycle: starts at zero, auth-only
  increments, success-only reset), AC32 (suppressed provider still fetched
  when credentialed). Drive these through the public refresh/render entry
  points, not by poking private counters. Add a threaded test: N concurrent
  auth-failure recordings for one provider yield exactly N increments (no
  lost updates).
- **Risks:** The counters are module-level state shared between refresh
  workers and the render path in production: unsynchronized `+= 1` is a
  read-modify-write race that loses increments, and a renderer can observe
  a torn view of counter vs. cached-result state — keep every counter
  mutation inside the lock and never hold the lock across network I/O.
  Once P8 runs providers in parallel, any path left unlocked here becomes a
  live race. Tests that manipulate `_auth_failure_counts` directly go
  stale; use fixtures that reset global state between tests to avoid
  inter-test bleed.

### P8: Per-provider refresh isolation
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix finding C4. Replace the single global refreshing
  flag / serial fetch (claim logic at __init__.py:722, worker at :791–:843)
  with per-provider refresh claims so a slow Gemini chain (40s+ worst case)
  cannot block Claude/Codex/GLM/DeepSeek updates — either one worker per
  due provider or a claim-per-provider structure with a per-provider
  in-flight flag and a TTL-based reclaim so a hung worker cannot block its
  provider forever. Stamp each claim with a generation/epoch so a reclaimed
  worker's late result is discarded rather than clobbering newer data.
  Workers must use the locking discipline P7 established for the auth
  counters and cache. (The keyring deadline and Agy HTTP read hardening
  formerly bundled here are split into P9 and P14.)
- **Dependencies:** [P7]
- **Tests:** With a mocked Gemini fetcher that blocks on an event, assert a
  concurrent refresh still updates DeepSeek/GLM cache entries; assert a
  hung provider's claim is reclaimable after its TTL and that the hung
  worker's eventual stale-generation result is discarded; assert
  suppression counters stay correct when two providers fail auth
  concurrently.
- **Risks:** Threading changes are deadlock-prone — keep `_cache_lock` hold
  times to pure dict updates, never across network I/O. TTL reclaim opens a
  duplicate-worker window in which the old hung worker may still return —
  the generation check must gate every write path (cache, counters, error
  state). GLib/GTK callbacks must remain on the main context; only the
  fetches move to worker threads.

### P14: Bounded, context-managed Agy API HTTP reads
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix the HTTP-boundary part of finding M1 (split out of
  P8). Read Agy API HTTP responses (the `_agy_api_fetch_models` region at
  __init__.py:373 and the read sites near :330/:413) inside a `with`
  context manager and enforce a 1 MB size cap, matching `fetch_json`'s
  discipline in quota_api.py; an oversized body is treated as a malformed
  (non-auth) response so it neither bumps the auth counter nor drops
  retained cache.
- **Dependencies:** [P6]
- **Tests:** Unit test that an oversized (>1 MB) Agy API body is rejected
  as a non-auth error (no counter change, retained cache kept) and the
  response object is closed (context-manager path exercised); a body
  exactly at the cap still parses; a connection dropped mid-read closes the
  response and surfaces a transport error.
- **Risks:** Detecting oversize by reading cap+1 bytes must tolerate
  chunked/short reads from mocked sockets — reuse the proven `fetch_json`
  pattern from quota_api.py rather than reimplementing the cap logic.

### P9: Harden the keyring token lookup (matching, deadline, cache)
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix finding M4 and the keyring-IO part of finding M1
  (split out of P8) in the keyring fallback used by the Agy API path
  (__init__.py:393 region): match secret items by exact attributes/label
  instead of substring `'antigravity'`; validate the JSON shape before
  indexing (`isinstance` checks on `['token']['access_token']` with
  graceful `None` on mismatch); run the `Secret.Service.get_sync()` call
  under a deadline wrapper (daemon thread + timeout) so a locked keyring
  cannot hang a refresh worker; and cache the retrieved token between
  refreshes with a bounded TTL and invalidation on auth failure so the
  keyring is not enumerated/unlocked on every refresh cycle.
- **Dependencies:** [P8]
- **Tests:** Unit tests with a faked Secret item collection: exact-match
  item found → token returned; near-miss label (substring only) → not
  matched; malformed secret payloads (missing `token`, non-dict, wrong
  types) → `None` without raising; a `get_sync` stub that never returns →
  lookup yields `None` within the deadline instead of hanging; second
  refresh within the cache TTL → zero keyring calls; auth failure → cache
  invalidated and the next refresh re-reads the keyring; concurrent
  invalidate-while-refill → no exception and no resurrected stale token.
- **Risks:** Exact attribute matching may miss tokens stored by older Agy
  versions under different labels — probe the real item attributes once and
  encode the observed exact schema, with the old label kept as a documented
  secondary exact match if it exists in the wild. The token cache is shared
  mutable state under P8's per-provider workers: guard read/refill/
  invalidate with a lock never held across keyring IO, and use a generation
  counter so an in-flight refill cannot resurrect a token that was
  invalidated meanwhile. Bound the cache TTL so a rotated token is picked
  up even without an auth failure; an unbounded cache serves a stale token
  indefinitely. The deadline wrapper leaks a blocked daemon thread when the
  keyring truly hangs (acceptable — document it), and GLib/Secret calls may
  not be safely interruptible — never invoke `get_sync` on the GTK main
  loop.

### P10: Fix the reset_iso data contract
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix finding M2 and keep R13 semantics coherent. The Agy
  path (__init__.py:458) must stop storing `'+3h'`-style strings in
  `reset_iso` — convert to a real ISO-8601 timestamp (now + parsed delta)
  or store the display form under a distinct key so `_fmt_reset` /
  `datetime.fromisoformat` consumers never silently get `None`. Relative
  countdown formatting (`_fmt_reset` __init__.py:848, `_fmt_hours_until`
  :864) stays the single formatting path: future resets as `XhYm`-style
  countdowns, expired/current as `0h0m`, never absolute clock times.
- **Dependencies:** [P5]
- **Tests:** AC15 (`3h42m` for a reset 3h42m out), AC16 (`0h0m` for
  expired/current, no absolute timestamp), new tests: Agy `'+3h'` input
  produces a valid ISO `reset_iso` (parseable by `fromisoformat`) and a
  correct countdown; a malformed delta string yields no reset display
  rather than a crash or a bogus timestamp.
- **Risks:** Converting `+3h` to absolute ISO fixes the clock at scrape
  time — acceptable drift within the cache TTL; note it in code. Any other
  producer writing non-ISO strings into `reset_iso` would reintroduce the
  bug — grep every writer of the key and cover each in tests.

### P15: Render retained stale data instead of degrading to `?`
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Fix finding M3 (split out of the former P10 scope). Stop
  degrading retained cache to `?`: when a provider errors transiently but
  retained data exists (retention logic near __init__.py:741), renderers
  must show the retained values with a stale marker instead of
  short-circuiting to `?` (or, if product chooses otherwise, stop
  retaining; the plan's default is render-with-marker since the retention
  test already exists). Sweep every `_render_*` function (Claude, Codex,
  Gemini, GLM, DeepSeek) so the stale behavior is uniform across providers.
- **Dependencies:** [P10]
- **Tests:** Per provider renderer: cached data plus a fresh transient
  network error renders the retained values with the stale marker rather
  than `?`; no retained data plus an error still renders the existing error
  form; a subsequent success clears the stale marker.
- **Risks:** The stale marker lengthens segments and shifts 60-column trim
  boundaries — P11's width sweep runs after this step (hence its
  dependency). Stale rendering must not apply to auth-suppressed providers,
  which R15 requires to be omitted from output entirely.

### P11: Rendering requirements regression: short codes, windows, filtering, width
- **Files:** [__init__.py, test_quota_status.py]
- **Description:** Re-verify (and repair if any prior step disturbed them)
  the v2 rendering requirements: Gemini renders `Ge` and never bare `G`
  (R7), GLM renders `G` (R8), Claude shows the 5-hour and 7-day windows —
  both when both are available, the single one otherwise, with no
  placeholder for a missing window (R9/AC27), Codex likewise for
  primary/secondary windows (R10/AC28), `quota_status.providers` from the
  Hermes `config.yaml` is honored as a case-sensitive allowlist
  (R11/R12/AC29), the complete status string is ≤60 characters when the
  renderer is told the terminal is ≤60 columns and untrimmed when wider or
  unknown (R14/AC17/AC30), trimming never emits partial control sequences or
  malformed separators (AC18), and Claude/Codex/Gemini each still produce
  exactly one segment when credentialed, allowed, and populated (R18/AC22).
- **Dependencies:** [P15]
- **Tests:** AC9–AC14, AC17, AC18, AC22, AC27–AC30. Add a
  trim-boundary test with a multi-provider string that crosses 60 chars
  mid-segment and assert the output stays a single well-formed status
  string.
- **Risks:** P15's stale-marker rendering and P10's countdown fixes
  lengthen segments and can change trim boundaries — run width tests after
  both (hence the dependency on P15, which depends on P10).
  Case-sensitivity tests must cover `GLM` (uppercase) not enabling `glm`.

### P12: Dead code removal and OAuth secret provenance
- **Files:** [__init__.py, gemini_cloudcode.py]
- **Description:** Fix findings N1 and N2. Delete the dead
  `_fmt_reset_hours()` helper (__init__.py:363) that shadows the live
  `_fmt_reset` / `_fmt_hours_until` helpers, confirming no references remain
  (including tests). In gemini_cloudcode.py (:27), add a provenance comment
  above the hard-coded Google OAuth client id/secret: where it comes from
  (Google's published installed-app credentials for the Cloud Code /
  gemini-cli flow), that installed-app client secrets are not confidential
  by design, and the ToS caveat that it is a third party's credential.
- **Dependencies:** [P11]
- **Tests:** Full existing suite passes after removal; ensure no test
  imports or monkeypatches `_fmt_reset_hours` (search and update any that
  do).
- **Risks:** Minimal; only risk is a hidden dynamic reference — search for
  the symbol name across the repo before deleting.

### P13: Plugin metadata and full-suite requirement verification
- **Files:** [plugin.yaml, test_quota_status.py]
- **Description:** Cover R19 and close out R20. Update `plugin.yaml` to
  document Claude, Codex, Gemini, GLM, and DeepSeek as the supported
  provider set and to expose/describe the `quota_status.providers`
  configuration key with its case-sensitive values (AC23). Audit
  `test_quota_status.py` against the AC24 checklist (GLM success,
  credential priority, host fallback; DeepSeek success; `Ge`/`G` codes;
  Claude and Codex dual- and single-window rendering; filtering; future and
  expired countdowns; 60-column trimming; 3-failure suppression; non-auth
  failure neutrality; recovery via background check) and add any test the
  earlier steps left uncovered so every requirement id R1–R19 has at least
  one passing test. Run the full pytest suite and fix any residual
  failures.
- **Dependencies:** [P1, P2, P3, P7, P8, P14, P9, P10, P15, P11, P12]
- **Tests:** A metadata test asserting `plugin.yaml` lists all five
  providers and the `quota_status.providers` key; full-suite run
  (`python -m pytest test_quota_status.py`) green.
- **Risks:** plugin.yaml is currently minimal — keep the schema Hermes
  actually loads; verify against the Hermes plugin loader expectations
  rather than inventing keys. Requirement-to-test mapping drift: keep an
  explicit test-naming convention (e.g. `test_r15_...`) so AC24's
  enumeration stays auditable.

## Ordering rationale

Execution order is the document order: P1, P2, P3, P4, P5, P6, P7, P8,
P14, P9, P10, P15, P11, P12, P13. Steps P14 and P15 were split out of the
former P8 and P10 during the fix round; their ids continue the numbering so
pre-existing ids stay stable for finding tracking, and they are placed in
the sequence where they must run — every step's dependencies precede it in
this order.

P1–P3 are self-contained `quota_api.py` fixes (non-finite validation, GLM
fallback breadth, Gemini transport/auth classification) with no dependency
on plugin-layer changes, so they go first and de-risk everything
downstream. P4 fixes the scraper module in isolation (fail-closed parsing,
provider tags, collision-safe tmux sessions) and must precede P5, which
rewires the Gemini orchestration in `__init__.py` to consume the new
tagged, best-effort scrape. P6 builds on P5's settled fetch paths to fix
auth classification, and P7 then locks the whole R15–R17 suppression
lifecycle with regression tests and makes the counters thread-safe. P8
(per-provider refresh workers) reshapes the refresh loop P7 adjusted, so it
depends on P7 and inherits its locking discipline. P14 hardens the Agy API
HTTP reads (context management, 1 MB cap) as its own step, needing only
P6's error classification. P9's keyring hardening (exact matching, deadline
wrapper, token cache) sits inside code paths P8's workers call, so it
follows P8. P10 fixes the reset_iso data contract, and P15 then changes
retained-error rendering to stale-with-marker across all renderers; both
change segment content, so the width/short-code/window rendering regression
sweep (P11) runs after P15. P12 cleanups come once the rendering helpers
stop moving, and P13 finishes with metadata plus the full-suite audit that
proves every requirement and finding is covered.

Finding-to-step map: C1→P4, C2→P6, C3→P4, C4→P8, C5→P5, C6→P4+P5, C7→P1,
C8→P3, M1→P14+P9, M2→P10, M3→P15, M4→P9, M5→P2, N1→P12, N2→P12.
Requirement-to-step map: R1–R3→P2, R4–R6→P1, R7/R8→P11, R9/R10→P11,
R11/R12→P11, R13→P10+P11, R14→P11, R15–R17→P6+P7, R18→P11, R19→P13,
R20→P13 (plus per-step tests throughout).
