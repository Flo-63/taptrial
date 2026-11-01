# Changelog

Alle nennenswerten Änderungen an TapTrial. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.6] – 2026-11-01

### Neu
- **Rennleitung: Start annullieren.** Ein versehentlich gestarteter Fahrer (nicht angetreten) lässt sich auch nach den 30 s am Startgerät zurücknehmen: Reiter „Status“ → Fahrer → Grund → „Start annullieren (DNS)“. Der Start zählt nicht mehr, der Fahrer ist DNS; gelöscht wird nichts (Protokoll „Start annulliert“). Hat der Fahrer schon Tipps an Posten oder Ziel, zeigt eine Warnung die Liste und verlangt eine eigene Bestätigung. Zurücknehmbar mit „Annullierung zurücknehmen“; ein späterer echter Start zählt wieder.
- **Gleiche Nachnamen unterscheidbar:** Haben mehrere Fahrer denselben Nachnamen, zeigen Ziel, Posten und Rennleitung den Anfangsbuchstaben des Vornamens dazu („Müller A.“), wenn nötig mehr Buchstaben oder den ganzen Vornamen. Startgerät, Zielmonitor und öffentliche Ergebnisse zeigen weiter den vollen Namen.
- **Aufgaben: „diese Zeit gilt“.** Bei mehreren Ziel- oder Startstempeln eines Fahrers zeigt die Karte den Abstand und je Stempel „… – diese Zeit gilt“; die anderen werden storniert.

### Geändert
- **Startgerät:** Nummer und Name des nächsten Fahrers sind jetzt das größte Element; der START-Knopf ist kleiner, bleibt aber über die volle Breite gut zu treffen (hoch und quer).
- **Zwei Geräte an einem Messpunkt:** Tippen zwei Handys am selben Posten denselben Fahrer innerhalb von 10 s, zählt der frühere Tipp, der zweite ist ein „Doppeltipp (anderes Gerät)“ – auch bei mehrfach durchfahrenen Messpunkten verschiebt sich nichts mehr. Zielhandys führen einen Fahrer schon mit einem Zielstempel als „im Ziel“, seine Kachel taucht bei zwei Zielstempeln nicht wieder auf. Gründe nicht gezählter Tipps heißen jetzt zutreffend („nicht auf der Strecke“, „mehr Tipps als Durchfahrten“).

### Behoben
- **Startgerät:** Nach dem Start eines über „Nummer wählen“ gewählten Fahrers stand wieder der übersprungene Fahrer als Nächster da – so konnte ein nicht anwesender Fahrer versehentlich gestartet werden. Jetzt geht die Reihe hinter dem gestarteten Fahrer weiter; Übersprungene sind orange markiert, unten aufgelistet und bleiben für Nachzügler wählbar.

## [0.1.5] – 2026-10-29

### Behoben
- **Posten:** Nicht gestartete Fahrer ließen sich antippen – jetzt gesperrt (ausgegraut); „Alle Fahrer“ gibt sie frei, falls der Start noch nicht gemeldet ist.
- **Posten:** „Rückgängig“ nahm auch Zwischenzeiten von Fahrern zurück, die schon im Ziel sind – jetzt gesperrt mit Hinweis auf die Rennleitung (außer der Posten ist selbst das Ziel der Strecke).

## [0.1.4] – 2026-10-27

### Neu
- **Ziel einer Strecke an einer Zwischenzeit:** Als „Ziel (letzter Punkt)“ einer Strecke lässt sich auch eine Zwischenzeit wählen – z. B. wenn eine kurze Strecke am Posten einer langen endet und dort nur ein Gerät steht. Das Posten-Gerät zeigt die Fahrer aller Strecken, die dort vorbeikommen oder enden; wer dort das Ziel hat, trägt „Ziel“, sein Tipp ist die Zielzeit (Gesamtzeit, Rang, Wertung, Zielmonitor, Ergebnis, Druck, CSV, Urkunden), danach ist er fertig. Für die anderen Strecken bleibt es eine Zwischenzeit. Kommt die Zwischenzeit im Verlauf der Strecke schon vorher vor, lehnt der Editor das mit Meldung ab. In der Rennleitung stehen solche Zieleinläufe unter „Letzte Einläufe“ (Korrektur und Rückgängig, kein Umbuchen). Eine andere Streckenzuordnung wertet alle Tipps neu. Streckendateien kennen dafür `zielort_art = "zwischenzeit"`.

### Behoben
- Der GitHub-Lauf „Tests“ zu 0.1.3 war rot: ein Test las eine Datei, die nicht zum veröffentlichten Stand gehört.

## [0.1.3] – 2026-10-21

### Geändert
- **Version nur noch an einer Stelle:** Die Versionsnummer steht nur noch in `pyproject.toml`. Das Docker-Image bekommt sie als Build-Argument `VERSION` (Label `org.opencontainers.image.version`); ohne Build-Argument bricht `docker build` mit einer klaren Meldung ab, statt still eine veraltete Nummer einzutragen. Selbst bauen: siehe README („Selbst bauen“).

## [0.1.2] – 2026-10-21

### Behoben
- **Aktiv setzen erst nach abgeschlossener Einrichtung:** Über die Adresse des letzten Assistenten-Schritts ließ sich eine Veranstaltung mitten in der Einrichtung abschließen und aktiv setzen. Jetzt geht kein Schritt des Assistenten vor dem offenen; unter „Status & Datenschutz“ steht bei offener Einrichtung „Aktivieren erst möglich, wenn die Einrichtung abgeschlossen ist“ mit „Einrichtung fortsetzen“. Hinweise aus „Als Nächstes“ sperren nicht.
- Eine schon aktive Veranstaltung mit offener Einrichtung bleibt unverändert; „Als Nächstes“ führt zur Einrichtung. Die Demo bricht in diesem Fall nicht mehr ab und lässt sie aktiv.
- Das CLI `veranstaltung` setzt über dieselbe Prüfung aktiv wie der Admin.

## [0.1.1] – 2026-10-19

### Behoben
- **Vereinsname war fast unsichtbar:** Er stand nur klein auf der Startseite. Jetzt steht er vorn – Startseite, Kopf der Ergebnisse („Verein · Veranstaltung“), Admin-Kopf, Zielmonitor, Druckansichten, Urkunden und Seitentitel; „TapTrial“ nur noch klein. Ohne Vereinsnamen bleibt alles neutral.
- Änderungen am Branding sind sofort sichtbar: HTML-Seiten werden nicht mehr vom Browser zwischengespeichert, ein offener Zielmonitor übernimmt den Namen beim nächsten Abruf.
- **Versionsangabe** stand mitten auf dem Bildschirm: jetzt klein unten in der Admin-Seitenleiste bzw. rechts in der Fußzeile der öffentlichen Seiten und der Hilfe; Zielmonitor und Geräte ohne Version.

### Geändert
- Umgebungsvariablen heißen `TAPTRIAL_…` (z. B. `TAPTRIAL_TOKEN_ADMIN`). Die alten Namen `RCB_…` gelten weiter (Hinweis „veraltet“ im Log), der neue Name gewinnt.
- „Als Nächstes“ erinnert dezent, wenn noch kein Vereinsname eingetragen ist; Hilfe-Abschnitt „Branding“.
- GitHub Actions auf aktuelle Hauptversionen mit Node 24, Runner `ubuntu-24.04`.
- Kommentare und Testbeschreibungen ohne Verweise auf interne Arbeitsnotizen.

## [0.1.0] – 2026-10-14

Erste öffentliche Version: Zeitnahme für Einzelzeitfahren mit Handys und Tablets im Browser – ohne Chips, ohne App-Store.

### Hinzugefügt
- **Start, Ziel, Rennleitung, Posten** als Web-Apps (PWA) mit QR-Code-Zugang je Gerät; Zeiten in Zehntelsekunden, **offline-fähig** (Service Worker, IndexedDB), Übertragung bei Netz automatisch.
- **Zielerfassung** mit großen Kacheln je Fahrer, Zeit ohne Nummer stempeln und später zuordnen; Rennleitung mit Korrekturen, Status DNS/DNF/DSQ (vorläufig und endgültig) und Plausibilitätsprüfung.
- **Alle Zeitdaten als Ereignisse** (append-only): nichts wird gelöscht oder überschrieben, jede Korrektur mit Notiz im Protokoll.
- **Admin** mit geführter Einrichtung (Grunddaten, Klassen und Wertung, Strecken, Teilnehmer, Zielmonitor), Übersicht „Als Nächstes“, Aufgabenliste, Protokoll, Ergebnis offiziell erklären, Abschließen, Anonymisieren und restlosem Löschen.
- **Mehrere Strecken** je Veranstaltung, Klassenzuordnung je Strecke, Ränge je Strecke.
- **Zwischenzeiten und Runden** (Segmente): Messpunkte mit eigenen Posten-Geräten, Rundenblöcke, Auswertungsabschnitte, Ranglisten je Abschnitt; Strecken als Datei speichern und laden.
- **Klassen- und Wertungssätze** als Vorlagen (mitgeliefert: Altersklassen und Leistungswertung des Deutschen Radsportabzeichens), Import/Export als TOML und CSV.
- **Öffentliche Ergebnisse** (Seite, Druck, CSV) und **Zielmonitor** für den Großbildschirm mit Live-Bereich, Abschnittszeiten und konfigurierbaren Seiten.
- **Druck**: Ergebnisliste, Urkunden, Start- und Zielprotokoll als Papier-Rückfallebene, Gerätelinks mit QR-Codes, Aushang „Live-Ergebnisse“.
- Kurzanleitungen je Gerät (je eine DIN-A4-Seite) und Admin-Handbuch, in der App unter „Hilfe“.
- Demo-Veranstaltung zum Ausprobieren: `python -m app.verwaltung demo`.
- Docker-Image mit Healthcheck, Compose-Dateien (auch mit Caddy für HTTPS), Version unter `/version`.
