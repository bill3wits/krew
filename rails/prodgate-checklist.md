# Vera's (Reviewer) Pre-Flight Checklist

**Use before every prodgate handoff.** The `prodgate check <gate-id>` command runs this automatically.
Items with [AUTO] are verified programmatically; [MANUAL] require bash's own judgment.

Also use **§0** when Ada asks “is this kanban card actually done?” (completion
acceptance — not a file stage). Work must **crystallize**.

---

## 0. KANBAN COMPLETE ACCEPTANCE [MANUAL]

**Reject `kanban complete` / mark incomplete** unless **≥1 crystal** exists:

| Crystal | Pass check |
|---|---|
| **A. Deliverable file** | Absolute path under the deliverables folder (`crystal.deliverables` in `$KREW_HOME/janitor.yaml`) or the card’s `dir:` workspace; `test -f` true; content is the work (not a restated title) |
| **B. Host action** | Apply/smoke/service/prodgate-land cited with measured result (command + outcome) |
| **C. Decision pin** | Row in the M&S decisions ledger (`crystal.decisions`) (`hold\|applied\|wontfix`) and/or an update to your pinned-deps findings doc for the packages/scope |

**Immediate reject:**
- Only long comments / “plan posted on card”
- New Desktop essay that **does not** add a pin or apply for scope already reviewed
- M&S “risk assessment” with no `hold|applied|wontfix` for each package in scope

**Pass note format:** `crystal: A|B|C · path=… · decision=… · smoke=…`

This does **not** limit how much work the board may carry — it forbids
**uncrystallized** “done.”

### 0.1 Mechanical gate [AUTO via plugin]
- CLI: `janitor/krew-crystal-check --summary '…' --artifact <deliverables>/….md` (exit 2 = reject complete).
- A `kanban_complete` wrapper that refuses without a crystal is the kanban loop's (not shipped in this package).

### 0.2 UNLOCK on applied [MANUAL]
Pip succeeding is not the advantage. For every `decision=applied` land:

> What did upstream claim? Is that path **on** in our start scripts / flags?
> How do we measure it?

Disposition (`unlock=` on the ledger row):

| | Meaning | Track on ops-efficiency? |
|---|---|---|
| **keep** | Helps, and the flag is on | Yes. `kpi_id` + measure. Ops tok/s: **bash** `scripts/ops-toks.py --lane ops --record` (CP `:8086`). Quiet-lane A/B: `--probe`. Not Crissa. |
| **toss** | Hurts | No. Roll back. |
| **land-only** | Neutral / security / hygiene | Land. Do not chart. Nightly greens default this. |

Reject complete: `applied` with no `unlock=`; or `keep` with `path_on=false`
(that is the fastsafetensors miss). Law: `docs/OPS-EFFICIENCY.md`.


---

## 1. PROTOCOL (Immediate reject on sight)

### 1.0 ZERO-DIFF (REVERT-FIRST) — READ BEFORE EVERY STAGE [AUTO]
> **This is the most common gate failure mode. 4+ occurrences, multiple rejections.**
> If the live file already differs from its original snapshot, re-staging copies the
> modified live file → zero diff → empty gate → immediate rejection.

**Procedure — follow every time:**
1. **Before staging**: check if live file ≠ git HEAD → `git diff <path>`
2. **If non-empty**: revert first → `git checkout <path>` or restore from backup
3. **Then stage**: `prodgate stage <gate> <path>`
4. **Edit ONLY staged copy**: `vi "$(prodgate path <gate> <path>)"` — NOT the live file

The prodgate guard (added 2026-06-25) now REFUSES re-stage when live drifted:
`"CHANGED since first stage"` → revert live, re-stage, edit staged copy.

### 1.1 Zero-diff staging (revert live before staging) [AUTO]
- The staged copy is byte-identical to the live file because the builder edited
  the live file directly then re-staged it. This produces a zero-diff stage that
  fails the reviewer on sight.
- **Check:** `prodgate diff` must show substantive changes. If diff is empty,
  revert the live file (git checkout or backup), then re-stage and edit the
  staged copy — NOT the live file.
- **Fix:** `cmd_stage` now rejects re-stage when live file changed since first stage.
  It compares `sha(live)` against `stage_sha` (sha of the staged copy from first stage).
  If they differ, the builder edited live → rejected with revert instructions.
  (Gate t_f47fa9bb — corrected from byte-compare which was always True post-copy2)

### 1.2 Ungated production write [AUTO]
- Staged copy differs from live file. Only the staged copy was edited.
- **Check:** `prodgate diff` shows substantive changes, not empty/whitespace diffs.

### 1.3 Submit for review — `kanban_request_review`, never block [MANUAL]
- After a non-empty `prodgate diff` and `prodgate check`, call
  `kanban_request_review(summary=..., reviewer=vera)`.
- Do **not** `kanban_block`. Do **not** wait for a human to type
  `prodgate approve` unless Vera rejected and Ada escalated.

### 1.4 NEVER `kanban_block` for prodgate review [AUTO via plugin]
- Review is **`kanban_request_review(reviewer=vera)`** — status becomes
  `review`, assignee **vera**. Ada's dispatcher then spawns Vera (she has
  **no** always-on gateway; that is by design).
- **`kanban_block` is not a review lane.** Blocking for "review-required"
  strands the staged gate: Vera is idle, nothing claims the card
  (measured 2026-08-12: `t_9bf46a4e`).
- A review-route hook may rewrite block/complete on an open gate into
  `request_review`; a keeper sweep unsticks anything that still lands blocked.
- **Do not** create a sibling "review card" unless Ada decomposes one.
  Gate id = builder task id.

### 1.5 Stale approval [AUTO]
- Staged files were edited AFTER approval. apply will void it.
- **Check:** Not applicable unless there's a prior APPROVED.json.

---

## 2. CODE QUALITY (Functional defects)

### 2.1 Syntax [AUTO]
- All .py files pass py_compile. All .sh files pass bash -n.
- **Check:** `verify_staged.py <gate-id>` exits 0 with no errors.

### 2.2 Eval on external data [AUTO]
- No `eval()` calls on non-trusted data.
- **Check:** Grep staged files for `eval(`.

### 2.3 Argument mismatch [AUTO]
- Function calls match current signatures.
- **Check:** AST pass in verify_staged.py flags suspicious call patterns.

### 2.4 Shared mutable variables [MANUAL]
- Every shared mutable variable has lock protection on reads AND writes.
- **Check:** Visual inspection of multi-threaded code.

### 2.5 SQLite parameterization [MANUAL]
- No dynamic IN lists passed via parameters.
- **Check:** Grep for `"IN ?"` or `"IN (" + ",".join` patterns.

### 2.6 Regex edge cases [MANUAL]
- Patterns tested against edge cases (word boundary vs substring).
- **Check:** Visual inspection or documented test cases.

### 2.7 Uninitialized variables [AUTO]
- All variables initialized before first use. Module-level = None for shared state.
- **Check:** AST pass flags names loaded but never assigned.

### 2.8 Dead code [MANUAL]
- No unused imports, unused constants, unused comments.
- **Check:** Visual inspection.

---

## 3. SPEC COMPLIANCE [MANUAL]

### 3.1 Feature completeness
- Task body features are all implemented.
- **Check:** Compare task body requirements against staged changes.

### 3.2 No scope creep
- No out-of-scope changes (removals, refactors not in task body).
- **Check:** Review prodgate diff for unintended changes.

---

## 4. HANDOFF QUALITY [MANUAL]

### 4.1 Pre-review sweep ran
- `verify_staged.py` was run and results reported in handoff.
- **Check:** Include sweep results in kanban_comment handoff.

### 4.2 Complete handoff metadata
- Handoff includes: changed_files, tests_run, gate_id, gate_status, concerns addressed.
- **Check:** Review handoff comment before creating review card.

### 4.3 Evidence in the completion summary [MANUAL]
- Every claim that drives a downstream decision cites the **real probe that
  produced it** — the command actually run, its exit code, and its output
  quoted (not paraphrased). "Verified", "passed", "works", "green" with no
  command + output on this card = **no evidence**.
- **Check:** each evidence-bearing claim has a command (or `kanban_show` /
  file path) plus quoted output from *this* run. Precedent: 2026-09-12,
  "runs cleanly (rc=0)" claimed over a builder that produced
  `python3 /usr/bin/python3 …` — the claim failed because the cited command
  was not the run command.
- Scope: the completion **summary** (what downstream workers read verbatim),
  not the whole work log.

### 4.4 Zero unresolvable task ids [MANUAL]
- **Every task id** named in the completion summary must exist on **this
  board**'s kanban DB at review time. A summary that names a card that does
  not exist poisons every downstream worker that reads it verbatim — the id
  is then trusted as a pointer to real work.
- **Check (cheap, real — run it):** resolve each id against the *current*
  board DB, one query per id:
  `sqlite3 "$(cat ~/.hermes/kanban/current)/kanban.db" "SELECT id FROM tasks WHERE id='t_XXXXXXXX';"`
  (the kanban loop uses the same `current` →
  `boards/<slug>/kanban.db` resolution.) Zero rows for any named id = phantom
  id = reject. Archived-board DBs (`boards/_archived/…`) are **not** this
  board.
- Existence-only: a real id that points at a completed or archived card is
  fine (it resolved); a wrong *status* claim about it is a 4.3 evidence
  problem, not a 4.4 one.

### 4.5 REVISE-before-accept (enforcement) [MANUAL]
- A reviewer (Vera) who finds an **evidenceless** completion claim (4.3) or a
  **phantom task id** (4.4) does **not** accept the completion. She calls
  `kanban_request_changes` naming the exact claim and id(s), and the
  completion is not accepted until the builder re-files a summary that passes
  4.3 + 4.4.
- `kanban_complete` on such a summary is a protocol violation of this
  checklist; log it in the Rejection Summary table below (gate id +
  "evidenceless summary" / "phantom id <id>") — same treatment as the
  existing reject rows.

---

## Rejection Summary (by gate)

| Gate | Rejection Reason | Key Defects |
|------|-----------------|-------------|
| t_db4bf8c9 | 3 material code defects | eval() crash, PASS/FAIL logic, positional arg bug |
| t_8292b203 | Ungated write + 5 defects | Prodgate violation, data race, uninitialized var |
| t_98692029 | Applied before re-approval | Protocol violation |
| t_5396971c | 10 defects | No gpu-image, local outside func, port conflict |

Run `krew-crystal-check` *before* `kanban_complete`: it fails fast when there is no crystal.
