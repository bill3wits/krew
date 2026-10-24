#!/usr/bin/env python3
"""Markdown krew reports → copy/print HTML (M&S, research, architect notes).




Usage:


  python3 scripts/krew-report-html.py --file <slug>.md --kind zoey --title "…" --author Crissa


HTML … she needs a template"): the glass's own palette (GROUND / INK / EMBER
from display/voiceonly-home.html :root), a masthead with eyebrow · title ·
lede, KPI tiles from a `kpi:` line, callouts from `> ` quotes, tables, and a
sources footer. Skill: hermes-packs/skills/zoey-report. The markdown stays
the record; this is what he reads.
"""
from __future__ import annotations

import argparse
import html as htmlmod
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import INBOX, today  # noqa: E402

JOB_FILES = {
    "logs": ("ms-{day}.md", "logs-{day}.md"),
    "research": ("team-research-{day}.md",),
    "sources": ("team-sources-{day}.md",),
    "world": ("architect-world-{day}.md",),
    "stack": ("architect-stack-{day}.md",),
    "ops": ("ops-efficiency-{day}.md",),
}

CSS = """
:root{
  --bg:#0c0d10; --card:#14161c; --bd:#252833; --inset:#1a1d24;
  --tx:#e8eaed; --mut:#8b93a7; --ok:#3dd68c; --warn:#e6b84d; --bad:#f07178;
  --acc:#7eb8ff;
}
@media (prefers-color-scheme: light){
  :root{ --bg:#f2f4f7; --card:#fff; --bd:#d8dde6; --inset:#eaedf2;
    --tx:#181b22; --mut:#5a6274; --ok:#177a4b; --warn:#8f6a12; --bad:#b23d47;
    --acc:#2a63a8; }
}
@media print{
  :root{ --bg:#fff; --card:#fff; --bd:#ccc; --tx:#111; --mut:#555; --acc:#124; }
  body{background:#fff}
  .eyebrow{color:#555}
}
*{box-sizing:border-box}
body{background:var(--bg); color:var(--tx); margin:0;
  font:15px/1.55 ui-sans-serif,system-ui,sans-serif;}
main{max-width:920px; margin:0 auto; padding:32px 20px 80px;}
h1,h2,h3,code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
h1{font-size:1.4rem; margin:0; letter-spacing:.01em;}
h2{font-size:1.02rem; margin:28px 0 8px; color:var(--acc); letter-spacing:.04em;
  border-bottom:1px solid var(--bd); padding-bottom:6px;}
h3{font-size:.95rem; margin:18px 0 8px; color:var(--mut);}
.eyebrow{font-family:ui-monospace,monospace; font-size:11px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--mut);}
.sub{color:var(--mut); font-size:13.5px; margin:8px 0 0; max-width:70ch;}
.card{background:var(--card); border:1px solid var(--bd); border-radius:10px;
  padding:18px 20px; margin-top:14px;}
p{margin:0 0 10px;}
ul,ol{margin:0 0 12px; padding-left:1.3em;}
li{margin:3px 0;}
code{font-size:.88em; background:var(--inset); padding:1px 5px; border-radius:4px;}
pre{background:var(--inset); border:1px solid var(--bd); border-radius:8px;
  padding:12px 14px; overflow-x:auto; font-size:12.5px; line-height:1.45;}
pre code{background:none; padding:0;}
table{border-collapse:collapse; width:100%; font-size:13.5px; margin:8px 0 14px;}
th{font-family:ui-monospace,monospace; font-size:10.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--mut); text-align:left; padding:6px 10px;}
td{padding:8px 10px; border-top:1px solid var(--bd); vertical-align:top;}
hr{border:0; border-top:1px solid var(--bd); margin:22px 0;}
strong{font-weight:650;}
.foot{margin-top:36px; color:var(--mut); font-size:12px;}
"""


def _inline(s: str) -> str:
    s = htmlmod.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def md_to_body(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code: list[str] = []
    list_kind = None

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            if in_code:
                out.append("<pre><code>" + htmlmod.escape("\n".join(code)) + "</code></pre>")
                code = []
                in_code = False
            else:
                close_list()
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(ln)
            i += 1
            continue
        if re.match(r"^\|.+\|$", ln) and i + 1 < len(lines) and re.match(r"^\|[-:| ]+\|$", lines[i + 1]):
            close_list()
            headers = [_inline(c.strip()) for c in ln.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and re.match(r"^\|.+\|$", lines[i]):
                rows.append([_inline(c.strip()) for c in lines[i].strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
            out.append("</tbody></table>")
            continue
        if ln.startswith("# "):
            close_list()
            out.append(f"<h1>{_inline(ln[2:].strip())}</h1>")
        elif ln.startswith("## "):
            close_list()
            out.append(f"<h2>{_inline(ln[3:].strip())}</h2>")
        elif ln.startswith("### "):
            close_list()
            out.append(f"<h3>{_inline(ln[4:].strip())}</h3>")
        elif ln.strip() == "---":
            close_list()
            out.append("<hr/>")
        elif re.match(r"^[-*] ", ln):
            if list_kind != "ul":
                close_list()
                out.append("<ul>")
                list_kind = "ul"
            out.append(f"<li>{_inline(ln[2:].strip())}</li>")
        elif re.match(r"^\d+\. ", ln):
            if list_kind != "ol":
                close_list()
                out.append("<ol>")
                list_kind = "ol"
            item = re.sub(r"^\d+\. ", "", ln).strip()
            out.append(f"<li>{_inline(item)}</li>")
        elif not ln.strip():
            close_list()
        else:
            close_list()
            out.append(f"<p>{_inline(ln.strip())}</p>")
        i += 1
    close_list()
    if in_code:
        out.append("<pre><code>" + htmlmod.escape("\n".join(code)) + "</code></pre>")
    return "\n".join(out)


# ── Zoey's template: the glass's palette, a masthead, tiles, callouts ─────
ZOEY_CSS = """
:root{
  --bg:#10131A; --card:#151922; --bd:#242A37; --inset:#0c0f15;
  --tx:#E9E5DB; --mut:#8B93A6; --dim:#4A5163; --ok:#7FD1A8; --warn:#D9A05B; --bad:#E06C75;
  --acc:#D9A05B; --acc2:#F0C489;
}
@media print{
  :root{ --bg:#fff; --card:#fff; --bd:#ccc; --inset:#f3f3f3; --tx:#111; --mut:#555; --dim:#777; --acc:#8a5a1b; --acc2:#8a5a1b; }
  body{background:#fff}
}
*{box-sizing:border-box}
body{background:var(--bg); color:var(--tx); margin:0;
  font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;}
main{max-width:880px; margin:0 auto; padding:40px 24px 90px;}
.mast{border-bottom:1px solid var(--bd); padding-bottom:18px; margin-bottom:22px;}
.eyebrow{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--acc); margin:0 0 10px;}
.eyebrow span{color:var(--mut)}
h1{font-family:ui-sans-serif,system-ui,sans-serif; font-weight:650; font-size:1.9rem;
  line-height:1.15; margin:0; letter-spacing:-.01em;}
.lede{color:var(--mut); font-size:1.05rem; margin:12px 0 0; max-width:64ch; line-height:1.5;}
.tiles{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:18px 0 6px;}
.tile{background:var(--card); border:1px solid var(--bd); border-radius:8px; padding:12px 14px;}
.tile b{display:block; font-family:ui-monospace,Menlo,monospace; font-size:1.35rem; color:var(--acc2); line-height:1.1;}
.tile i{font-style:normal; color:var(--mut); font-size:12px; letter-spacing:.06em; text-transform:uppercase;}
h2{font-size:1.05rem; margin:30px 0 8px; color:var(--acc); letter-spacing:.03em;
  border-bottom:1px solid var(--bd); padding-bottom:6px; font-weight:650;}
h3{font-size:.98rem; margin:18px 0 6px; color:var(--tx); font-weight:650;}
p{margin:0 0 10px;}
ul,ol{margin:0 0 12px; padding-left:1.3em;}
li{margin:4px 0;}
li::marker{color:var(--acc);}
code{font-family:ui-monospace,Menlo,monospace; font-size:.86em; background:var(--inset);
  border:1px solid var(--bd); padding:1px 6px; border-radius:4px;}
pre{background:var(--inset); border:1px solid var(--bd); border-radius:8px;
  padding:12px 14px; overflow-x:auto; font-size:12.5px; line-height:1.45;}
pre code{background:none; border:0; padding:0;}
table{border-collapse:collapse; width:100%; font-size:14px; margin:8px 0 16px;
  background:var(--card); border:1px solid var(--bd); border-radius:8px; overflow:hidden;}
th{font-family:ui-monospace,Menlo,monospace; font-size:10.5px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--mut); text-align:left; padding:8px 12px; background:var(--inset);}
td{padding:9px 12px; border-top:1px solid var(--bd); vertical-align:top;}
blockquote{margin:14px 0; padding:12px 16px; border-left:3px solid var(--acc);
  background:var(--card); border-radius:0 8px 8px 0; color:var(--tx);}
blockquote p{margin:0}
hr{border:0; border-top:1px solid var(--bd); margin:24px 0;}
strong{font-weight:650; color:var(--acc2);}
em{color:var(--mut); font-style:italic}
.foot{margin-top:40px; color:var(--dim); font-size:12px; border-top:1px solid var(--bd); padding-top:12px;}
.foot b{color:var(--mut); font-weight:600}
"""


def _split_masthead(md: str) -> tuple[str, str, list[tuple[str, str]], str]:
    """The masthead out of Zoey's markdown: the first `# ` line is the title;
    before the first `##`, `lede:` (or the first plain paragraph) is the lede
    and `kpi: label = value; …` lines become tiles. Returns
    (title, lede, tiles, rest-of-markdown)."""
    title = ""
    lede = ""
    tiles: list[tuple[str, str]] = []
    rest: list[str] = []
    seen_section = False
    for ln in md.splitlines():
        s = ln.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if s.startswith("## "):
            seen_section = True
        if not seen_section:
            if s.lower().startswith("lede:"):
                lede = s[5:].strip()
                continue
            if s.lower().startswith("kpi:"):
                for part in s[4:].split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        tiles.append((k.strip(), v.strip()))
                continue
            if not lede and s and not s.startswith(("#", ">", "|", "-", "*", "`")):
                lede = s
                continue
        rest.append(ln)
    return title, lede, tiles, "\n".join(rest)


def _callouts(body_html: str) -> str:
    """`> ` quote lines are callouts. md_to_body knows no blockquote, so they
    arrive as <p>&gt; …</p>; fold each run into one blockquote."""
    out: list[str] = []
    run: list[str] = []
    for ln in body_html.split("\n"):
        if ln.startswith("<p>&gt; ") and ln.endswith("</p>"):
            run.append(ln[len("<p>&gt; "):-len("</p>")])
            continue
        if run:
            out.append("<blockquote><p>" + "<br/>".join(run) + "</p></blockquote>")
            run = []
        out.append(ln)
    if run:
        out.append("<blockquote><p>" + "<br/>".join(run) + "</p></blockquote>")
    return "\n".join(out)


def render_markdown(md: str, *, title: str = "", kind: str = "report",
                    author: str = "Crissa", day: str = "", sources: str = "") -> str:
    """Zoey's HTML from her markdown (ZOEY_CSS). Importable by the team-zoey
    plugin (show_me's report landing) and the CLI (--kind zoey)."""
    t, lede, tiles, rest = _split_masthead(md)
    title = title or t or "Report"
    day = day or today().isoformat()
    src_line = ""
    kept: list[str] = []
    for ln in rest.splitlines():
        if ln.strip().lower().startswith("sources:") and not src_line:
            src_line = ln.strip()[8:].strip()
            continue
        kept.append(ln)
    sources = sources or src_line
    body = _callouts(md_to_body("\n".join(kept)))
    tiles_html = ""
    if tiles:
        tiles_html = '<div class="tiles">' + "".join(
            f'<div class="tile"><b>{htmlmod.escape(v)}</b><i>{htmlmod.escape(k)}</i></div>' for k, v in tiles
        ) + "</div>"
    foot = f"<b>{htmlmod.escape(author)}</b> · {htmlmod.escape(day)} · {htmlmod.escape(kind)}"
    if sources:
        foot += " · sources: " + _inline(sources)
    foot += " · the markdown sits beside this file"
    lede_html = f'<p class="lede">{_inline(lede)}</p>' if lede else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{htmlmod.escape(title)}</title>
<style>{ZOEY_CSS}</style>
</head>
<body>
<main>
<header class="mast">
<p class="eyebrow">{htmlmod.escape(author)} <span>· {htmlmod.escape(kind)} · {htmlmod.escape(day)}</span></p>
<h1>{htmlmod.escape(title)}</h1>
{lede_html}
{tiles_html}
</header>
{body}
<p class="foot">{foot}</p>
</main>
</body>
</html>
"""


def wrap(title: str, body: str, kind: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{htmlmod.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<p class="eyebrow">krew · {htmlmod.escape(kind)} · copy/print</p>
<div class="card">
{body}
</div>
<p class="foot">Source markdown sits beside this file. Renderer: <code>scripts/krew-report-html.py</code>. Law: <code>docs/STEWARD-ARCHITECT.md</code>.</p>
</main>
</body>
</html>
"""


def render_file(src: Path, *, kind: str = "", title: str = "", author: str = "") -> Path | None:
    if not src.is_file():
        return None
    md = src.read_text(encoding="utf-8")
    dest = src.with_suffix(".html")
    if kind == "zoey":
        html = render_markdown(md, title=title, author=author or "Crissa")
    else:
        title = title or src.stem.replace("-", " ")
        kind = kind or src.stem.split("-")[0]
        html = wrap(title, md_to_body(md), kind)
    _write(dest, html)
    print(dest)
    return dest


def _write(dest: Path, text: str) -> None:
    ""


    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", default="", help="logs|research|world|stack|sources|ops")
    ap.add_argument("--day", default="", help="YYYY-MM-DD (default today)")
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--kind", default="", help="zoey = her report template; else the graphite krew look")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    args = ap.parse_args()
    day = args.day or today().isoformat()
    written = 0
    if args.file:
        if render_file(args.file, kind=args.kind, title=args.title, author=args.author):
            written += 1
    elif args.job:
        names = JOB_FILES.get(args.job, ())
        for tmpl in names:
            p = INBOX / tmpl.format(day=day)
            if render_file(p):
                written += 1
        if args.job == "logs":
            living = INBOX / "ms.html"
            dated = INBOX / f"ms-{day}.html"
            if dated.is_file():
                living.write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")
                print(living)
        if args.job == "research":
            living = INBOX / "team-research.html"
            dated = INBOX / f"team-research-{day}.html"
            if dated.is_file():
                living.write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")
                print(living)
    else:
        ap.print_help()
        return 2
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
