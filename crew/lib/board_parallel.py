"""krew: board_parallel.py (lifted from the reference install; see crew/README.md)."""








from __future__ import annotations

# Mirrors team_board's own resolution (team-crissa _BOARD_READ_WORDS): a read word WITH a card id is `show`.
READ_WORDS = frozenset({"card", "thread", "card_thread", "read", "read card", "detail", "details", "view", "get",
                        "get_card", "open", "body", "comments", "history", "info", "inspect", "look"})


def team_board_read_only(args: dict) -> bool:
    action = str((args or {}).get("action") or "list").strip().lower()
    tid = str(args.get("id") or args.get("task") or args.get("card") or args.get("card_id") or "").strip()
    if action == "show":
        return bool(tid)
    return action in READ_WORDS and bool(tid)
