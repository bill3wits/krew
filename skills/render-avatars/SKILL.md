---
name: render-avatars
description: Bring a Krew mate to life on the wall from one reference photo — bust shot, scene, hub plates, core loops, slides, flourishes, the talking harvest, intake, and how mouth tiles are chosen (the 19 classes with their shapes). The formula Lila went through on; use it for Bash and every face after.
version: 0.1.1
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, avatar, comfy, ltx, vera, lila, bash, mouth, takes]
    related_skills: [krew-bring-to-life, krew-render, render-lane, pose-flourish, krew-deliverable]
---

# Render avatars — the formula (crew do-list)

A face on the wall is a LIBRARY: a folder of loops that start and end on
hub stills, plus a bank of mouth tiles the conductor pastes while the voice
speaks. Lila's is `display/lila-poses/` and `~/Desktop/LilaPoses/` — open
them; every step below has her file as the worked example. The the user's
page for the same loop is the krew docs.

## 0. Before you start

- Files you use must be absolute host paths under `~/Desktop/<Name>Poses/`
  (or `~/.krew/crew/display/<name>-poses/`). Never point a tool at a
  card attachment path; copy the attachment there first. If an image tool
  still says "only base64 data", read the file and pass
  `data:image/png;base64,<base64 -w0 file>`.
- Every render is 1296×704 for plates and Grok takes; krew LTX loops are
  768×416 (multiples of 32) and are cut/scaled to 1296×704 at intake.
- Look at every output with `vision_analyze` before you post it. Numbers
  first (NCC against the plate), your eyes second, three lines on the card.
- One changed sentence per retry. Three takes per step, then stop and say why.

## 1. The bust shot (the anchor)

Start from the user's reference photo. Ask the image editor (`image_generate`,
`model="qwen-edit-2511"`, `image_url` = the reference) for an **in-place
edit**: same person, same clothes, same wall, only the head turned to a
full-face view, chin level, eyes on the lens, **closed lips**. Keep the
hair as it is unless the user says otherwise; hair is what every renderer
loses first, so the anchor's hair must be one you can describe in a line
("high ponytail over her left shoulder", "short dark curls").
Lila: `~/Desktop/LilaPoses/00-ANCHOR-lila-face.jpg` (five passes; the user
picked the fourth). The anchor is the seed for EVERYTHING after; never seed
from your own last output.

## 2. The scene

One room per face, distinct from the others at wall size, then locked (never
re-shot). Rules: warm even key light (a window to one side, a small lamp on
the other), no colour cast; one accent colour (Crissa turquoise, Lila
terracotta); texture behind, no readable words, no fine repeating patterns
(they turn to noise at 736 wide); a pale desk; **the laptop lid runs along
the bottom edge of the frame and hides the hands**; camera at webcam
height, straight on. Lila's room brief and prompt: the krew docs
and `~/Desktop/LilaPoses/lila-hub-down-src.jpg`.

**Do not one-shot two-pic.** `qwen-edit-2511` keeps the framing of picture 1
and the *clothes/hair* of picture 1. One call “replace the woman with the
man from picture 2” is how `bash-plate-2pic-try1.png` and `qwenedit_00115_`
shipped: his face, her ponytail, earrings, white top. Drawn FROM a bust,
the plate is a bust (`WRONG-bash-plate-20260911.png` — huge head, no lid).
June NR200P: edit the real still in place; do not generate a cousin.

**Four `image_generate` calls, in order. Look after each (`krew-vlm`
identity brief). Stop on FAIL.**

1. `model=qwen-edit-2511`, `image_url` = mate's finished plate, **no**
   `reference_image_urls`. Prompt: replace the woman with a man, short
   dark curly hair, black hoodie, same place and pose. Hair of hers must
   go. Look: still a blonde ponytail → FAIL, do not proceed.
2. That PNG as canvas, `reference_image_urls` = [take-8 / the anchor].
   Prompt: replace **only the head** of the man in picture 1 with the man
   in picture 2. Look: face is not take-8 → FAIL.
3. White clothing survives every prompt. Do not ask the model to recolour
   the shirt. Paint the torso (pixels min-channel > 185 → RGB 30,30,33),
   then `qwen-edit-2511` “clean the black hooded sweatshirt, uniform matte
   black, natural folds”. Look: white blouse or her earrings → FAIL.
4. Desk plate + “sat back a little, eyes on the lens, closed lips, chin
   level” → comms / forward plate.

Museum: real photo → Qwen-edit in place → WAN/LTX from the **approved**
still. Weights stay `qwen-edit-2511`. Crew 27B looks. Do not swap Muse
onto the crew engine.

## 3. The hub plates (one per station)

| station | plate | what she does |
|---|---|---|
| desk (`down`) | heads down at the laptop, eyes on the screen | the work core |
| forward (`back`) | sat back a little, eyes on the lens | listening / talking core |
| close (`down_close`) | pushed in, lid along the bottom | FaceTime (optional at first) |

Each plate: the anchor's face and hair line, the scene brief, closed lips,
1296×704. Lila's: `lila-hub-down-src.jpg`, `lila-hub-comms-src.jpg`. The
plate is frame 0 and last frame of every loop on that station (Law 1).

**One render per card .** A render card blocks once: submit,
block with reason "render pending", and collect re-readies it for the
judging turn. Kanban's block-loop detector sends a card to triage on the
second block of the same kind, and triage is a dead end for the tools
(only `hermes kanban specify` brings it back, retitled). So the work loop,
the listen loop and each slide are their own cards; never queue the next
render on a card that has already blocked.

## 4. Core loops (silent, krew renderer)

```
python3 ~/.krew/crew/scripts/render-submit.py --card <id> --name <name>-work \
  --recipe ltx25-flf2v --size 768x416 --seconds 8 \
  --seed <plate.png> --plate <plate.png> --prompt-file <prompt.txt> --out-dir ~/Desktop/<Name>Poses/renders
```
One call, then END YOUR TURN; render-collect lands `take-N.mp4` + stills +
NCC on the card and re-readies it. Prompt = Lila's working-loop prompt with
the person swapped (`~/Desktop/LilaPoses/lila-work-loop.txt`): eyes on the
screen, head level, small typing shoulders, one blink, closed mouth, "the
first frame and the last frame are exactly this still", "nothing happens".
Bar: both ends ≥ 0.97, eyes never above the lid, hair and clothes held, no
smile, no scale breathe. Listening core: same from the forward plate, eyes
on the lens, **lips do not move** (`lila-listen-loop.txt`).

## 5. Slides (transitions between hubs)

`--last <other plate> --guide 1.0 --seconds 3` on the same recipe: the first
frame is one hub, the last is the other. Then cut at the last frame that
scores ≥ 0.975 against the target and dip four frames onto the hub still
(render-collect scores; the cut is the intake's job). Lila:
`clip-lila-sit-up.mp4` (down→back), `clip-lila-sit-down.mp4` (back→down).

## 6. Flourishes

One small act each, from and back to the same plate: a glance to the side,
a stretch, a note, a sip. 6–8 s. Prompt names the ONE thing that moves and
says everything else stays ("head level, eyes back on the screen, the
ponytail barely stirs"). Same bars as a core. Never a look at the camera on
a desk flourish; never a smile unless asked.

## 7. The talking harvest (needs the user)

The krew renderers are silent. A mouth bank needs speech filmed WITH its
audio — the user makes it (Grok) from the forward plate: the natural line,
said not performed, head still, closed lips between words:
> Yes, hello there — the fox chose a very quick path through those old wooden gates, then she laughed.
Ask for it on the card and stop; do not fake it.

## 8. Intake (into the face's own library)

**A take that carries its own speech (a Grok host line, a talking take)
goes through the reference install's host-line intake, which neither krew
nor Crissa ships.** Post the take, its line and the speech window measured
from the clip's own audio on the card and stop: the owner does the intake
on the machine that has it. Never run an intake from a card.

Silent loops (the krew renderer) do not need it — by hand, with
`KREW_POSE_DIR=~/.krew/crew/display/<name>-poses`:
crop Grok takes to 1296×704 (`crop=1280:695:0:12,scale=1296:704`), dip a
loop's last 10 frames onto its own frame 0 if it does not close (seam ≥ 0.97),
inventory row (hubs `down`/`back`, `element: true`, `kit.keys`), then
`pose-head-frame.py --clips <stem> --ctx 0`, `pose-freelance.py finish <stem>` (reads THIS clip's mouth from its head track; never `pose-mouth-harvest.py` — it rewrites every sidecar in the library, and T0 refuses it from the crew),
`pose-loop-playlist.py <key> --harvest --ctx 0` for the talking take. Each in
`systemd-run --user --scope -p CPUQuota=300%`. The Doctor and Takes page for
that face run on their own ports (`KREW_DOCTOR_URL`, `--port`).

## 9. Mouth tiles — what the user picks, and what makes a good one

**A mouth tile is her FACE, not her mouth** (the user: every earlier new face
ended in small circle mouths because the crop did not scale with the zoom). The cut
reaches her cheekbones and the tip of her nose, with a gap of neck below the jaw, and is
marked `_fc1`. A frame with no face data is refused (`FACE CROP REFUSED`), never cut
small. `pose-hub-check.py --strict` fails a live playlist that paints anything else.
Render recipe that worked first time: raw at x704, 10 frames on each end for the anchor
attachments.

The Takes page stamps one FRAME per class from the talking take; rank 1 of
each class goes on the loops; a vowel stacked from the same take within 12
frames becomes a walk. You do not stamp (the bank is the user's eye), but
you must know the shapes to judge a take and to describe them on a card.

| class | the shape (what the frame must show) | the drill syllable |
|---|---|---|
| rest | lips closed, relaxed, teeth hidden — the idle mouth and after `.!?` | (between words) |
| rest-open | lips just parted, jaw slack — a mid-sentence pause | (between words) |
| MBP | lips pressed shut for m/b/p, slight bulge | Mm |
| FV | upper teeth on the lower lip | Four |
| TH | tongue tip between the teeth | Thee |
| TDN | tongue up behind the upper teeth, lips parted narrow | Dee |
| L | tongue tip up and visible, jaw open a little | Lee |
| R | lips slightly rounded, corners in, small opening | Ree |
| KG | jaw open, back-of-tongue sound, lips neutral | Kay |
| S/Z | teeth nearly together, lips drawn back a little, a thin slit | Ss |
| SH/CH | lips pushed forward, squared, teeth close | She |
| EE | wide, corners back, teeth showing, small vertical gap | See |
| EH | half open, relaxed width, teeth just visible | Eh |
| ER | slightly rounded, mid-open, tongue back | Er |
| AH | jaw open, relaxed, the widest vertical opening, tongue low | Ah |
| OH | rounded, mid-open, lips forward | Oh |
| WOO | small tight round opening, lips pushed forward | Who |
| Y | corners back, narrow gap, like a quick EE | Yay |
| H | open, breathy, like AH but softer | Hah |

A good tile: the shape at its peak (not arriving, not leaving), head at the
station's pose, no motion blur, no hand or hair across the mouth, lips not
over-acted (open median ≈ 0.45 height/width, p95 ≤ 0.65 — Lila's less-mouth
take, not the wide one). A bad take: manic lips (profiles at 0.7+), mouth
under 100 px wide in frame, a smile baked in, the head turning mid-word.

## 10. Deliver

The take folder under `~/Desktop/<Name>Poses/renders/` (collect writes it and
`krew-renders/<name>/`), the plates in `~/Desktop/<Name>Poses/`, a README
naming the anchor. On the card: numbers, then what your eyes saw, then the
one sentence you would change next. Never install; never touch
`inventory.json` or a playlist; the user looks first.

## 10. Mouth on the wall — what a library must carry (, Lila's first day)

Everything below was found on Lila the day her bank went live. The user: "how do
we lock this in properly. Feel like traveled territory to me." It is; the
lock is this list, the installer's lint and the dials file.

1. **Every stamped tile needs a `head` record in map.json.** The conductor's
   head paste (`pin=nose-pixel`) reads the tile's own head frame from the map
   entry; with none it returns nothing and the old Haar-box paste runs for
   every class but rest — mouths float, jitter and come out the wrong size
   while the dials still *say* nose-pixel. After the harvest, with the
   library's Doctor STOPPED (it holds the pen on map.json):
   `KREW_POSE_DIR=display/<face>-poses python3 scripts/pose-head-frame.py --map --clips <take,take,…>`
   then start the Doctor and restart the conductor. `pose-install-picks.py`
   refuses a rank-1 stamp without a head. The conductor's state `skip` says
   "no tile head … box paste" when it happens anyway.
2. **The library carries its dials.** `display/<face>-poses/mouth-dials.json`
   boots the conductor (env still wins). Seed a new face from Crissa's:
   affine · fit take · nose-pixel · arch · head · scale 1.0 · nose 0/−8 ·
   ema 0.35, `fit_under_pixel: true`, `pin_hold: false`. Those two are
   dials, not laws: the laws are rest = a translation on its own axes + the
   pixel match and classes = dest axes + take-width fit.
   Flip them only through "vs default" with the user's eye on the stills.
3. **Tune by single elimination on the Composer.** Hold a class, set the
   dials, press "vs default", click the winner; the winner is the file.
   Say drops a held class first. Live on the stage is the conductor's own
   stream, the thumbnail is only a glance.
4. **Scale is universal** (one multiplier for the whole library). If one
   vowel is a performed extreme (take B's AH), that is a stamp to replace on
   the grid, not a dial.

## 11. Register / onboarding intake (from the Init lane)

A 720p talking take is not blocked any more. What the intake does to each
clip, so render cards and Composer requests can ask for it as the landing
step:
- crop to the krew frame and scale to 736×400;
- measure her speech from the clip's own audio, cut to 0.55 s before the
  first word and 1.0 s after the last, then bake a 10-frame dissolve from
  her plate on the front and back to it at the end (every clip starts and
  ends on the plate at 0.999);
- stop the audio 0.3 s after her last word — the audio length is the host
  beat, the register's beep and the wizard's ear follow her words;
- a row role of `host` is not in the conductor's vocabulary (it crash-loops
  the conductor): rows are `oneshot`; and the two leave slides live in the
  kit so a forced play has a way home.

## 12. The phoneme storm — the staple harvest (ensconced)

The user: "This is the single best phoneme tile source clip. Ensconce this
forever as the staple clip to harvest for all new avatars." It was an
accident: on the Imagine prompt for the close-up shapes take
(CLOSE-3, `harvest-shapes-close-screen-tail`, 1280×720, 15 s) listed the
mouth shapes as *direction* — and she read the direction out loud, holding
each shape as she named it. ASR heard all eight. Every note, at its peak,
said not performed, one after another. Nothing we wrote on purpose did that.

**The formula.** The still is the station's plate (desk, forward, close…).
The spoken line IS the instruction list — write it as her words:

> Same woman/man, same hair, same clothes, same desk, laptop and studio as
> this still. Fixed camera, no zoom, no cut, head still, eyes where the
> station looks (the screen for desk, the lens for forward). She reads the
> instructions aloud, slowly and clearly, holding each mouth shape as she
> names it, lips closed between sentences: "Lips pressed shut for m. Upper
> teeth on the lower lip for f. Tongue tip between the teeth for th. Tongue
> up behind the teeth for d. Tongue tip up jaw a little open for l. Lips
> slightly rounded jaw nearly shut for r. Jaw a little open tongue back for
> k. Teeth nearly together lips drawn back for s. Lips pushed forward and
> squared for sh. Wide smile teeth showing for ee. Half open and relaxed for
> eh. Lips rounded jaw mid for er. Jaw open relaxed tongue low for ah. Lips
> rounded and forward for oh. A small tight round opening for oo. Narrow
> smile jaw nearly shut for y. Jaw open relaxed a breath out for h. Then
> lips shut again." No smile, no nod, no hand. Ends on the still, closed
> mouth. 1296×704, with audio.

The first eight sentences are CLOSE-3's, verbatim; the rest extend it to
all nineteen classes in the same cadence. **Use it:** one storm per station
for every new avatar, and whenever a loop's mouths look weak on test, film
the storm at THAT loop's station and re-stamp from it. The Composer's Say
box carries this line by default; "test tiles" says it on a loop.

## 13. Mouth size — what the take's mouth does to every tile 

The tile carries the take's articulation, and the paste scales it only by
head size (fit=take corrects the lens, not the mouth). So a take that
PERFORMS the line — wide AH, big pursed OH — lands lips 1.3× her own on the
loop; every tile floats. Lila's first comms take was that; the redo "with
even less mouth movement" (take B) won the ranking on nearly every class.
- Ask Grok for the smaller mouth every time: "natural, quiet, unhurried,
  mouth small and relaxed, closed lips between the words" — and ask twice,
  natural and "even less mouth", and rank the pair on the grid.
- Scale on the dial panel is one multiplier for the whole library; it is
  not a fix for one performed vowel. A performed vowel is a stamp to
  replace.
- The storm (§12) is said slowly with each shape held — that is the shape
  at its peak without the swing that a performed word adds.
- The hinge (`avatars/<name>/manifest.json` head_geom kw/kh) sizes the hole
  the tile is cut to, not the tile; widening it did nothing for a
  too-big tile. The face boxes in that manifest are measured per face.

## 14. The tiles drill — intake step, every face, every talking harvest 

**Do this after §8 intake and §9 stamps, before the loop is called done.**
The drill is the pacer car: the harvest film plays on that face's conductor
locked frame-for-frame to its OWN voice, the tiles paint over her filmed
mouth, and the report says how early or late the tiles ran against her real
mouth, how well the shapes followed (r), the change rate, and where the tile
landed against her mouth box. It is the one instrument that measures the
mouth against the underlying render instead of an eye.

1. Composer → load the harvest film from the library (Source films) →
   **tiles drill** in the transport row. Words = the Say box (empty = the
   staple line of §12, which is what every staple harvest says). It loops;
   each pass writes a row to the dial journal (Tune box → **history**).
2. Read the pass: `tiles lead her mouth by N ms (r …) · changes/s · … ·
   landed dx/dy px of her mouth, width ×w`. The lead should read 0 with
   the sheet lead at 0; |dx|,|dy| small against her head; width ×1.00.
3. Optimize the clock, once per face:
   `python3 scripts/mouth-drill-optimize.py --conductor http://127.0.0.1:<port> --key <harvest key> --quick`
   (stage 1 = lead; drop `--quick` for holds and crossfade). It applies the
   winner live and logs `logs/drill-opt-*.json`; it never locks.
4. **The the user locks** (Tune box → vs default, or a journal row → lock) and
   ticks the reference row. Record the read on the loop's row (`looked:`).

What the drill measures and what it cannot: timing dials (sheet lead,
holds, crossfade, first lead) and placement (pin, fit, scale, nose u/v)
have numbers. Warp, edge, sag and the hole are seams — the film's own
mouth is already the truth there, so "do nothing" would win any pixel
score; those stay the user's eye, single elimination, on the same drill.
The timing numbers are properties of the clock, not the clip: one
optimisation per face and per engine/tempo change is the global truth;
re-run when the clock, the tempo or the aligner changes, not per clip.
