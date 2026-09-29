# Plan zajęć PK — rok 4, subskrybowany kalendarz

Strona z formularzem: wybierasz grupę ćwiczeniową, laboratoryjną i podgrupy
przedmiotów wybieralnych, a na końcu dostajesz link do kalendarza `.ics`,
który sam się aktualizuje, gdy plan się zmieni.

Obejmuje 4 rok Informatyki, studia niestacjonarne I stopnia, i tylko bieżący
semestr.

Strona: `https://adam-m-04.github.io/calendar-pk/`

---

## Subskrypcja

Najprościej przyciskiem „Subskrybuj w Apple Calendar” na stronie. Ręcznie:

1. **Mac:** Kalendarz → Plik → Nowa subskrypcja kalendarza… → wklej link.
2. **Odświeżanie: co godzinę.**
3. Na iPhonie kalendarz pojawi się sam, jeśli w iCloud masz włączoną
   synchronizację kalendarzy.

Google Calendar też przyjmie link („Z adresu URL”), ale odświeża subskrypcje
rzadko — potrafi to trwać dobę.

---

## Skąd biorą się dane

| Źródło | Uwagi |
|---|---|
| **FK Planer** — `ii.pk.edu.pl/~fkruzel/fk-planer-lti/public/plan-ns/snapshot.php` | Publiczny JSON stojący za planem online Katedry Informatyki. Źródło nadrzędne. |
| xlsx na `it.pk.edu.pl` | **Nie używamy.** To ręczny eksport z planera i szybko się starzeje: 28.09.2026 różnił się od JSON-a w 21 ze 161 zajęć 4 roku. |

### Jak czytać grupy

- **Grupy laboratoryjne GL1–GL4** to najdrobniejszy podział roku (w JSON-ie
  kohorta `sem. 7 / gr. N` przy przedmiocie podzielonym na 4 grupy).
- **Grupy ćwiczeniowe C1, C2** obejmują kolejne GL-e: C1 = GL1+GL2,
  C2 = GL3+GL4. W JSON-ie wyglądają tak samo (`gr. 1`, `gr. 2`) — rozróżniamy
  je po liczbie grup przedmiotu.
- **K01, P02, Ć01…** przy przedmiotach wybieralnych to **osobne grupy zapisów**,
  niezwiązane z numerem GL (tak mówi też nagłówek xlsx). Dlatego formularz pyta
  o nie osobno przy każdym przedmiocie.

### Pułapki w danych

- Plan skraca długie nazwy („Zaawansowane technologie proj.ap.int”). Pełne
  nazwy są w `SUBJECT_FULL` w `generate.py`.
- Prowadzący są zapisani jako „Nazwisko Imię”; w kalendarzu odwracamy kolejność.
- Kody sal (L1–L5, W1…) rozwijamy z listy sal w JSON-ie; adresy kampusów są
  wpisane na sztywno w `CAMPUS_ADDRESS`.
- Plan bywa niespójny sam ze sobą — potrafi dać dwie grupy tej samej osoby
  w tym samym czasie. Formularz pokazuje takie kolizje, kalendarz ma wtedy
  oba wydarzenia.

---

## Jak działają linki

GitHub Pages serwuje tylko statyczne pliki, więc nie da się przefiltrować
planu parametrami w adresie. `generate.py` generuje **osobny plik dla każdej
możliwej kombinacji**: grupa laboratoryjna × (podgrupa albo „nie chodzę”) dla
każdego przedmiotu wybieralnego. Dla zimy 2026/27 to 4608 plików, ok. 160 MB
(przed kompresją).

Nazwą pliku jest 12 znaków SHA-256 z opisu wyboru:

```
v1|sem7|gl2|podstawy-przetwarzania-w-chmurze=K02,programowanie-robotow=K02,…
```

Formularz liczy dokładnie ten sam skrót w przeglądarce (`canonical()` w
`generate.py` i w `site/app.js` muszą zostać zgodne). Grupa ćwiczeniowa nie
wchodzi do opisu, bo wynika z laboratoryjnej.

UID każdego wydarzenia zależy tylko od identyfikatora bloku w planie, więc gdy
zajęcia przeniosą się na inny termin, kalendarz przesunie wydarzenie zamiast
dodać nowe.

### Zmiana semestru

JSON zawsze niesie tylko bieżący semestr. Gdy planer przełączy się na nowy,
workflow zapisuje skróty starych linków do `legacy/` (commit robi sam). Stare
linki dalej działają, ale zwracają jedno wydarzenie „Nowy semestr — wybierz
przedmioty ponownie” z linkiem do formularza.

---

## Powiadomienia o zmianach tuż przed zajęciami

Kalendarz aktualizuje się sam, ale nie mówi, że coś się w nim zmieniło.
Dlatego gdy plan się zmieni, workflow porównuje nową wersję z poprzednią dla
obserwowanych linków i wysyła maila, jeśli zmiana dotyczy zajęć w ciągu
najbliższych 4 dni (liczy się stary albo nowy termin). Od środy obejmuje to
cały najbliższy weekend. O zmianach z większym wyprzedzeniem nie ma maila —
trafiają tylko do kalendarza.

Mail wymienia przeniesienia, usunięte i nowe zajęcia, zmiany sali,
prowadzącego i uwag w planie:

```
Temat: Plan PK: zmiany na sob. 03.10, niedz. 04.10

• Zaawansowane technologie projektowania aplikacji internetowych — projekt (grupa P01)
  było: sobota 03.10, 08:00–10:30, zdalnie
  jest: sobota 03.10, 12:00–14:30, zdalnie

• Przedsiębiorczość innowacyjna — wykład (grupy Ć01, Ć02)
  usunięte z planu: niedziela 04.10, 10:45–13:15, zdalnie
```

### Konfiguracja (raz)

1. Na koncie Google włącz weryfikację dwuetapową i utwórz **hasło aplikacji**
   (https://myaccount.google.com/apppasswords, nazwa np. „calendar-pk”).
2. Dodaj sekrety repozytorium (Settings → Secrets and variables → Actions
   albo `gh secret set NAZWA`):

   | Sekret | Zawartość |
   |---|---|
   | `SMTP_USER` | adres Gmail, z którego idą maile |
   | `SMTP_PASSWORD` | hasło aplikacji z punktu 1 |
   | `NOTIFY_WATCH` | obserwowane kalendarze, po jednym w linii: link (albo sam skrót z linku) i opcjonalnie adresy odbiorców. Bez adresu mail idzie na `SMTP_USER`. |

3. **Actions → Zbuduj i opublikuj kalendarze → Run workflow** z zaznaczonym
   „Wyślij testowy e-mail”. Przyjdzie wiadomość z najbliższymi zajęciami.

### Jak to działa

- Porównanie robi `notify.py` na `options.json` (identyfikatory bloków,
  terminy, sale, prowadzący, uwagi), więc zmiana samego formatu `.ics` nie
  wywołuje maili. Gdy planer usunie blok i utworzy nowy zamiast go przesunąć,
  para z tym samym przedmiotem, formą i grupą jest pokazywana jako zmiana
  terminu.
- Poprzednia wersja planu leży w cache Actions (`notify-state-*`), a nie jest
  pobierana ze strony: CDN Pages przez 10 minut po publikacji oddaje starą
  wersję (parametr w adresie nie pomaga) i ta sama zmiana przyszłaby dwa razy.
  Cache wygasa po 7 dniach bez publikacji — wtedy porównanie idzie
  z opublikowanym `options.json`.
- Wysyłka to osobny job `notify`, więc jej awaria nie wstrzymuje publikacji
  kalendarzy. Run jest wtedy czerwony, a „Re-run failed jobs” wysyła te same
  wiadomości jeszcze raz.
- Plan sprawdzany jest co godzinę (przy włączonym
  [zewnętrznym harmonogramie](#harmonogram)), więc mail przychodzi do godziny
  po zmianie w planerze. Okno 4 dni to `NOTIFY_DAYS` w `notify.py`.
- W logach publicznego repo linki występują tylko jako numery („link nr 1”).

Podgląd bez wysyłania:

```bash
NOTIFY_WATCH=<skrót> python3 notify.py diff --old stary/options.json --new public/options.json --today 2026-10-01 --preview
```

---

## Uruchomienie lokalnie

Wymaga tylko Pythona 3.11+, bez zależności.

```bash
python3 generate.py --output public
python3 -m http.server 8765 --directory public
```

`--snapshot plik.json` pozwala pracować na zapisanej kopii planu bez sieci.
`--min-sessions` (domyślnie 30) to zabezpieczenie: jeśli w planie jest mniej
zajęć 4 roku, build pada i nic nie publikuje, zamiast wyczyścić ludziom
kalendarze.

---

## Konfiguracja GitHuba (raz)

1. Repozytorium **publiczne** (Pages w prywatnym wymaga płatnego planu).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Actions → Zbuduj i opublikuj kalendarze → Run workflow** — pierwszy build
   ręcznie.
4. Opcjonalnie powiadomienia e-mail — sekrety z sekcji
   [Powiadomienia](#powiadomienia-o-zmianach-tuż-przed-zajęciami).
5. Zewnętrzny harmonogram — sekcja niżej.

Dalej leci samo: workflow co godzinę pobiera plan, a publikuje tylko wtedy,
gdy zmieniły się dane 4 roku albo kod (porównuje `fingerprint`
z opublikowanego `meta.json`). Raz na 45 dni bez commitów wypycha pusty
commit, bo GitHub wyłącza harmonogram po 60 dniach bezczynności repozytorium.

### Harmonogram

Cron GitHuba działa „w miarę możliwości” i przy obciążeniu gubi terminy. Po
starcie repo z 28 godzinnych terminów odpaliły się 4, każdy z opóźnieniem
14–44 min. Dlatego workflow co godzinę uruchamia z zewnątrz
[cron-job.org](https://cron-job.org) przez API GitHuba. Własny cron (co 15 min)
zostaje jako zapas.

1. **Token:** GitHub → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token.
   - Repository access: *Only select repositories* → `calendar-pk`
   - Permissions → Repository permissions → **Actions: Read and write**
     (nic więcej)
   - Ważność: najdłuższa dostępna. Wpisz sobie datę wygaśnięcia do kalendarza.
2. **cron-job.org** → Create cronjob:
   - URL: `https://api.github.com/repos/Adam-M-04/calendar-pk/actions/workflows/build.yml/dispatches`
   - Harmonogram: co godzinę, np. w 5. minucie
   - Advanced → Request method: **POST**
   - Headers:

     | Nagłówek | Wartość |
     |---|---|
     | `Authorization` | `Bearer <token z punktu 1>` |
     | `Accept` | `application/vnd.github+json` |
     | `X-GitHub-Api-Version` | `2022-11-28` |
     | `Content-Type` | `application/json` |

   - Request body: `{"ref":"main","inputs":{"only_if_changed":"true"}}`
   - Notifications: włącz maila przy nieudanym wywołaniu (np. gdy token
     wygaśnie).
3. **Test run** w cron-job.org powinien dostać odpowiedź `204`, a w Actions
   pojawi się run `workflow_dispatch`, który przy niezmienionym planie kończy
   się komunikatem „Plan bez zmian — pomijam publikację”.

`only_if_changed` sprawia, że wywołanie z zewnątrz działa jak cron: publikuje
tylko przy zmianie. Ręczny „Run workflow” bez tej opcji publikuje zawsze.
Token może tylko uruchamiać workflow w tym jednym repo. Nie da się nim zmienić
kodu ani odczytać sekretów.
