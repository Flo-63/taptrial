"""Name und Erscheinungsbild nach außen (Release 1): „TapTrial – Zeitnahme per Handy“.

Interne Namen bleiben, damit bestehende Installationen und Geräte weiterlaufen: Umgebungsvariablen
`RCB_*`, Dienst- und Paketname `rcb-zeitfahren`, die Datenbank und die Schlüssel im Gerätespeicher
(`rcb_*`, IndexedDB `rcb-zeitfahren`). Vereinsname (und später Logo) kommen aus dem Branding
(app/installation.py); eine frische Installation ist neutral.
"""

NAME = "TapTrial"
UNTERTITEL = "Zeitnahme per Handy"
VOLL = f"{NAME} – {UNTERTITEL}"

# Release 4: ruhiger Hinweis (keine Warnung) im Assistenten, gleicher Satz in README und BETRIEB
HINWEIS_GENEHMIGUNG = (
    "Hinweis: Auf öffentlichen Straßen ist für Veranstaltungen mit Zeitnahme in der Regel eine "
    "Erlaubnis nach § 29 StVO nötig, auf anderen Flächen die Zustimmung des Eigentümers."
)
