#!/usr/bin/env python3
"""
Generator kalendarzy .ics z planem zajęć 4 roku Informatyki (studia
niestacjonarne, I stopień) na Politechnice Krakowskiej.

Źródło danych: publiczny JSON stojący za planem online Katedry Informatyki
(FK Planer), https://ii.pk.edu.pl/~fkruzel/fk-planer-lti/public/plan-ns/
Ten sam plan trafia do xlsx na stronie wydziału, ale xlsx jest eksportem
robionym ręcznie i bywa nieaktualny — JSON jest źródłem nadrzędnym.

GitHub Pages serwuje tylko statyczne pliki, więc nie da się filtrować planu
parametrami w adresie. Zamiast tego generujemy osobny plik dla każdej możliwej
kombinacji wyborów (grupa laboratoryjna × podgrupa każdego przedmiotu
wybieralnego albo „nie chodzę”). Nazwą pliku jest skrót SHA-256 z opisu
wyboru — formularz liczy dokładnie ten sam skrót w przeglądarce.

Użycie:
    python3 generate.py --output public
    python3 generate.py --snapshot snapshot.json --output public   # bez sieci
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc

SNAPSHOT_URL = "https://ii.pk.edu.pl/~fkruzel/fk-planer-lti/public/plan-ns/snapshot.php"
PLAN_URL = "https://ii.pk.edu.pl/~fkruzel/fk-planer-lti/public/plan-ns/"
SITE_URL = "https://adam-m-04.github.io/calendar-pk/"
USER_AGENT = "calendar-pk/1.0 (+https://github.com/Adam-M-04/calendar-pk)"

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"
LEGACY_DIR = ROOT / "legacy"

CALNAME = "Plan zajęć PK — rok 4"
HASH_VERSION = "v1"      # zmiana formatu opisu wyboru = nowe linki dla wszystkich
HASH_LENGTH = 12

# Rok 4 studiów I stopnia to semestry 7 (zima) i 8 (lato). JSON zawsze niesie
# tylko bieżący semestr, więc bierzemy ten, który w nim jest.
YEAR = 4
YEAR_SEMESTERS = (7, 8)
COHORT_RE = re.compile(
    r"^I stopień niestac sem\. (?P<sem>\d+)(?P<block>W\d+)?(?: / gr\. (?P<group>\d+))?$"
)

ACTIVITY = {
    "W": "wykład",
    "L": "laboratorium",
    "P": "projekt",
    "C": "ćwiczenia",
    "S": "seminarium",
}

# Plan skraca długie nazwy, żeby zmieściły się w komórkach xlsx.
SUBJECT_FULL = {
    "Zaawansowane technologie proj.ap.int":
        "Zaawansowane technologie projektowania aplikacji internetowych",
}

CAMPUS_ADDRESS = {
    "Czyżyny": "Kampus Czyżyny, al. Jana Pawła II 37, Kraków",
    "Warszawska": "Kampus Warszawska, ul. Warszawska 24, Kraków",
}


# --- pobieranie -------------------------------------------------------------

def fetch_json(url: str, attempts: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            print(f"  próba {attempt}/{attempts} nieudana: {exc}", file=sys.stderr)
            time.sleep(5 * attempt)
    raise SystemExit(f"Nie udało się pobrać {url}: {last}")


def fetch_text(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, TimeoutError):
        return None


# --- model ------------------------------------------------------------------

@dataclass
class Session:
    """Jedno spotkanie z planu (blok w JSON-ie)."""
    block_id: str
    uid: str
    start: datetime          # UTC
    end: datetime            # UTC
    subject: str             # nazwa z planu (klucz)
    activity: str            # W / L / P / C / S
    teacher: str
    room: str
    campus: str
    hours: int
    zjazd: int
    group_text: str
    notices: list[str]
    # Kto ma te zajęcia: albo zbiór grup laboratoryjnych (przedmioty
    # obowiązkowe), albo przedmiot wybieralny + podgrupy, dla których jest.
    gl: frozenset[int] | None = None
    elective: str | None = None
    labels: frozenset[str] = frozenset()

    def attends(self, gl: int, picks: dict[str, str]) -> bool:
        if self.elective is None:
            return gl in self.gl
        return picks.get(self.elective) in self.labels


@dataclass
class Elective:
    key: str
    name: str
    block: str               # "W1", "W2" albo "" dla przedmiotu z kohorty głównej
    options: list[str]
    teachers: list[str] = field(default_factory=list)


@dataclass
class Model:
    term: str
    semester: int
    saved_at: datetime
    gl_count: int
    c_groups: dict[int, list[int]]    # grupa ćwiczeniowa → jej grupy laboratoryjne
    electives: list[Elective]
    sessions: list[Session]
    fingerprint_input: dict


def slug(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    ascii_ = ascii_.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")


def teacher_name(block: dict) -> str:
    """Plan zapisuje „Nazwisko Imię”; w kalendarzu naturalniej jest odwrotnie."""
    raw = block.get("teacherDisplay") or block.get("teacher") or ""
    names = []
    for part in raw.split(" / "):
        tokens = part.split()
        names.append(f"{tokens[1]} {tokens[0]}" if len(tokens) == 2 else part)
    return ", ".join(n for n in names if n)


def notices_for(block: dict, notices: list[dict]) -> list[str]:
    """Komunikaty planu („Początek: 6 listopada” itp.) dopasowane do bloku."""
    out = []
    for notice in notices:
        m = notice.get("match") or {}
        if "planType" in m and m["planType"] != block["planType"]:
            continue
        if "cohort" in m and m["cohort"] != block["cohort"]:
            continue
        if "subjects" in m and block["subject"] not in m["subjects"]:
            continue
        if "activity" in m and m["activity"] != block["activity"]:
            continue
        if "activities" in m and block["activity"] not in m["activities"]:
            continue
        if "teachers" in m and block["teacher"] not in m["teachers"]:
            continue
        by_act = m.get("groupsByActivity") or {}
        if block["activity"] in by_act and block.get("groupNo") not in by_act[block["activity"]]:
            continue
        text = notice.get("text") or notice.get("shortText")
        if text:
            out.append(text)
    return out


def weekend_of(day: date) -> date:
    return day - timedelta(days=day.weekday() - 5) if day.weekday() >= 5 else day


def build_model(payload: dict) -> Model:
    state = payload["state"]
    blocks = state["blocks"]
    tn = payload.get("timetableNotices") or {}
    notices = (tn.get("publicationNotes") or []) + (tn.get("notices") or [])

    # Numeracja zjazdów jak w xlsx: kolejne weekendy z jakimikolwiek zajęciami NS.
    weekends = sorted({weekend_of(date.fromisoformat(b["date"]))
                       for b in blocks if b.get("planType") == "niestacjonarne" and b.get("date")})
    zjazd_no = {w: i for i, w in enumerate(weekends, start=1)}

    parsed = []
    for b in blocks:
        m = COHORT_RE.match(b.get("cohort", ""))
        if m and int(m["sem"]) in YEAR_SEMESTERS and b.get("date"):
            parsed.append((b, m))
    if not parsed:
        raise SystemExit("W planie nie ma zajęć 4 roku — nie publikuję pustych kalendarzy.")
    semester = Counter(int(m["sem"]) for _, m in parsed).most_common(1)[0][0]
    parsed = [(b, m) for b, m in parsed if int(m["sem"]) == semester]

    def labels_of(b: dict) -> list[str] | None:
        eg = b.get("nsExportGroups")
        return list(eg["labels"]) if eg and eg.get("labels") else None

    # Przedmioty wybieralne: każdy blok z własnymi podgrupami zapisów (K01, P02,
    # Ć01…) albo z kohorty bloku wybieralnego (sem. 7W1, 7W2…).
    elective_labels: dict[str, set[str]] = defaultdict(set)
    elective_block: dict[str, str] = {}
    base_groups: dict[str, set[int]] = defaultdict(set)
    for b, m in parsed:
        labels = labels_of(b)
        if labels or m["block"]:
            if labels:
                elective_labels[b["subject"]].update(labels)
            elif m["group"]:
                elective_labels[b["subject"]].add(f"gr. {m['group']}")
            elective_block.setdefault(b["subject"], m["block"] or "")
        elif m["group"]:
            base_groups[b["subject"]].add(int(m["group"]))
    for subject in elective_block:
        if not elective_labels[subject]:
            elective_labels[subject].add("tak")

    # Grupy laboratoryjne (GL1…GLn) to najdrobniejszy podział kohorty głównej;
    # przedmiot podzielony na mniej grup ma grupy ćwiczeniowe, z których każda
    # obejmuje kolejne GL-e (C1 = GL1+GL2 itd., tak jak kolumny w xlsx).
    gl_count = max((len(g) for g in base_groups.values()), default=1)
    ratio_of: dict[str, int] = {}
    for subject, groups in base_groups.items():
        n = len(groups)
        if gl_count % n:
            raise SystemExit(f"{subject}: {n} grup nie dzieli {gl_count} grup laboratoryjnych.")
        ratio_of[subject] = gl_count // n
    c_ratios = Counter(r for r in ratio_of.values() if r > 1)
    c_groups: dict[int, list[int]] = {}
    if c_ratios:
        r = c_ratios.most_common(1)[0][0]
        c_groups = {c: list(range((c - 1) * r + 1, c * r + 1)) for c in range(1, gl_count // r + 1)}

    rooms = {r["code"]: r for r in state.get("rooms", [])}
    sessions: list[Session] = []
    elective_teachers: dict[str, set[str]] = defaultdict(set)
    for b, m in parsed:
        day = date.fromisoformat(b["date"])
        start_local = datetime(day.year, day.month, day.day, tzinfo=WARSAW) + timedelta(minutes=b["start"])
        start = start_local.astimezone(UTC)
        end = (start_local + timedelta(minutes=b["duration"])).astimezone(UTC)
        subject = b["subject"]
        common = dict(
            block_id=b["id"],
            # UID zależy tylko od identyfikatora bloku, więc przeniesienie zajęć
            # na inny termin aktualizuje wydarzenie zamiast tworzyć nowe.
            uid=hashlib.sha256(b["id"].encode()).hexdigest()[:32] + "@calendar-pk",
            start=start, end=end, subject=subject, activity=b["activity"],
            teacher=teacher_name(b), room=b.get("room") or "", campus=b.get("campus") or "",
            hours=b.get("hours") or 0, zjazd=zjazd_no[weekend_of(day)],
            notices=notices_for(b, notices),
        )
        if subject in elective_block:
            labels = labels_of(b)
            if not labels:
                labels = [f"gr. {m['group']}"] if m["group"] else sorted(elective_labels[subject])
            if len(labels) == 1:
                group_text = "" if labels == ["tak"] else f"grupa {labels[0]}"
            else:
                group_text = "grupy " + ", ".join(sorted(labels))
            elective_teachers[subject].add(common["teacher"])
            sessions.append(Session(**common, group_text=group_text,
                                    elective=slug(subject), labels=frozenset(labels)))
        elif m["group"]:
            g, r = int(m["group"]), ratio_of[subject]
            gls = frozenset(range((g - 1) * r + 1, g * r + 1))
            group_text = f"grupa GL{g}" if r == 1 else f"grupa C{g}"
            sessions.append(Session(**common, group_text=group_text, gl=gls))
        else:
            sessions.append(Session(**common, group_text="cały rok",
                                    gl=frozenset(range(1, gl_count + 1))))
    sessions.sort(key=lambda s: (s.start, s.subject))

    electives = [
        Elective(key=slug(s), name=SUBJECT_FULL.get(s, s), block=elective_block[s],
                 options=sorted(elective_labels[s]), teachers=sorted(elective_teachers[s]))
        for s in elective_block
    ]
    electives.sort(key=lambda e: (e.block, e.name))
    keys = [e.key for e in electives]
    if len(set(keys)) != len(keys):
        raise SystemExit(f"Dwa przedmioty mają ten sam klucz: {keys}")

    used_rooms = sorted({s.room for s in sessions})
    return Model(
        term=(state.get("meta") or {}).get("term") or state["academicCalendar"]["year"],
        semester=semester,
        saved_at=datetime.fromisoformat(state["snapshot"]["savedAt"]),
        gl_count=gl_count,
        c_groups=c_groups,
        electives=electives,
        sessions=sessions,
        fingerprint_input={
            "blocks": [b for b, _ in parsed],
            "rooms": {code: rooms.get(code) for code in used_rooms},
            "notices": notices,
            "weekends": [w.isoformat() for w in weekends],
            "term": (state.get("meta") or {}).get("term"),
        },
    )


# --- wybór i skrót ----------------------------------------------------------

def canonical(semester: int, gl: int, picks: dict[str, str]) -> str:
    """Opis wyboru, z którego liczony jest skrót. Ta sama funkcja jest w app.js."""
    parts = ",".join(f"{k}={picks[k]}" for k in sorted(picks) if picks[k])
    return f"{HASH_VERSION}|sem{semester}|gl{gl}|{parts}"


def selection_hash(semester: int, gl: int, picks: dict[str, str]) -> str:
    return hashlib.sha256(canonical(semester, gl, picks).encode()).hexdigest()[:HASH_LENGTH]


def all_selections(model: Model):
    choices = [[None, *e.options] for e in model.electives]
    for gl in range(1, model.gl_count + 1):
        for combo in itertools.product(*choices):
            picks = {e.key: c for e, c in zip(model.electives, combo) if c}
            yield gl, picks


# --- ICS --------------------------------------------------------------------

def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Łamanie linii co 75 bajtów (RFC 5545), bez rozcinania znaków UTF-8."""
    if len(line.encode()) <= 75:
        return line
    parts, cur, limit = [], b"", 75
    for ch in line:
        enc = ch.encode()
        if len(cur) + len(enc) > limit:
            parts.append(cur.decode())
            cur, limit = enc, 74          # linia kontynuacji zaczyna się od spacji
        else:
            cur += enc
    parts.append(cur.decode())
    return "\r\n ".join(parts)


def room_text(session: Session, rooms: dict) -> tuple[str, str]:
    """(krótko do opisu, pełne miejsce z adresem do pola LOCATION)."""
    code = session.room
    if code == "ONLINE" or session.campus == "Zdalnie":
        return "zdalnie", "Online"
    if code == "TODO" or not code:
        return "do ustalenia", "Sala do ustalenia"
    name = (rooms.get(code) or {}).get("name", "")
    number = re.match(r"^L\d+ - (\d+)", name)
    if number:
        short = f"s. {number[1]} ({code})"
    elif code == "SEMINARYJNA":
        short = "s. seminaryjna"
    else:
        short = f"s. {code}"
    address = CAMPUS_ADDRESS.get(session.campus, f"Kampus {session.campus}" if session.campus else "")
    return f"{short}, {session.campus}" if session.campus else short, \
        f"{short}, {address}" if address else short


def render_event(session: Session, rooms: dict, stamp: str, saved_local: str) -> str:
    activity = ACTIVITY.get(session.activity, session.activity)
    title = f"{SUBJECT_FULL.get(session.subject, session.subject)} — {activity}"
    room_short, location = room_text(session, rooms)
    head = activity.capitalize() + (f" · {session.group_text}" if session.group_text else "")
    length = f" · {session.hours} × 45 min" if session.hours else ""
    lines = [head]
    if session.teacher:
        lines.append(f"Prowadzący: {session.teacher}")
    lines.append(f"Zjazd {session.zjazd}{length}")
    lines += session.notices
    lines += ["", f"Plan online: {PLAN_URL}", f"Stan planu: {saved_local}"]
    out = [
        "BEGIN:VEVENT",
        fold(f"UID:{session.uid}"),
        f"DTSTAMP:{stamp}",
        f"DTSTART:{session.start:%Y%m%dT%H%M%SZ}",
        f"DTEND:{session.end:%Y%m%dT%H%M%SZ}",
        fold(f"SUMMARY:{escape(title)}"),
        fold(f"LOCATION:{escape(location)}"),
        fold(f"DESCRIPTION:{escape(chr(10).join(lines))}"),
        fold(f"CATEGORIES:{escape(activity.capitalize())}"),
        fold(f"URL:{PLAN_URL}"),
        "TRANSP:OPAQUE",
        "END:VEVENT",
    ]
    return "\r\n".join(out)


def calendar(description: str, events: list[str]) -> str:
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//calendar-pk//Plan zajec PK//PL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        fold(f"X-WR-CALNAME:{escape(CALNAME)}"),
        fold(f"X-WR-CALDESC:{escape(description)}"),
        "X-WR-TIMEZONE:Europe/Warsaw",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    return "\r\n".join(head + events + ["END:VCALENDAR"]) + "\r\n"


def selection_description(model: Model, gl: int, picks: dict[str, str]) -> str:
    parts = [f"Informatyka NS, rok {YEAR}, sem. {model.semester} ({model.term})"]
    c = next((c for c, gls in model.c_groups.items() if gl in gls), None)
    parts.append(f"grupa ćw. {c}, lab. {gl}" if c else f"grupa lab. {gl}")
    for e in model.electives:
        if picks.get(e.key):
            parts.append(f"{e.name}{'' if picks[e.key] == 'tak' else ' ' + picks[e.key]}")
    return " · ".join(parts)


def legacy_calendar(old_term: str, model: Model, stamp: str) -> str:
    """Kalendarz dla linku z poprzedniego semestru: jedno wydarzenie z prośbą o nowy wybór."""
    first = min(s.start for s in model.sessions).astimezone(WARSAW).date()
    event = "\r\n".join([
        "BEGIN:VEVENT",
        f"UID:nowy-semestr-{slug(model.term)}@calendar-pk",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{first:%Y%m%d}",
        f"DTEND;VALUE=DATE:{first + timedelta(days=1):%Y%m%d}",
        fold(f"SUMMARY:{escape('Nowy semestr — wybierz przedmioty ponownie')}"),
        fold("DESCRIPTION:" + escape(
            f"Ten link dotyczył semestru {old_term}. Plan na {model.term} jest już "
            f"dostępny — wybierz grupy i przedmioty na {SITE_URL} i dodaj nowy kalendarz. "
            f"Ten możesz potem usunąć.")),
        fold(f"URL:{SITE_URL}"),
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ])
    return calendar(f"Nieaktualny link z semestru {old_term}", [event])


# --- strona -----------------------------------------------------------------

def options_json(model: Model, rooms: dict) -> dict:
    """Dane dla formularza: struktura wyboru + lekka lista zajęć do podglądu i kolizji.

    Z tej samej listy notify.py wylicza zmiany między kolejnymi wersjami planu,
    stąd identyfikator bloku, prowadzący i uwagi, których formularz nie używa.
    """
    sessions = []
    for s in model.sessions:
        start, end = s.start.astimezone(WARSAW), s.end.astimezone(WARSAW)
        item = {
            "id": s.block_id,
            "date": start.date().isoformat(),
            "start": start.strftime("%H:%M"),
            "end": end.strftime("%H:%M"),
            "subject": SUBJECT_FULL.get(s.subject, s.subject),
            "activity": ACTIVITY.get(s.activity, s.activity),
            "room": room_text(s, rooms)[0],
            "group": s.group_text,
            "teacher": s.teacher,
        }
        if s.notices:
            item["notices"] = s.notices
        if s.elective is None:
            item["gl"] = sorted(s.gl)
        else:
            item["elective"] = s.elective
            item["labels"] = sorted(s.labels)
        sessions.append(item)
    return {
        "hashVersion": HASH_VERSION,
        "hashLength": HASH_LENGTH,
        "year": YEAR,
        "semester": model.semester,
        "term": model.term,
        "savedAt": model.saved_at.astimezone(WARSAW).strftime("%d.%m.%Y, %H:%M"),
        "glCount": model.gl_count,
        "cGroups": {str(c): gls for c, gls in model.c_groups.items()},
        "electives": [
            {"key": e.key, "name": e.name, "block": e.block, "options": e.options, "teachers": e.teachers}
            for e in model.electives
        ],
        "sessions": sessions,
    }


def fingerprint(model: Model, legacy: dict[str, list[str]]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(model.fingerprint_input, sort_keys=True, ensure_ascii=False).encode())
    h.update(json.dumps(legacy, sort_keys=True).encode())
    for path in sorted([ROOT / "generate.py", *SITE_DIR.rglob("*")]):
        if path.is_file():
            h.update(path.relative_to(ROOT).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def archive_previous_term(site_url: str, model: Model) -> None:
    """Gdy na Pages wisi jeszcze poprzedni semestr, zapisz jego skróty do legacy/."""
    raw = fetch_text(site_url.rstrip("/") + "/meta.json")
    if not raw:
        print("Brak opublikowanego meta.json — nie ma czego archiwizować.", file=sys.stderr)
        return
    meta = json.loads(raw)
    if meta.get("term") == model.term and meta.get("semester") == model.semester:
        return
    hashes = fetch_text(site_url.rstrip("/") + "/hashes.txt")
    if not hashes:
        print("Opublikowany semestr jest inny, ale brak hashes.txt.", file=sys.stderr)
        return
    target = LEGACY_DIR / f"{slug(meta.get('term', 'stary'))}-sem{meta.get('semester')}.txt"
    if target.exists():
        return
    LEGACY_DIR.mkdir(exist_ok=True)
    target.write_text(f"# {meta.get('term')}, sem. {meta.get('semester')}\n{hashes}")
    print(f"Zarchiwizowano skróty poprzedniego semestru do {target.name}", file=sys.stderr)


def read_legacy() -> dict[str, list[str]]:
    out = {}
    for path in sorted(LEGACY_DIR.glob("*.txt")):
        lines = path.read_text().splitlines()
        term = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else path.stem
        out[term] = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    return out


# --- główna -----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", type=Path, default=Path("public"))
    ap.add_argument("--snapshot", type=Path, help="lokalny plik JSON zamiast pobierania")
    ap.add_argument("--archive-from", metavar="URL",
                    help="adres opublikowanej strony; przy zmianie semestru zapisze stare skróty do legacy/")
    ap.add_argument("--min-sessions", type=int, default=30,
                    help="poniżej tylu zajęć 4 roku build pada i nic nie publikuje")
    args = ap.parse_args()

    if args.snapshot:
        payload = json.loads(args.snapshot.read_text())
    else:
        print(f"Pobieram {SNAPSHOT_URL}", file=sys.stderr)
        payload = fetch_json(SNAPSHOT_URL)

    model = build_model(payload)
    if len(model.sessions) < args.min_sessions:
        raise SystemExit(f"Tylko {len(model.sessions)} zajęć 4 roku (< {args.min_sessions}) — "
                         "nie publikuję, żeby nie wyczyścić ludziom kalendarzy.")
    if args.archive_from:
        archive_previous_term(args.archive_from, model)
    legacy = read_legacy()

    rooms = {r["code"]: r for r in payload["state"].get("rooms", [])}
    stamp = model.saved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    saved_local = model.saved_at.astimezone(WARSAW).strftime("%d.%m.%Y, %H:%M")
    rendered = [render_event(s, rooms, stamp, saved_local) for s in model.sessions]

    out = args.output
    if out.exists():
        shutil.rmtree(out)
    (out / "c").mkdir(parents=True)
    for path in SITE_DIR.rglob("*"):
        if path.is_file():
            target = out / path.relative_to(SITE_DIR)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    hashes: list[str] = []
    total_bytes = 0
    for gl, picks in all_selections(model):
        digest = selection_hash(model.semester, gl, picks)
        events = [ev for s, ev in zip(model.sessions, rendered) if s.attends(gl, picks)]
        body = calendar(selection_description(model, gl, picks), events)
        path = out / "c" / f"{digest}.ics"
        if path.exists():
            raise SystemExit(f"Kolizja skrótu {digest} — zwiększ HASH_LENGTH.")
        path.write_text(body, newline="")
        hashes.append(digest)
        total_bytes += len(body.encode())

    current = set(hashes)
    legacy_count = 0
    for old_term, old_hashes in legacy.items():
        body = legacy_calendar(old_term, model, stamp)
        for digest in old_hashes:
            if digest not in current and not (out / "c" / f"{digest}.ics").exists():
                (out / "c" / f"{digest}.ics").write_text(body, newline="")
                legacy_count += 1

    (out / "options.json").write_text(json.dumps(options_json(model, rooms), ensure_ascii=False))
    (out / "hashes.txt").write_text("\n".join(sorted(hashes)) + "\n")
    meta = {
        "term": model.term,
        "semester": model.semester,
        "savedAt": model.saved_at.isoformat(),
        "sessions": len(model.sessions),
        "calendars": len(hashes),
        "legacyCalendars": legacy_count,
        "fingerprint": fingerprint(model, legacy),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    print(f"Semestr {model.semester} ({model.term}), stan planu {saved_local}", file=sys.stderr)
    print(f"Zajęć 4 roku: {len(model.sessions)}, grup lab.: {model.gl_count}, "
          f"ćwiczeniowych: {len(model.c_groups)}", file=sys.stderr)
    for e in model.electives:
        print(f"  wybieralny {e.block or '—'}: {e.name} {e.options}", file=sys.stderr)
    print(f"Kalendarzy: {len(hashes)} ({total_bytes / 1e6:.1f} MB), "
          f"starych linków z notką: {legacy_count}", file=sys.stderr)


if __name__ == "__main__":
    main()
