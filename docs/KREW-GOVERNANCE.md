# Krew governance

How a design or a change is allowed to ship. One rule on this page.

## Second-order sweep

Every design or change card lists what else the change triggers, and each line is checked against the intended design before the card ships:

- presence / Secure
- user mode
- gateways
- janitor
- self-healing and sweep loops
- kanban routing
- voice / Wall services

A line says `unchanged`, or it names the trigger and the design it was checked against. A side effect that is not in the design is a bug, even when the main change works.

Origin: 2026-10-08. A Render switch dropped Secure. That effect was not in the design.

The card template carries the list as a required field. Ada files it on the card (`skills/ada-orchestrate/SKILL.md`, `## Second-order sweep (required)`). Lila (design) and Bash / Steward (a change) check it before ship (`skills/adversarial-gate/SKILL.md`).
