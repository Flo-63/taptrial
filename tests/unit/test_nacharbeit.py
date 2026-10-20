"""Nacharbeit mit Segmenten messen (Simulation 50 Fahrer, Beispielstrecke)."""

from app.simulation import nacharbeit_rundkurs


def test_ohne_fehler_keine_nacharbeit():
    m = nacharbeit_rundkurs(seed=1, vergessen=0.0, doppelt=0.0)
    assert m.fahrer == 50 and m.aufgaben == 0 and m.rennleitung == 0
    assert m.gesamtzeit_falsch == 0 and m.rang_falsch == 0 and m.segmentzeiten_falsch == 0


def test_vergessene_tipps_ohne_faelle_gesamtzeit_und_rang_gleich():
    m = nacharbeit_rundkurs(seed=2, vergessen=0.15, doppelt=0.0)
    assert m.vergessen > 0
    assert m.aufgaben == 0 and m.rennleitung == 0
    assert m.gesamtzeit_falsch == 0 and m.rang_falsch == 0


def test_reproduzierbar_und_je_fahrer():
    a = nacharbeit_rundkurs(seed=3)
    assert a == nacharbeit_rundkurs(seed=3)
    assert a.je_fahrer_aufgaben == a.aufgaben / 50 and a.je_fahrer_rennleitung == a.rennleitung / 50


def test_script_bericht(capsys):
    from scripts.nacharbeit import main

    assert main(["--laeufe", "2"]) == 0
    aus = capsys.readouterr().out
    assert (
        "Beispielstrecke" in aus
        and "Zwei Strecken" in aus
        and "je Fahrer" in aus
        and "Abschnittszeiten" in aus
    )


def test_vergessene_und_doppelte_tipps_keine_falschen_segmentzeiten():
    """Abstandsregel und automatisches Verwerfen der Doppeltipps."""
    for seed in (
        38,
        46,
        101,
    ):  # u. a. sehr schnelle Fahrer (Jugend) mit zwei fehlenden Durchfahrten
        m = nacharbeit_rundkurs(seed=seed, vergessen=0.15, doppelt=0.02)
        assert m.doppelt > 0 or seed == 101
        assert (
            m.aufgaben,
            m.rennleitung,
            m.gesamtzeit_falsch,
            m.rang_falsch,
            m.segmentzeiten_falsch,
        ) == (0, 0, 0, 0, 0)
        assert m.segmentzeiten > 100


def test_zwei_strecken_gemeinsamer_messpunkt_und_abschnitt_runde():
    """Kurze Strecke über den gemeinsamen Kreisel, Abschnitt „Runde“ auf der langen."""
    for seed in (1, 38, 46):
        m = nacharbeit_rundkurs(seed=seed, vergessen=0.15, doppelt=0.02, zwei_strecken=True)
        assert (
            m.aufgaben,
            m.rennleitung,
            m.gesamtzeit_falsch,
            m.rang_falsch,
            m.segmentzeiten_falsch,
        ) == (0, 0, 0, 0, 0)
        assert m.segmentzeiten > 150
