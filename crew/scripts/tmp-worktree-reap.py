#!/usr/bin/env python3
"""The box owns /tmp checkouts: report, then (when armed) reap krew and hermes-agent worktrees left in /tmp (the User 2026-10-01).

/tmp is tmpfs - every file a checkout writes is RAM. 05:17 10-01 a worker's two comparison checkouts of krew
into /tmp hit its 4 GB scope cap, and the first one (3.0 GB) outlived the worker. This walks a repo's registered
worktrees and names each one under the tmp root that is
  * older than --max-age-h (2 h; age = newest of the worktree dir and its index/HEAD in .git/worktrees/<name>),
  * used by no live process (no /proc/<pid>/cwd or open fd under it),
  * clean (no uncommitted change) and holding no commit that only it can see (HEAD on a branch or tag),
and REPORTS it with its size. Armed (KREW_TMP_WORKTREE_REAP=1) it runs `git worktree remove --force` + `prune`
on each candidate and checks the tmp root's use before and after. Unarmed it reports what it would free.

  tmp-worktree-reap.py [--repo PATH ...] [--tmp-root /tmp] [--max-age-h 2] [--log FILE]

One JSON line per run to ~/.hermes/logs/tmp-worktree-reap.jsonl. Off: ~/.hermes/tmp-worktree-reap.off.
Exit 0 always (a report is not a failure).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HOME = Path.home()
# krew, plus both hermes-agent trees (the User 2026-10-01: the week's replay had crew comparison checkouts of
# the hermes clone in /tmp too). The live tree is only READ here (worktree list / status); a reap there would be
# `git worktree remove` of a /tmp checkout, never a move of the live tree itself.
DEFAULT_REPOS = [Path(os.path.expanduser(p)) for p in
                 (os.environ.get("KREW_WORKTREE_REPOS") or "~/krew:~/projects/hermes-agent:~/.hermes/hermes-agent").split(":") if p.strip()]
LOG = KH.hermes_home() / "logs" / "tmp-worktree-reap.jsonl"
OFF = KH.hermes_home() / "tmp-worktree-reap.off"


def armed() -> bool:
    return os.environ.get("KREW_TMP_WORKTREE_REAP", "0").strip().lower() in ("1", "true", "yes", "on")


def git(repo: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def worktrees(repo: Path) -> list[dict]:
    """`git worktree list --porcelain` as dicts (path, head, detached/branch, prunable)."""
    r = git(repo, "worktree", "list", "--porcelain")
    out, cur = [], {}
    for line in (r.stdout or "").splitlines() + [""]:
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v if v else True
    return out


def used_by_a_process(path: Path) -> list[int]:
    root = str(path.resolve()) if path.exists() else str(path)
    pids = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            links = [os.readlink(p / "cwd")]
        except OSError:
            links = []
        try:
            links += [os.readlink(f) for f in (p / "fd").iterdir()]
        except OSError:
            pass
        if any(l == root or l.startswith(root + "/") for l in links):
            pids.append(int(p.name))
    return pids


def admin_dir(repo: Path, path: Path) -> Path | None:
    gitfile = path / ".git"
    try:
        txt = gitfile.read_text().strip()
    except OSError:
        return None
    if txt.startswith("gitdir:"):
        return Path(txt.split(":", 1)[1].strip())
    return None


def age_s(path: Path, adm: Path | None, now: float) -> float:
    stamps = []
    for f in [path] + ([adm / "index", adm / "HEAD", adm] if adm else []):
        try:
            stamps.append(f.stat().st_mtime)
        except OSError:
            pass
    return now - max(stamps) if stamps else 0.0


def size_bytes(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
            except OSError:
                pass
    return total


def tmp_used(root: Path) -> int:
    try:
        return shutil.disk_usage(root).used
    except OSError:
        return -1


def holds(repo: Path, path: Path) -> str:
    """'' when the worktree may go; else why it stays."""
    st = git(path, "status", "--porcelain")
    if st.returncode != 0:
        return "git status failed"
    if st.stdout.strip():
        return "uncommitted changes"
    head = git(path, "rev-parse", "HEAD").stdout.strip()
    if not head:
        return "no HEAD"
    on = git(repo, "for-each-ref", "--contains", head, "--format=%(refname)", "refs/heads", "refs/tags",
             "refs/remotes").stdout.strip()
    if not on:
        return "HEAD is on no branch or tag"
    return ""


def scan(repos: list[Path], tmp_root: Path, max_age_h: float, now: float | None = None) -> list[dict]:
    now = now or time.time()
    root = str(tmp_root.resolve())
    rows = []
    for repo in repos:
        for wt in worktrees(repo):
            p = Path(str(wt.get("worktree") or ""))
            if not str(p).startswith(root + "/"):
                continue
            row = {"repo": str(repo), "path": str(p), "head": str(wt.get("HEAD") or "")[:12]}
            if not p.exists():
                row.update(verdict="prunable", why="folder gone (git worktree prune clears the record)")
                rows.append(row)
                continue
            adm = admin_dir(repo, p)
            a = age_s(p, adm, now)
            row.update(age_h=round(a / 3600, 2), bytes=size_bytes(p))
            pids = used_by_a_process(p)
            if a < max_age_h * 3600:
                row.update(verdict="keep", why=f"younger than {max_age_h:g} h")
            elif pids:
                row.update(verdict="keep", why=f"in use by pid {','.join(map(str, pids[:5]))}")
            elif (h := holds(repo, p)):
                row.update(verdict="keep", why=h)
            else:
                row.update(verdict="reap", why=f"older than {max_age_h:g} h, no live process, clean")
            rows.append(row)
    return rows


def reap(rows: list[dict]) -> None:
    for row in rows:
        if row["verdict"] != "reap":
            continue
        repo = Path(row["repo"])
        r = git(repo, "worktree", "remove", "--force", row["path"], timeout=300)
        row["removed"] = r.returncode == 0 and not Path(row["path"]).exists()
        if not row["removed"]:
            row["error"] = (r.stderr or "").strip()[-200:]
    for repo in {Path(r["repo"]) for r in rows}:
        git(repo, "worktree", "prune")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", action="append", type=Path)
    ap.add_argument("--tmp-root", type=Path, default=Path("/tmp"))
    ap.add_argument("--max-age-h", type=float, default=2.0)
    ap.add_argument("--log", type=Path, default=LOG)
    a = ap.parse_args(argv)
    if OFF.exists():
        print(f"tmp-worktree-reap: off ({OFF})")
        return 0
    mode = "reap" if armed() else "report"
    before = tmp_used(a.tmp_root)
    rows = scan([r for r in (a.repo or DEFAULT_REPOS) if (r / ".git").exists()], a.tmp_root, a.max_age_h)
    if mode == "reap":
        reap(rows)
    after = tmp_used(a.tmp_root)
    cand = [r for r in rows if r["verdict"] == "reap"]
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode, "tmp_used_before": before,
           "tmp_used_after": after, "would_free" if mode == "report" else "freed":
           sum(r.get("bytes", 0) for r in cand) if mode == "report" else max(0, before - after),
           "candidates": len(cand), "rows": rows}
    try:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        with a.log.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    gib = 1024 ** 3
    print(f"tmp-worktree-reap {mode}: {len(rows)} /tmp worktree(s), {len(cand)} candidate(s); "
          f"/tmp used {before / gib:.2f} -> {after / gib:.2f} GiB"
          + (f"; would free {rec['would_free'] / gib:.2f} GiB" if mode == "report" else ""))
    for r in rows:
        print(f"  {r['verdict']:8s} {r['path']}  {r.get('age_h', '-')} h  {r.get('bytes', 0) / gib:.2f} GiB  {r['why']}"
              + ("  REMOVED" if r.get("removed") else (f"  FAILED {r['error']}" if r.get("error") else "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
