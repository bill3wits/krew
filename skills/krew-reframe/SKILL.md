---
name: krew-reframe
description: Step up a level before the third try. Write the frame that lets the planner cut a different plan, for any crew member.
version: 1.0.0
author: krew
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [reframe, kanban, failure, decompose, crew]
    related_skills: [adversarial-gate]
---

# Reframe: look again from above

Models are weak at stepping back and looking again from above with a more
systemic mindset. So the step is written down, and it is the same text
whether a worker writes the frame on the second failure, a loop script asks
a model for it, or Crissa rules on two frames that did not land.
One method, any caller.

**When.** The second failure of the same shape on one card. A wedge that
keeps coming back. Any moment you are about to run the last attempt's steps
again. Not on the first failure: one sample is a shape, not a cause.

**What a frame is not.** Not a retry with more care. Not a longer error
report. Not a fix. It is the paragraph a colleague would write on the
whiteboard after saying "stop: what are we actually doing here?"

## Method

Write plain text, short (a paragraph a person reads in one go), no
headings, in this order. The numbers are the contract; whoever plans from
the frame reads them by number.

1. **Attempted.** One sentence: what the card was trying to make true, in
   the project's terms (a render finished, a package landed, a service up).
2. **Repeated.** The failure class, not the message: same error at the
   same step, N times. Name the step.
3. **Shared assumption.** The thing every attempt took for granted. Look
   one level up from the step: the lane (who holds the tool), the harness
   (floors, ports, interpreters, timeouts), the question (is this even the
   right package / unit / feed), the world (a service that answers 404 at
   night and 200 in the morning), the budget (VRAM, tokens, time). The
   assumption is usually a number or a "stays up" that nobody wrote down.
   **Put the numbers on the table first**: the residents, the need, the
   total, the floor, the timeout. Add them up. A frame that says "a shared
   pool" where "40 + 37 + 2 of 96, need 24" was available has not stepped
   up, it has stepped sideways.
4. **Two or three framings that drop it.** Each one sentence, each a
   different plan, none of them the old steps. If two of them are the old
   plan with a smaller number, you have not stepped up.
5. **The smallest discriminating test.** One measurement that says which
   framing is right before anyone builds anything. Name the command or
   the number to read.
6. **Hands or software.** If any step needs a body, a cable, a switch:
   say which; that part becomes NEEDS HUMAN. Otherwise write "software",
   which means the crew decides and the owner is not asked.

Post it as one board comment beginning `frame:`. Then block the card with
the exact error. Do not run the old steps. The planner cuts the next plan
from your frame.

## Three frames that were right (for calibration)

- **Smoke test red five nights.** Attempted: prove a model server boots.
  Repeated: the engine exits at start, every time. Assumption: a fixed
  amount of free VRAM is enough. Framings: (a) the floor must be
  utilisation x total, (b) the smoke test is not a verdict when VRAM is
  short, (c) the build is innocent either way. Test: read the first error
  line, not the traceback tail. Software.
- **A dependency fails to build three nights.** Attempted: keep a service
  current. Repeated: the sdist build fails on the new Python. Assumption:
  the package under this name is the one we run. Framings: (a) name
  collision, (b) requires-python, (c) the service comes from elsewhere.
  Test: the package index's last release date and summary. Software:
  won't fix.
- **A render cannot submit, "wedged".** Attempted: one more take.
  Repeated: submit refused, free VRAM under the take's need. Assumption:
  the LLM server's cache and the image server's staged model can both stay
  resident during a take. Framings: (a) trim the LLM cache for the take,
  (b) the image server unloads between takes, (c) takes queue behind an
  explicit VRAM lease. Test: sum the residents against the card; if it is
  over, no take was ever going to land. Software.

## Ruling on two frames (Crissa)

If a card reaches you at its last attempt with two frames on it, your first
word is `try` with the framing you pick by number, `park` with why, or
`hands` if question 6 named a body. Never ask the owner about software.
