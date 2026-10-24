---
name: xspaces-live
description: Run the live room lap. The user's own recorded questions play on the speakers, Crissa answers, and the announcer says Pass or Fail from the ledger. One lap at a time. A question with no recording is skipped.
version: 1.0.0
---

# xspaces-live

You run the lap. Crissa is the one who answers. You do not answer for her, and you do not grade her from memory.

## Run

From the Crissa checkout, one process:

```
ZOEYOS_DEMO=1 python3 scripts/xspaces-live.py
```

`--once` is a single lap. A second process refuses while the pid file is live. Stop with the off file in the Crissa data folder (`xspaces-live.off`). The env is off by default, so a boot does not start it.

Read the grade from `xspaces-drill/<day>/live-<HHMMSS>.jsonl` in that same data folder. Each row is one beat. Say Pass or Fail from those rows. A row with `skipped: no wav` was not played.

## When a beat fails

Fix the expect or the coaching text, then run the lap again. Do not invent a new sentence in the user's voice. Record a new take from the panel, or leave the question skipped.

Do not file a hero-clip, an ad, or an outbox card. Do not post to X. Do not restart the wall, the ear, or the conductor. Do not resize or fullscreen the browser. The film zoom is the lap's own hole, and the lap puts it back.

A question with no wav in `demo-lines/` is skipped. Do not point the lap at anyone else's recording.

## What you may change

`config/zoeyos-demo.json` expects and question text. A skill sentence that made her claim the setup is public, offline, or ready for two people.

Heard controls (`stop`, `yes go ahead`, `thanks that's all for now`) are listeners: stop pauses, yes repeats the last line, thanks ends the lap. They are not recordings.

Control Panel, Ears and mouth: Dev mode hides the tuning demo. Off, the lap stays off. On, Demo starts the lap on the speakers and Demo off stops it at the next beat. Record your lines saves each question. Hold (`demo-hold` in the Crissa data folder) waits the next beat so the user can speak. It does not pause Listening and it does not home the wall. Crissa's demo-hold kill switch (named in its scripts/demo-hold.py) ignores the file. Extra beats are `dev_beats` in `config/zoeyos-demo.json`. They run only while `demo-dev.on` exists in the data folder. A fail stays in the ledger, including an extra-beat fail. An extra beat does not pause the session.

## A dropped line

A short line the voice gate drops (`dispatch` level `silent`) is posted once more, the same PCM, and only when the dropped text is one of the recorded questions. That repost is the ear path. Do not synthesise a new line.

## Empty room

`ZOEYOS_DEMO_BROADCAST=1` plays the recorded question and does not post it to the ear, and it does not pause the session. Pause homes the wall. `zoey-hands` runs the beat's `job` when the question has one. A screen beat passes only when the reply contains `confirmed: shown`. A refusal is a fail.

Secure holds auth with no face in the camera. It is not a voice match. Do not change the ear so a recording counts as him.
