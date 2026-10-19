def test_up_liefert_200_ohne_token(client):
    antwort = client.get("/up")
    assert antwort.status_code == 200
    assert antwort.json() == {"status": "ok"}


def test_ungueltige_klassen_config_verhindert_start(einstellungen, tmp_path):
    import dataclasses

    import pytest
    from fastapi.testclient import TestClient

    from app.klassen import KlassenConfigFehler
    from app.main import create_app

    kaputt = tmp_path / "k.toml"
    kaputt.write_text('[[klasse]]\nname = "A"\ngeschlecht = "m"\nalter_von = 5\nalter_bis = 1\n')
    e = dataclasses.replace(einstellungen, klassen_datei=str(kaputt))
    with pytest.raises(KlassenConfigFehler), TestClient(create_app(e)):
        pass
