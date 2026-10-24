---
name: adversarial-gate
description: >-
  Single-stream or dual hot/cold adversarial review before shipping design or
  code. Use for non-trivial design cards and implementation cards.
version: 1.0.0
author: krew
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [review, adversarial, design, code, quality, subagent, krew]
    related_skills: [plan, requesting-code-review]
---

# Adversarial gate

Prefer this skill over inventing a one-off review prompt.

**Does not replace:** Ada's board routing, a separate Vera
card, or Hermes `--goal` / `goal_judge`. Those stack **outside** this skill.

**Who loads it:** Lila (design cards), Bash and Steward (implementation
cards). Same procedures.

### Why an in-run adversary (not a different profile)

Hermes `delegate_task` children get the **parent's toolsets** (or a *subset*;
they **cannot broaden** parent permissions). So:

| Producer | Adversary sub can use |
|----------|------------------------|
| **Lila** | Lila's tools: skills, design stack, browser/vision, web |
| **Bash / Steward** | the coder's tools: terminal, file, skills, the same working tree |

A **Vera board card** is a *different* process with Vera's kit
(good for release gates; not the same as "Bash's hands reviewing Bash's
diff"). Use this skill when the reviewer should **read the same tree,
run the same probes, load the same design skills** as the author.

---

## When to use

| Use | Skip |
|-----|------|
| Design doc / moodboard / UI information architecture | One-line rename, pure "read and report" |
| Non-trivial code, refactor, mitigation | Trivial fix with no judgment |
| Spec from **refs** (card, design doc, docs, measured probes) | No refs and no acceptance criteria |

When unsure, **use Mode A** at minimum.

---

## Inputs (always assemble first)

```text
BRIEF     = card title + body + acceptance criteria
REFS      = paths/docs/design doc/attachments + external refs (cite names)
DRAFT     = design section or plan/diff summary (must exist before attack)
LAWS      = your project's standing rules, if code (write them down once;
            e.g. single source of truth, no secrets in code, release gate
            for production paths)
SWEEP     = the card's Second-order sweep (required on a design or change
            card): presence/Secure, user mode, gateways, janitor,
            self-healing and sweep loops, kanban routing, voice/Wall
            services. Each line is unchanged, or the trigger checked
            against the design. A side effect that is not in the design
            is a block.
```

Mark unmeasured claims **ASSUMED**.

---

## Mode A: single-stream adversarial gate (default)

One author stream; one attack sub; you synthesize.

```
1. GATHER   refs + brief
2. DRAFT    your best deliverable (design doc or implementation plan/diff)
3. ATTACK   spawn ONE in-run sub (delegate_task / child), review only
4. REVISE   fix block + should; nits optional with a one-line reason to skip
5. SHIP     deliverable + a short "Adversarial review" section in the complete summary
```

### Attack sub brief (paste)

```text
You are adversarial review ONLY. Do not implement. Do not reassign board cards.
Given:
  BRIEF: ...
  REFS: ...
  DRAFT: ...
  LAWS: ... (if code)
  SWEEP: ... (design or change card; a side effect that is not in the design is a block)

Return markdown:
## Attacks
For each: severity (block|should|nit), claim, evidence (ref/brief clause), fix hint.

## Verdict
pass | revise | reject
One sentence why.

No new product vision. No second design from scratch unless the draft is empty.
```

### Severity

| Level | Meaning |
|-------|---------|
| **block** | Must fix before ship |
| **should** | Fix unless skipped for time, with a reason |
| **nit** | Optional |

**Ship rule:** zero open **block** items. Summarize remaining should/nit.

---

## Mode B: dual hot/cold + adversarial + synthesis (complex / qualitative)

Use when the card is **taste-heavy, multi-option, or high blast radius**
(home screen, voice behaviour, production-adjacent config). It costs more
seats, so use it sparingly and stay within your spawn depth and concurrency
settings.

```
        REFS + BRIEF
              |
     +--------+--------+
     v                 v
  HOT draft         COLD draft
  (creative)        (conservative)
  high temp*        low temp*
     |                 |
     +--------+--------+
              v
     ADVERSARIAL sub (one)
     attacks BOTH drafts vs REFS
              v
     YOU synthesize the best of both
     + fix blocks
              v
     SHIP + Adversarial review
```

\*If the API exposes temperature / reasoning_effort on children, set **hot**
higher (more options) and **cold** lower (strict, minimal, criterion-bound).
If children **cannot** set temperature, still run two parallel briefs: one
"invent options, bold", one "minimal change, safety-first".

### Hot sub brief

```text
You are the HOT design/code draft. Maximize useful options and taste.
Given BRIEF + REFS only (no prior draft).
Return: one coherent proposal (design sections or implementation plan).
Label assumptions ASSUMED. Do not attack; do not implement beyond the plan/spec text.
```

### Cold sub brief

```text
You are the COLD design/code draft. Minimize risk and scope.
Given BRIEF + REFS only.
Return: the smallest change that meets acceptance; call out what you refuse to invent.
Label ASSUMED. No decoration. Do not attack.
```

### Adversarial sub (after both drafts land)

```text
You are adversarial review ONLY.
Given BRIEF, REFS, HOT_DRAFT, COLD_DRAFT, SWEEP.
Compare both to REFS/acceptance. Prefer neither by default.
A side effect that is not in the design is a block.
Return: attacks per draft (block/should/nit), what to steal from each,
and a recommended merge direction (hot|cold|hybrid) with reasons.
Do not implement. Do not reassign board cards.
```

### You (parent) synthesize

1. Merge the best ideas; **block** items fixed in the final artifact.
2. Prefer cold for safety-critical paths; hot for visual/IA when refs support it.
3. Final **Adversarial review** section: hot/cold/adversary one-liners + what shipped.
4. Complete the card with paths/commands a reviewer can trust.

---

## Mode choice (simple)

| Situation | Mode |
|-----------|------|
| Clear design doc, mechanical implementation | **A** |
| Ambiguous brief, multi-zone UI, first design | **B** |
| Change to a live production file | **A** (then Vera's release gate, separately) |
| Card already runs with `--goal` | **A or B still OK** inside the worker; the goal judge is separate |

---

## Anti-patterns

- Shipping without a **concrete DRAFT** before the attack.
- Asking the adversary to **rewrite the product**.
- Using Mode B for every one-liner (wastes crew).
- Turning the adversary into a **board assignee** (Ada routes; you stay in-run).
- Confusing this with a **Vera card** or release gate (stack them when Ada asks).

---

## Complete summary template (paste into the board's complete)

```markdown
## Adversarial review
- Mode: A | B
- Refs used: ...
- Blocks fixed: ...
- Should skipped (why): ...
- (Mode B) Hot contributed: ... Cold contributed: ...
- Second-order sweep: each line checked against the design
```
