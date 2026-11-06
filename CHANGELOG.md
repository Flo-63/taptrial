# Changelog

Alle nennenswerten Änderungen an TapTrial. Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.2.0] – 2026-11-06

### Neu
- **Benutzer und Rollen:** Superadmin, Vereinsadmin und Event-Admin mit festem Geltungsbereich (Verein bzw. zugewiesene Veranstaltung). Die Oberfläche zeigt nur, was die Rolle darf; der Server prüft jede Admin-Seite und -Aktion.
- **Anmeldung über einen Anmeldedienst** (`TAPTRIAL_ANMELDUNG=proxy`, z. B. Authentik mit Forward-Auth vor `/admin`): Die App prüft das signierte Token bei jeder Anfrage und vergibt die Rollen selbst. Wer sich zum ersten Mal anmeldet, bekommt ein Konto ohne Rolle und sieht „Kein Zugriff“ mit einem Kontakt. Standard bleibt die Passphrase (`token`); Umstellen und Rückweg im Betriebshandbuch.
- **Seite „Benutzer“:** Konten vorab anlegen, Rollen vergeben und entziehen, Konten deaktivieren; Event-Admins auch direkt in der Veranstaltung.
- **Wer hat was gemacht:** Admin-Einträge zeigen im Protokoll, auf der Fahrer-Seite und im Protokoll-CSV, wer sie ausgelöst hat; jede Admin-Änderung steht mit Person in „Admin-Aktionen“.

### Geändert
- Die öffentliche Vorschau einer Veranstaltung gibt es nur noch für Personen, die sie im Admin sehen dürfen.
- Neue Abhängigkeit: `pyjwt` mit `cryptography` (Prüfung der Tokens).

### Intern
- Migration 3 (Verein, Benutzer, Rollen, Protokoll). `scripts/tests.sh` sichert die Ausgabe roter Läufe.

## [0.1.8] – 2026-11-03

### Behoben
- **Statusleiste der Geräte:** Bei einer Uhrwarnung konnte die Uhrzeit auf schmalen Handys oder mit breiter Systemschrift aus der Leiste rutschen. Uhrzeit und Verbindung bleiben jetzt immer ganz sichtbar; lange Hinweise werden gekürzt.

### Intern
- E2E-Tests laufen lokal und auf GitHub mit denselben Schriften, und `veroeffentlichen.sh` führt sie vor dem Commit im öffentlichen Repo auch im Export aus (`--schnell` überspringt das mit Hinweis).

## [0.1.7] – 2026-11-03

### Sicherheit
- **Rechte der Geräte:** Ein Gerät ändert im Betrieb nur noch seine eigenen Einträge; fremde nur die Rennleitung, Einträge des Admins und einen endgültigen DSQ nur der Admin. Der Server prüft das, nicht nur die Oberfläche.
- **Offizielles Ergebnis:** Solange es offiziell ist, nehmen Geräte und Gerätedatei keine Einträge mehr an. Die Geräte zeigen das, behalten zurückgestellte Einträge und senden sie nach dem Zurücksetzen auf „vorläufig“ von selbst.
- **Admin-Passphrase:** Die App startet nicht mehr mit dem Platzhalter aus `.env.example` oder mit weniger als 24 Zeichen. **Vor dem Update prüfen.**
- **Fehlversuche:** Falsche Tokens an der API zählen je Absender und führen zu einer zeitlich begrenzten Sperre; gültige Gerätelinks sind nie betroffen.
- **Öffentlich lesbar** sind nur noch die aktive Veranstaltung und abgeschlossene mit offiziellem Ergebnis. Die Vorschau wirkt nur mit Admin-Anmeldung; das Sitzungs-Cookie gilt dafür für die ganze Seite, bestehende Sitzungen melden sich für die Vorschau einmal neu an.
- **Eingaben:** Der Server weist unplausible Zeiten und zu große Anfragen ab (10 MB, auch in der Caddy-Vorlage).

### Neu
- **Import „ändern per Startnummer“:** Vorhandene Nummern überschreiben, neue ergänzen; leere optionale Felder bleiben, wie sie sind.
- **Gerät:** Abgelehnte Einträge nennen den Grund und lassen sich als Datei sichern.

### Geändert
- **Import „ersetzen“** ist gesperrt, sobald es Zeiten gibt (auch über die Kommandozeile).
- **Migrationen** sind versioniert und laufen je Schritt ganz oder gar nicht. Ohne den Schutz der Ereignisse startet die App nicht. Daten-Nachzüge laufen einmal statt bei jedem Start.
- **Tests auf GitHub:** Browser- und Container-Tests laufen als eigener Lauf „E2E“.

### Behoben
- **Gerätedatei:** Durchfahrten von Posten-Handys wurden als ungültig verworfen.
- **Protokoll und CSV** blieben nach einer unplausiblen Zeit dauerhaft mit einem Fehler stehen; alle Ansichten vertragen auch vorhandene Altdaten.
- **Segmente:** Ein Tipp vor dem Start oder nach dem Ziel verschob Rundenzeiten. Gesamtzeit und Rang waren nie betroffen.
- **Tests** wären ab April 2027 ohne Codeänderung rot geworden.
- **Gerät:** Eine Ablehnung am Posten blockierte alle ausstehenden Einträge und meldete fälschlich „Token ungültig“.

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
