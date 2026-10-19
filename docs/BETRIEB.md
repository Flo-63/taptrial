# Betriebshandbuch – TapTrial

Kurz und zum Ausdrucken: Ablauf am Vorabend, am Renntag und danach. Installation, Sicherung und Update stehen im README. `<eure-domain>` ist die Adresse eurer Installation.

Hinweis: Auf öffentlichen Straßen ist für Veranstaltungen mit Zeitnahme in der Regel eine Erlaubnis nach § 29 StVO nötig, auf anderen Flächen die Zustimmung des Eigentümers.

**Rollen und Geräte**
| Wer | Gerät | Link |
|---|---|---|
| Starter | Handy A | `…/start?token=…` |
| Zielrichter | Handy B | `…/ziel?token=…` (Sicht „Erfassen“) |
| Rennleitung | Handy C (oder Reiter „Rennleitung“ auf Handy B) | `…/ziel?token=…&sicht=rennleitung` |
| Veranstalter | Laptop | `https://<eure-domain>` → „Anmeldung Admin“ → Admin-Passphrase |
| Zuschauer / Zielmonitor | beliebig | `https://<eure-domain>` → „Ergebnisse“ bzw. „Zielmonitor“ |

Die Gerätelinks und QR-Codes stehen im Admin unter „Geräte“. Sie gelten nur für die jeweilige Veranstaltung.

---

## 1. Vorabend

- [ ] Letztes Update eingespielt, danach **kein Update mehr** bis nach dem Rennen.
- [ ] Im Admin: Veranstaltung mit dem Assistenten („Neue Veranstaltung einrichten“) angelegt, **Einrichtung abgeschlossen und aktiv** (sonst zeigen alle Geräte „Zeitnahme noch nicht freigegeben“; hat die aktive Veranstaltung Startzeiten, lässt sich bis zum Abschließen keine andere aktiv setzen – auch nicht per CLI), Teilnehmerliste hochgeladen (Admin → Teilnehmer → „CSV importieren“). Fehler in der Import-Vorschau direkt korrigieren; importiert wird erst, wenn keine Fehler mehr offen sind. Nachmeldungen/Korrekturen unter „Teilnehmer“.
- [ ] Einmal je Installation (Admin → Vorlagen): **Branding** (Vereinsname für die Startseite) und **Impressum & Datenschutz** (Link oder Text) eintragen – die Übersicht erinnert daran, solange es fehlt.
- [ ] QR-Codes der Gerätelinks ausdrucken oder auf dem Laptop bereithalten.
- [ ] Aushang „Live-Ergebnisse“ (Admin → Geräte) ausdrucken und an Start, Ziel und Anmeldung aufhängen.
- [ ] **Start- und Zielprotokoll** (Admin → Drucken & Export → „Start- und Zielprotokoll (A4) drucken“) nach dem letzten Teilnehmer-Import drucken und an Start **und** Ziel mitnehmen (mit Kugelschreiber). Fällt ein Handy oder das Netz aus, dort Uhrzeiten von Hand notieren und hinterher im Admin als manuelle Zeit nachtragen.
- [ ] Admin → Einstellungen (Klassen & Wertung): Wertungssatz wählen (der Assistent fragt je Längen-Platz, z. B. 5 km und 20 km bei DRA A.1: aus Datei laden oder Platzhalter anlegen, bei einem Versehen „Ändern“); Admin → Strecken: **gemessene Länge je Strecke** eintragen (bei A.1 mindestens 5,0 bzw. 20,0 km) und die Klassenzuordnung prüfen.
- [ ] **Nur bei mehreren Starts oder Zielen:** Admin → Messpunkte: weitere Messpunkte der Art „Start“ bzw. „Ziel“ anlegen (Art wählen, „Messpunkt hinzufügen“), dann je Strecke im Reiter „Verlauf“ Start und Ziel wählen. Unter „Geräte“ hat jeder Messpunkt eigene Karten; je Messpunkt ein Handy mit dessen Link einplanen. Beispiele siehe unten („Zwei Startorte“).
- [ ] **Nur mit Segmenten (Zwischenzeiten):** Admin → Messpunkte: Zwischenzeiten der Veranstaltung anlegen (einer kann von mehreren Strecken genutzt werden); Admin → Strecken → Strecke öffnen → Reiter „Verlauf“: Punktfolge (mit Rundenblöcken) eintragen und die angezeigte Abfolge prüfen – vor der ersten Zeit, danach ist sie eingefroren. Auswertungsabschnitte (z. B. „Runde“ = Kreisel bis nächster Kreisel) lassen sich jederzeit ergänzen. Liegt ein Messpunkt auf der Ziellinie, ist das kein Problem (sein Tipp beim Zieleinlauf zählt nicht). Je Messpunkt einen Helfer mit Handy einplanen; die Posten-Links stehen unter „Geräte“ (eine Karte je Posten), Kurzanleitung „Posten“ ausdrucken.
- [ ] Admin → Einstellungen → **Zielmonitor und Ergebnisse**: Seiten (Ergebnis, ggf. Ranglisten einzelner Abschnitte), Anzeigedauer und die Abschnittsfelder im Live-Bereich einstellen und mit „Vorschau“ prüfen (der Assistent bietet das als Schritt 4 mit Vorgaben an). Ohne Einstellung zeigt der Monitor wie bisher das Ergebnis. Ein offener Monitor lädt sich nach einem Update der App selbst neu; „Letzte Zieleinläufe“ und „Auf der Strecke“ stehen untereinander. Darunter „Zwischenzeiten öffentlich“ und die „Ergebnistabelle (Seite, Druck, CSV)“: welche Abschnitte als Spalte erscheinen.
- [ ] Zielmonitor (Tablet/Bildschirm im Querformat) mit `https://<eure-domain>/monitor` öffnen; Ergebnisse öffentlich unter `/ergebnisse` (kein Token nötig).
- [ ] Auf **jedem** Gerät den passenden Link öffnen, dann „Zum Startbildschirm hinzufügen“ und die App **vom Startbildschirm** einmal öffnen (füllt den Offline-Speicher). Statusleiste: „online · 0 ausstehend“, Uhrzeit des letzten Syncs.
- [ ] Startgerät zeigt „Nächster: Nr. 1 …“, Zielgerät zeigt alle Kacheln, jeder Posten „Zwischenzeit <Name>“.
- [ ] Vor dem Testlauf im Admin Klassensatz, Wertungssatz, Strecken und Zuordnung prüfen – mit der ersten Zeit werden Klassen, Wertung und Streckenzuordnung für diese Veranstaltung eingefroren (die Längen bleiben bis „offiziell“ änderbar).
- [ ] Testlauf: Am Start eine Nummer starten, am Ziel antippen, in der Rennleitung prüfen, dass der Einlauf mit Fahrzeit erscheint. Danach am Ziel (Rennleitung) „Rückgängig“ und am Start „Rückgängig“ (innerhalb 30 s) bzw. später über den Admin korrigieren. Alles wird protokolliert, nichts wird gelöscht.
- [ ] **Generalprobe:** am besten mit einer **eigenen Testveranstaltung** („Neue Veranstaltung einrichten“, Einrichtung abschließen, aktiv setzen) – Links verteilen, starten, Ziel, Posten, Rennleitung durchspielen, auch einmal im Flugmodus. Danach die Testveranstaltung unter „Status & Datenschutz“ **komplett löschen** (die Testhandys melden „Diese Veranstaltung gibt es nicht mehr“) und die echte Veranstaltung wieder aktiv setzen (geht nur, solange die Testveranstaltung nicht mehr aktiv ist oder gelöscht wurde – eine Veranstaltung mit Startzeiten wird nicht verdrängt). Einen Probemodus gibt es nicht. Bis zur Freigabe (Einrichtung abgeschlossen **und** aktiv) zeigen alle Geräte „Zeitnahme noch nicht freigegeben“ und schalten sich danach von selbst frei.
- [ ] Versehentlich zu früh aktiv gesetzt? Solange es keine Startzeit gibt: Status & Datenschutz → „**Nicht mehr aktiv setzen …**“ – die Geräte sind dann wieder gesperrt.
- [ ] Handys voll laden, Powerbanks und Kabel einpacken. Displayhelligkeit hoch, automatische Sperre auf „nie“ bzw. lang.
- [ ] Automatische Uhrzeit (Netzzeit) auf allen Geräten eingeschaltet.
- [ ] Fallback einpacken: Handstoppuhr oder zweites Handy für Video mit sichtbarer Uhr, Papierliste der Teilnehmer, Stift.
- [ ] Sicherung ziehen (README, „Sicherung und Wiederherstellung“).

### Beispiele: zwei Starts

- **Anderer Start, gleiches Ziel:** Jugend (5 km) startet am Parkplatz „Nord“, Erwachsene (20 km) am Standard-„Start“; beide kommen am gemeinsamen „Ziel“ an. Messpunkte: Start „Nord“ anlegen; Strecke „5 km“ → Verlauf → Start „Nord“. Geräte: zwei Starthandys (Karten „Start“ und „Start Nord“), ein Zielhandy. Jedes Starthandy zeigt nur seine Fahrer mit eigener Reihenfolge („Nächster“, Warteliste, Nummer wählen, DNS aufheben); fällt eines ins Funkloch, arbeitet es weiter und sendet später – Doppelstarts entstehen nicht. Das Zielhandy zeigt alle Fahrer.
- **Beides verschieden:** Strecke „Rundkurs“ von „Nord“ nach „Hof“, alle anderen von „Start“ nach „Ziel“. Dann je Messpunkt ein Handy: „Start“, „Start Nord“, „Ziel“, „Ziel Hof“ (dazu „Rennleitung“ bzw. „Rennleitung Hof“, falls getrennt). Das Zielhandy am Hof zeigt in „Erfassen“ nur die Rundkurs-Fahrer; „Zeit ohne Nummer“ dort bietet die Rennleitung bevorzugt diesen Fahrern an.
- **Gleicher Start, anderes Ziel:** nur ein weiteres Ziel anlegen und bei der betroffenen Strecke wählen; es bleibt ein Starthandy.
- Probelauf vorher: je Handy den Link öffnen, oben steht der Messpunkt („Start · Nord“); die Übersicht meldet unter „Als Nächstes“ jeden genutzten Messpunkt, dessen Gerät sich noch nicht verbunden hat.

## 2. Renntag

### Vor dem Start
- [ ] Netzabdeckung an Start und Ziel prüfen (Statusleiste „online“). Offline ist kein Problem, nur der Sync wartet dann.
- [ ] **Uhr prüfen:** Die Statusleiste muss an Start **und** Ziel (und an jedem Posten) „Uhr ±0,…“ **unter 0,5 s** zeigen. Bei rot „Uhr weicht … ab – Gerät prüfen“ automatische Uhrzeit aus/an schalten oder Gerät tauschen. „Uhr ungeprüft“ heißt: noch keine Verbindung zum Server. Admin → Geräte → „Uhren der Geräte“ zeigt die letzte Abweichung jedes Geräts mit Messzeitpunkt (Posten mit Namen); die Übersicht meldet unter „Als Nächstes“ Posten, die sich noch nicht verbunden haben. Gemessene Zeiten werden nie automatisch korrigiert.
- [ ] **Uhrenvergleich (zusätzlich):** Start- und Zielhandy nebeneinander legen, „Uhrzeit“ oben rechts vergleichen (das ist die Geräteuhr, keine Rennuhr). Die Sekunden müssen **gleichzeitig umspringen**; springt eine sichtbar später um (mehr als ein Augenblick, ca. 0,2 s) → **Gerät tauschen** oder Uhrzeit-Einstellung prüfen (automatische Zeit aus/an). Die Anzeige zeigt nur ganze Sekunden.
- [ ] Apps vom Startbildschirm öffnen, nicht im Browser-Tab (sonst anderer Speicher, besonders iOS).
- [ ] Zielrichter bleibt in der Sicht „Erfassen“; die Rennleitung arbeitet auf eigenem Gerät oder im Reiter „Rennleitung“.

### Während des Rennens
**Start**
- START drücken, wenn der Fahrer losfährt. Die Anzeige springt auf den nächsten.
- Fahrer nicht da: „Überspringen“ (kommt ans Ende der Warteliste). Fährt nicht: „DNS“ (zweimal tippen).
- Falsch gestartet: „Rückgängig“ innerhalb von 30 s. Später nur noch über Admin/Rennleitung klären.
- Reihenfolge durcheinander: „Nummer wählen“.
- Sind alle durch, zeigt das Startgerät „Alle Fahrer gestartet“.

**Ziel (Zielrichter, Sicht „Erfassen“)**
- Fahrer erkannt: Kachel antippen (einmal; Doppeltipp wird ignoriert).
- Nummer nicht erkannt oder zwei Fahrer gleichzeitig: sofort „Zeit ohne Nummer“ (für jeden Fahrer einmal), Nummern merken/zurufen. Die Zuordnung macht die Rennleitung.
- Fahrer ohne Kachel (z. B. als DNF markiert) kommt doch: „Zeit ohne Nummer“.
- Die Zeile unter „Zeit ohne Nummer“ zeigt, wie viele noch auf der Strecke sind; am Ende „Alle gestarteten Fahrer im Ziel“.

**Posten (nur mit Segmenten)**
- Fahrer fährt vorbei: seine Kachel einmal antippen; sie zeigt danach die nächste Durchfahrt („Durchfahrt k/N“) und verschwindet nach der letzten. ±1 s und „Rückgängig“ wie am Ziel.
- Fahrer verpasst oder unsicher: **nicht** nachtragen – eine fehlende Durchfahrt hat keine Folgen für Gesamtzeit und Rang.

**Rennleitung**
- Reiter zeigt „Rennleitung (N)“, solange offene Zeiten existieren: Zeit antippen, Fahrer wählen.
- Falsch angetippt: unter „Letzte Zieleinläufe“ „Umbuchen“ oder „Rückgängig“ (zweimal tippen).
- Aufgabe/Sturz: Fahrer antippen → „DNF“ (zweimal tippen). Irrtum: „Status aufheben“.
- Segmente: Fehlende Posten-Tipps und Doppeltipps erledigt die Auswertung selbst; es gibt keine Fälle dafür.
- Regelverstoß: Fahrer antippen → Grund eintragen → „DSQ vorläufig“ (zweimal tippen). Der Fahrer bleibt mit Zeit und Rang „unter Vorbehalt“ (gelb). Endgültig entscheidet der Veranstalter im Admin unter „Aufgaben“ (bestätigen = DSQ rot, ohne Rang; oder verwerfen, jeweils mit Notiz).

**Funkloch**
- Statusleiste „offline · N ausstehend“: einfach weiterarbeiten. Alle Einträge sind auf dem Handy gespeichert, auch wenn die App geschlossen wird oder der Akku gewechselt wird.
- Sobald wieder Netz da ist, wird automatisch gesendet („0 ausstehend“). Die Zeiten sind die Zeitpunkte des Antippens, nicht des Sendens.
- Das Zielgerät weiß offline nicht, wer gestartet ist: Kacheln eventuell ausgegraut, trotzdem antippbar.

## 3. Nach dem Rennen

- [ ] Auf **allen** Geräten „0 ausstehend“ abwarten, bevor Apps geschlossen oder Handys ausgeschaltet werden. Zeigt ein Gerät „abgelehnt“, Screenshot machen und dem Veranstalter melden.
- [ ] Kommt ein Gerät mit „N ausstehend“ nicht mehr ins Netz: dort „Gerät zurücksetzen“ (zweimal tippen) → „Einträge exportieren“, Datei zum Laptop schicken und im Admin unter „Protokoll“ → „Gerätedatei einlesen“ übernehmen – **vor** dem Abschließen (danach ist die Veranstaltung schreibgeschützt).
- [ ] Admin „Aufgaben“: keine offenen Zeiten, keine Konflikte; Fehler dort unter „Korrekturen“ beheben (jede Korrektur mit Notiz, alles im Protokoll).
- [ ] Vorläufige DSQ entscheiden (bestätigen oder verwerfen).
- [ ] „Ergebnis offiziell erklären“ (geht erst ohne offene vorläufige DSQ und Konflikte; bei Einspruch mit Notiz zurücknehmbar).
- [ ] Mit Segmenten: Ranglisten je Abschnitt unter „Drucken & Export“ drucken bzw. als CSV laden (öffentlich nur mit „Zwischenzeiten öffentlich“).
- [ ] Ergebnisliste prüfen, exportieren (CSV) bzw. drucken/als PDF speichern (Admin → Drucken & Export → „Ergebnisliste als PDF“; eine Klasse pro Seite).
- [ ] **Sicherung ziehen** (README, „Sicherung und Wiederherstellung“) und an zweitem Ort ablegen.
- [ ] Veranstaltung **abschließen** (Admin → Status & Datenschutz → „Abschließen …“, Namen der Veranstaltung eintippen): danach schreibgeschützt, Gerätelinks ungültig, Exporte weiter möglich.

**Abschließen, Löschen, Token erneuern – der Unterschied:**
- **Abschließen:** Ergebnisse und Exporte bleiben, nichts ist mehr änderbar; Geräte melden „Veranstaltung beendet“ und löschen ihre Daten.
- **Löschen:** entfernt die Veranstaltung mit allen Daten (vorher Backup/Exporte!). Eine aktive/laufende wird dabei beendet, danach ist keine aktiv. Geräte melden „Veranstaltung gelöscht“ und löschen ihre Daten. Der Server merkt sich nur Fingerabdrücke der alten Gerätetokens, keine Daten.
- **Token erneuern:** nur der Link einer Rolle wird ungültig, das Rennen läuft weiter; Geräte mit dem alten Link zeigen „Zugangstoken ungültig“ und behalten ihre Daten.
- In allen Fällen: nicht übertragene Einträge bleiben auf dem Gerät (Hinweis + „Einträge exportieren“).
- Gerätedaten gehören zur Veranstaltung: Öffnet ein Gerät den Link einer **neuen** Veranstaltung (z. B. nach Löschen, ohne dass es die alte noch einmal gesehen hat), beginnt es leer – keine alten Starts, kein „Zuletzt gestartet“, keine alten Einstellungen. Nicht übertragene Einträge der alten bleiben getrennt erhalten („N Einträge einer früheren Veranstaltung – als Datei sichern“) und werden nicht an die neue gesendet.
- [ ] Alle Helfer öffnen die App danach **einmal** mit Netz: Sie zeigt „Veranstaltung beendet – Daten auf diesem Gerät gelöscht“ (Teilnehmerliste und übertragene Einträge werden vom Handy gelöscht). Erscheint „N Einträge nicht übertragen“: „Einträge exportieren“ und die Datei an die Rennleitung schicken (nach dem Abschließen nur noch zur Ansicht; übernehmen lässt sie sich nur vorher, siehe oben).
- [ ] Danach das Symbol auf dem Startbildschirm entfernen.
- [ ] Nach Ablauf der Frist (Standard 6 Monate, Admin zeigt „Anonymisierung fällig“): **anonymisieren** per Knopf. Es wird nichts automatisch gelöscht.

## 4. Notfälle

| Problem | Maßnahme |
|---|---|
| Startgerät defekt/leer | Ersatzhandy, Startlink öffnen. Bereits gestartete Fahrer kommen vom Server (falls online gewesen). Sonst Startzeiten auf Papier notieren, später als manuelle Zeit im Admin nachtragen. |
| Zielgerät defekt/leer | Zweites Handy mit Ziellink (beliebig viele Zielgeräte möglich). Bis dahin Handstoppuhr/Video. Ausstehende Einträge des defekten Geräts werden gesendet, sobald es wieder an ist und Netz hat. |
| Posten-Handy defekt/leer (Segmente) | Ersatzhandy mit dem Posten-Link dieser Zwischenzeit (Admin → Geräte). Verpasste Durchfahrten nicht nachtragen – sie haben keine Folgen für die Wertung. Handy verloren: „Token erneuern“ auf der Karte dieses Postens. |
| Zeit vergessen/verpasst | Rennleitung: „Zeit ohne Nummer“ geht nicht nachträglich. Zeit von Handstoppuhr/Video notieren und später als manuelle Zeit (mit Notiz) im Admin nachtragen. |
| Zwei Fahrer gleichzeitig | Zweimal „Zeit ohne Nummer“, Reihenfolge merken, Rennleitung ordnet zu. |
| Uhrabweichung (Sekunden springen versetzt um) erst später bemerkt | Ruhe bewahren, Abweichung notieren (beide Uhren fotografieren). Korrektur nach dem Rennen über manuelle Zeiten. |
| Server nicht erreichbar | Weiterarbeiten (offline). Prüfen: `curl -fsS https://<eure-domain>/up`, dann auf dem Server `docker compose logs app`. Notfalls `docker compose restart app`. |
| Gerät zeigt „Zugangstoken ungültig“ | Falscher oder erneuerter Link bzw. Veranstaltung abgeschlossen. Aktuellen Link im Admin unter „Geräte“ öffnen. |
| Handy mit Gerätelink verloren | Admin → „Geräte“ → Token erneuern; allen anderen Geräten derselben Rolle den neuen Link geben. Ausstehende Einträge eines Geräts mit altem Link werden abgewiesen, deshalb vorher „0 ausstehend“ abwarten, wenn möglich. |
| Handy wird weitergegeben / Daten sollen sofort weg | Unten auf der Seite „Gerät zurücksetzen“ (zweimal tippen). Nicht übertragene Einträge bleiben erhalten und können exportiert werden. |
| Admin-Passphrase vergessen | Auf dem Server in `.env` nachsehen bzw. neue eintragen und `docker compose up -d` (nicht am Renntag). |
| Anmeldung „Zu viele Fehlversuche“ | 15 Minuten warten (Sperre je IP) oder anderes Netz/Gerät. |
| Daten kaputt | Letzte Sicherung zurückspielen (README). Geräte senden danach ausstehende Einträge erneut. |

## 5. Demo-Veranstaltung

Zum Vorführen und Üben gibt es eine fertige, **abgeschlossene** Veranstaltung „Demo-Zeitfahren Kottenforst“ (04.10.2026): 15 Teilnehmer (Radsportlegenden, Comicfiguren, zwei Jugendliche) auf „20 km“ (Start 20k → P1, zwei Runden P1 → P2 → P1, P1 → Ziel) und „5 km“ (Start Jugend → Ziel), DRA A.1 flach mit Abzeichen, Zwischenzeiten, ein vergessener Klick, ein Doppeltipp, eine in der Rennleitung zugeordnete Zielzeit ohne Nummer, ein DNF und ein DNS; Ergebnis offiziell. Sie entsteht über die normalen Funktionen der App (Assistent, Geräteereignisse, Rennleitung, offiziell erklären, abschließen) und ist danach **nicht aktiv** – sie blockiert keine neue Veranstaltung, eine vorher aktive bleibt aktiv. Läuft gerade eine Veranstaltung mit Startzeiten, bricht das Skript ab.

- Mit Docker: `docker compose exec app python -m app.verwaltung demo`
- Ohne Docker: `uv run python -m app.verwaltung demo`
- Gibt es die Demo schon, bricht das Skript mit Hinweis ab; `--ersetzen` löscht sie vorher über die normale Löschfunktion und legt sie neu an.
- Fehlt der Wertungssatz „DRA A.1 flach“ (umbenannt oder gelöscht), wird nichts angelegt.
- Ansehen: Admin → „Demo-Zeitfahren Kottenforst“ bzw. öffentlich `/ergebnisse?veranstaltung=<ID>`.
- Entfernen: Admin → Demo öffnen → „Status & Datenschutz“ → „Komplett löschen“ (Namen eintippen).
