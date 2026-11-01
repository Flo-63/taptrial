"""K 3: Eindeutige Kurznamen für Geräte und Rennleitung."""

from app.anzeigename import anzeigenamen


def test_eindeutiger_nachname_bleibt_wie_heute():
    assert anzeigenamen([(1, "Anna", "Müller"), (2, "Ben", "Schmidt")]) == {
        1: "Müller",
        2: "Schmidt",
    }


def test_gleicher_nachname_anfangsbuchstabe_mehr_buchstaben_ganzer_vorname():
    namen = anzeigenamen([
        (1, "Anna", "Müller"),
        (2, "Albert", "Müller"),
        (3, "Bernd", "Müller"),
        (4, "Clara", "Schmidt"),
        (5, "Jan", "Meier"),
        (6, "Janine", "Meier"),
        (7, "Lena", "Weber"),
        (8, "Lena", "Weber"),
    ])  # fmt: skip
    assert namen == {
        1: "Müller An.",
        2: "Müller Al.",
        3: "Müller B.",
        4: "Schmidt",
        5: "Meier Jan",  # Vorname steckt ganz im anderen: ganzer Vorname
        6: "Meier Jani.",
        7: "Weber Lena",  # gleicher Vor- und Nachname: mehr geht nicht, die Nummer steht daneben
        8: "Weber Lena",
    }


def test_gross_klein_und_leerzeichen_egal_ohne_vorname():
    namen = anzeigenamen([(1, "anna", " müller "), (2, "Bert", "Müller"), (3, "", "Müller")])
    assert namen == {1: "müller a.", 2: "Müller B.", 3: "Müller"}
