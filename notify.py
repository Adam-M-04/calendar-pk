#!/usr/bin/env python3
"""
Powiadomienia e-mail o zmianach w planie ogłoszonych tuż przed zajęciami.

Subskrybowany kalendarz aktualizuje się sam, ale ani Apple Calendar, ani Google
nie mówią, że coś się w nim zmieniło. Zmiana z dużym wyprzedzeniem nie jest
problemem, ale przeniesienie sobotnich zajęć ogłoszone w piątek łatwo
przegapić.

Gdy plan 4 roku się zmienił, workflow porównuje nowy options.json z poprzednim
i dla każdego obserwowanego linku szuka zmian w zajęciach, których stary albo
nowy termin wypada w ciągu najbliższych NOTIFY_DAYS dni. Dalsze zmiany trafiają
tylko do kalendarza.

Obserwowane linki są w sekrecie NOTIFY_WATCH, po jednym w linii:
    <link do kalendarza albo sam skrót> [adres e-mail …]
Bez adresu wiadomość idzie na SMTP_USER.

Użycie:
    python3 notify.py diff --old stary/options.json --new public/options.json --preview
    python3 notify.py send      # wiadomości z $MESSAGES, konto z $SMTP_USER / $SMTP_PASSWORD
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from generate import PLAN_URL, SITE_URL, WARSAW, fetch_text, selection_hash

NOTIFY_DAYS = 4          # od środy obejmuje cały najbliższy weekend

WEEKDAYS = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
WEEKDAYS_SHORT = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
COMPARED = ("date", "start", "end", "room", "teacher", "notices", "subject", "activity", "group")


# --- dane -------------------------------------------------------------------

def watch_list() -> list[tuple[str, list[str]]]:
    """NOTIFY_WATCH: „<link albo skrót> [adres …]” w każdej linii, # zaczyna komentarz."""
    out = []
    for line in os.environ.get("NOTIFY_WATCH", "").splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            out.append((Path(fields[0].rstrip("/")).stem, fields[1:]))
    return out


def load_options(path: Path | None, url: str | None) -> dict | None:
    if path and path.exists():
        print(f"Poprzedni stan planu: {path}", file=sys.stderr)
        return json.loads(path.read_text())
    if url:
        print(f"Poprzedni stan planu: {url} (brak zapisanego stanu)", file=sys.stderr)
        raw = fetch_text(url)
        return json.loads(raw) if raw else None
    return None


def find_selection(options: dict, digest: str) -> tuple[int, dict[str, str]] | None:
    """Odwraca skrót z linku na wybór grup — tak samo jak all_selections() w generate.py."""
    electives = options["electives"]
    choices = [[None, *e["options"]] for e in electives]
    for gl in range(1, options["glCount"] + 1):
        for combo in itertools.product(*choices):
            picks = {e["key"]: c for e, c in zip(electives, combo) if c}
            if selection_hash(options["semester"], gl, picks) == digest:
                return gl, picks
    return None


def attends(s: dict, gl: int, picks: dict[str, str]) -> bool:
    if "elective" in s:
        return picks.get(s["elective"]) in s["labels"]
    return gl in s["gl"]


def comparable(old: dict | None, new: dict) -> str | None:
    """Powód, dla którego nie da się porównać wersji, albo None."""
    if old is None:
        return "brak poprzedniej wersji planu"
    if (old.get("term"), old.get("semester")) != (new["term"], new["semester"]):
        return "nowy semestr"
    if any("id" not in s for s in old["sessions"]):
        return "poprzednia wersja planu nie ma identyfikatorów zajęć"
    return None


# --- zmiany -----------------------------------------------------------------

def day_long(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def day_short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}"


def title(s: dict) -> str:
    return f"{s['subject']} — {s['activity']}" + (f" ({s['group']})" if s.get("group") else "")


def when(s: dict) -> str:
    return f"{day_long(s['date'])}, {s['start']}–{s['end']}, {s['room']}"


def describe_change(a: dict, b: dict) -> list[str]:
    lines = [f"• {title(b)}"]
    if title(a) != title(b):
        lines.append(f"  wcześniej: {title(a)}")
    if when(a) != when(b):
        lines += [f"  było: {when(a)}", f"  jest: {when(b)}"]
    else:
        lines.append(f"  {when(b)}")
    if a.get("teacher") != b.get("teacher"):
        lines.append(f"  prowadzący: {a.get('teacher') or '—'} → {b.get('teacher') or '—'}")
    old_notes, new_notes = a.get("notices", []), b.get("notices", [])
    lines += [f"  nowa uwaga: {n}" for n in new_notes if n not in old_notes]
    lines += [f"  usunięta uwaga: {n}" for n in old_notes if n not in new_notes]
    return lines


def plan_changes(old: dict, new: dict, gl: int, picks: dict[str, str],
                 first: date, last: date) -> list[tuple[str, list[str]]]:
    """Zmiany w zajęciach z terminem (starym albo nowym) od first do last.

    Zwraca (data do tematu wiadomości, linie opisu), w kolejności terminów.
    """
    before = {s["id"]: s for s in old["sessions"] if attends(s, gl, picks)}
    after = {s["id"]: s for s in new["sessions"] if attends(s, gl, picks)}
    everywhere = {s["id"]: s for s in new["sessions"]}

    changed = [(before[i], after[i]) for i in before.keys() & after.keys()
               if any(before[i].get(f) != after[i].get(f) for f in COMPARED)]
    removed = [s for i, s in before.items() if i not in after]
    added = [s for i, s in after.items() if i not in before]

    # Planer bywa, że usuwa blok i tworzy nowy zamiast go przesunąć. Parę z tym
    # samym przedmiotem, formą i grupą pokazujemy jako zmianę terminu.
    def kind(s: dict) -> tuple:
        return s["subject"], s["activity"], s.get("group")

    for a in sorted(removed, key=lambda s: (s["date"], s["start"])):
        same = [b for b in added if kind(b) == kind(a)]
        if same:
            b = min(same, key=lambda b: abs(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])))
            removed.remove(a)
            added.remove(b)
            changed.append((a, b))

    def near(s: dict) -> bool:
        return first <= date.fromisoformat(s["date"]) <= last

    entries: list[tuple[tuple, str, list[str]]] = []
    for a, b in changed:
        hits = [s for s in (a, b) if near(s)]
        if hits:
            s = min(hits, key=lambda s: (s["date"], s["start"]))
            entries.append(((s["date"], s["start"]), s["date"], describe_change(a, b)))
    for s in removed:
        if near(s):
            moved = everywhere.get(s["id"])
            what = (f"przeniesione do innej grupy ({moved['group'] or 'bez grupy'})" if moved
                    else "usunięte z planu")
            entries.append(((s["date"], s["start"]), s["date"], [f"• {title(s)}", f"  {what}: {when(s)}"]))
    for s in added:
        if near(s):
            entries.append(((s["date"], s["start"]), s["date"], [f"• {title(s)}", f"  nowe: {when(s)}"]))

    entries.sort(key=lambda e: e[0])
    return [(day, lines) for _, day, lines in entries]


# --- wiadomości -------------------------------------------------------------

def footer(new: dict) -> list[str]:
    return [
        "",
        f"Plan online: {PLAN_URL}",
        f"Stan planu: {new['savedAt']}",
        "",
        "—",
        f"calendar-pk, {SITE_URL}",
        f"Powiadomienie przychodzi, gdy zmienią się zajęcia w ciągu {NOTIFY_DAYS} dni od wykrycia zmiany.",
    ]


def change_message(changes: list[tuple[str, list[str]]], new: dict) -> dict:
    days = sorted({day for day, _ in changes})
    one = len(changes) == 1
    subject = f"Plan PK: {'zmiana' if one else 'zmiany'} na {', '.join(day_short(d) for d in days)}"
    body = ["Zmiana w Twoim planie na najbliższe dni:" if one
            else "Zmiany w Twoim planie na najbliższe dni:"]
    for _, lines in changes:
        body += [""] + lines
    body += ["", "Kalendarz zaktualizuje się sam przy najbliższym odświeżeniu."] + footer(new)
    return {"subject": subject, "body": "\n".join(body)}


def test_message(new: dict, gl: int, picks: dict[str, str], today: date) -> dict:
    upcoming = sorted((s for s in new["sessions"]
                       if attends(s, gl, picks) and s["date"] >= today.isoformat()),
                      key=lambda s: (s["date"], s["start"]))
    body = [
        "To jest wiadomość testowa — wysyłka powiadomień działa.",
        "",
        f"Prawdziwe powiadomienie przyjdzie, gdy w ciągu najbliższych {NOTIFY_DAYS} dni",
        "zmienią się Twoje zajęcia: termin, sala, prowadzący albo uwagi w planie.",
    ]
    if upcoming:
        first = upcoming[0]["date"]
        body += ["", f"Najbliższe zajęcia, {day_long(first)}:"]
        for s in upcoming:
            if s["date"] == first:
                body += ["", f"• {title(s)}", f"  {s['start']}–{s['end']}, {s['room']}"]
    return {"subject": "Plan PK: test powiadomień", "body": "\n".join(body + footer(new))}


# --- polecenia --------------------------------------------------------------

def cmd_diff(args: argparse.Namespace) -> None:
    watch = watch_list()
    if not watch:
        print("NOTIFY_WATCH jest pusty — powiadomienia wyłączone.", file=sys.stderr)
        return
    new = json.loads(args.new.read_text())
    old = load_options(args.old, args.old_url)
    reason = comparable(old, new)
    if reason:
        print(f"Nie porównuję wersji planu: {reason}.", file=sys.stderr)
    today = args.today or datetime.now(WARSAW).date()
    last = today + timedelta(days=NOTIFY_DAYS)

    messages = []
    for index, (digest, _) in enumerate(watch, start=1):
        # W logach publicznego repo pokazujemy tylko numer linku, nie skrót.
        selection = find_selection(new, digest)
        if selection is None:
            print(f"::warning::Obserwowanego linku nr {index} nie ma w bieżącym planie "
                  "(literówka w NOTIFY_WATCH albo nowy semestr).")
            continue
        gl, picks = selection
        if args.test:
            messages.append({"watch": index, **test_message(new, gl, picks, today)})
        if reason:
            continue
        changes = plan_changes(old, new, gl, picks, today, last)
        print(f"Link nr {index}: zmian w zajęciach do {last:%d.%m}: {len(changes)}", file=sys.stderr)
        if changes:
            messages.append({"watch": index, **change_message(changes, new)})

    if args.preview:
        for m in messages:
            print(f"=== link nr {m['watch']}\nTemat: {m['subject']}\n\n{m['body']}\n")
    output = os.environ.get("GITHUB_OUTPUT")
    if output and messages:
        with open(output, "a") as f:
            f.write(f"messages={json.dumps(messages)}\n")


def cmd_send(_: argparse.Namespace) -> None:
    messages = json.loads(os.environ.get("MESSAGES") or "[]")
    if not messages:
        print("Nie ma czego wysłać.")
        return
    user = os.environ.get("SMTP_USER")
    password = (os.environ.get("SMTP_PASSWORD") or "").replace(" ", "")  # Google pokazuje je w grupach po 4
    if not user or not password:
        raise SystemExit("Brak sekretów SMTP_USER / SMTP_PASSWORD — nie mam jak wysłać powiadomień.")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    watch = watch_list()

    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60) as smtp:
        smtp.login(user, password)
        for m in messages:
            msg = EmailMessage()
            msg["Subject"] = m["subject"]
            msg["From"] = formataddr(("Plan PK", user))
            msg["To"] = ", ".join(watch[m["watch"] - 1][1] or [user])
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain="calendar-pk")
            msg.set_content(m["body"])
            smtp.send_message(msg)
            print(f"Wysłano powiadomienie dla linku nr {m['watch']}: {m['subject']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(required=True)

    diff = sub.add_parser("diff", help="porównaj wersje planu i przygotuj wiadomości")
    diff.add_argument("--new", type=Path, required=True, help="nowy options.json")
    diff.add_argument("--old", type=Path, help="poprzedni options.json (zapisany stan)")
    diff.add_argument("--old-url", help="opublikowany options.json, gdy nie ma zapisanego stanu")
    diff.add_argument("--today", type=date.fromisoformat, help="data „dziś” (RRRR-MM-DD), do testów")
    diff.add_argument("--test", action="store_true", help="dołóż wiadomość testową dla każdego linku")
    diff.add_argument("--preview", action="store_true", help="wypisz wiadomości na ekran")
    diff.set_defaults(func=cmd_diff)

    send = sub.add_parser("send", help="wyślij wiadomości przygotowane przez diff")
    send.set_defaults(func=cmd_send)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
