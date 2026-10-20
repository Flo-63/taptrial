# Changelog

Alle nennenswerten Änderungen an TapTrial. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

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
