// Zielmonitor: Serverstand alle 5 s, laufende Zeiten jede Sekunde im Browser (Startzeit vom Server +
// eigene Uhr, nur zur Orientierung).
// Segmente 72: Kopfleiste mit Kennzahlen, eine Spaltenkopfzeile und ein gemeinsames Raster für alle
// Bereiche (Platz · Nr. · Name · Klasse · Abschnitt · Zeit · Abz./Status) – „Auf der Strecke“,
// „Letzte Zieleinläufe“ und das Ergebnis (nach Klasse gruppiert, Klassen in fester Reihenfolge:
// Strecke, Damen vor Herren, Alter aufsteigend) stehen untereinander in voller Breite.
// Alle sichtbaren Zeilen teilen sich die Höhe: Zeilenhöhe = verfügbare Höhe ÷ Zeilen, begrenzt auf
// 4–6 % der Fensterhöhe; die Schrift folgt der Zeilenhöhe. Erst wenn die Mindesthöhe nicht reicht,
// wird geblättert (je Bereich mindestens 3 Zeilen bzw. alle): Strecke/Zieleinläufe alle 10 s, das
// Ergebnis nach der eingestellten Anzeigedauer. Leere Bereiche sind ausgeblendet.
// Segmente 14: Seiten, Reihenfolge, Anzeigedauer und Live-Bereich kommen aus der Konfiguration der
// Veranstaltung (daten.monitor) und werden bei jedem Abruf übernommen, ohne Neuladen.
(function () {
  const INTERVALL_MS = 5000;
  const vorschau = new URLSearchParams(location.search).get("veranstaltung");
  const BEREICH_BLAETTERN_S = 10;
  const MIN_ZEILEN = 3;
  const ZEILE_MIN = 0.04, ZEILE_MAX = 0.06; // Anteil der Fensterhöhe
  const ZWEIZEILIG = 1.4; // schmale Fenster: Abschnitt unter dem Namen
  const SCHMAL = 900;
  const NAECHSTE = 3;
  // Live-Bereiche: alle Zeilen (HTML), Zeilen je Seite und aktuelle Seite
  const bereiche = { unterwegs: { zeilen: [], proSeite: 0, seite: 0 }, letzte: { zeilen: [], proSeite: 0, seite: 0 } };
  let bereichSek = 0;
  let daten = null;
  let letzterErfolg = null;
  let seite = 0; // Unterseite des Ergebnisses
  let seiten = [];
  let konfIndex = 0; // aktuelle Monitor-Seite (Konfiguration)
  let restSek = null;
  let letzteKonf = "";
  const konfSeiten = () => (daten && daten.monitor && daten.monitor.seiten.length ? daten.monitor.seiten : [{ typ: "ergebnis", dauer: 10, live: true, abzeichen: true, strecken: [] }]);
  const konf = () => konfSeiten()[konfIndex % konfSeiten().length];
  const mitAbzeichen = () => !!(daten.wertung && konf().abzeichen !== false);
  const $ = (id) => document.getElementById(id);
  const zwei = (n) => String(n).padStart(2, "0");
  const uhr = (ms) => { const d = new Date(ms); return `${zwei(d.getHours())}:${zwei(d.getMinutes())}:${zwei(d.getSeconds())}`; };
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  function laufzeit(startMs) {
    const s = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sek = s % 60;
    return h ? `${h}:${zwei(m)}:${zwei(sek)}` : `${m}:${zwei(sek)}`;
  }

  // --- Zellen des gemeinsamen Rasters --------------------------------------------------------

  const nrZelle = (nr) => `<td class="nr"><span class="nr-feld">${nr}</span></td>`;
  // Klasse gedämpft, „· Strecke“ nur, wenn es mehrere Strecken gibt (API liefert sonst „“)
  const klasseZelle = (x) => `<td class="klasse">${esc(x.klasse)}${x.strecke ? `<span class="mon-strecke">${esc(x.strecke)}</span>` : ""}</td>`;

  function statusZelle(z) {
    const teile = [];
    if (mitAbzeichen() && z.stufe) teile.push(`<span class="abzeichen stufe-${z.stufe.toLowerCase()}">${esc(z.stufe_text)}</span>`);
    if (z.status_text) teile.push(`<span class="status-text">${esc(z.status_text)}</span>`);
    return `<td class="status">${teile.join(" ")}</td>`;
  }

  function zeileKlassen(z) {
    const kl = ["status-" + z.status.toLowerCase()];
    if (z.vorbehalt) kl.push("vorbehalt");
    return kl.join(" ");
  }

  // Abschnittsfeld (Segmente 14/52/72): Name klein und gedämpft, Zeit, bei „mit Pfeil und Differenz“
  // ▼ schneller / ▲ langsamer und die Differenz. Ohne Zeit bleibt das Feld leer.
  function segmentFeld(s) {
    if (!s) return `<span class="segment"></span>`;
    const richtung = s.pfeil === "▼" ? "schneller" : "langsamer";
    return `<span class="segment"><span class="seg-label">${esc(s.label)}</span><span class="seg"><span class="seg-zeit">${esc(s.zeit)}</span>` +
      (s.pfeil ? `<span class="seg-pfeil ${richtung}" title="${richtung} als vorher">${s.pfeil}</span><span class="seg-diff">${esc(s.diff)}</span>` : "") + `</span></span>`;
  }

  function segmenteEinpassen() {
    for (const feld of document.querySelectorAll(".mon-liste .segment")) {
      feld.classList.remove("ohne-diff", "ohne-pfeil");
      if (!feld.querySelector(".seg")) continue;
      const zuEng = () => feld.scrollWidth > feld.clientWidth + 1;
      if (zuEng()) feld.classList.add("ohne-diff");
      if (zuEng()) feld.classList.add("ohne-pfeil");
    }
  }

  // Zeit-Spalte: passt die längste Zeit nicht, wird die Zeitschrift für alle Zeilen kleiner
  function zeitenEinpassen() {
    document.body.style.setProperty("--skala-zeit", "1");
    let q = 1;
    for (const td of document.querySelectorAll(".mon-liste td.zeit")) {
      if (td.clientWidth) q = Math.max(q, td.scrollWidth / td.clientWidth);
    }
    if (q > 1) document.body.style.setProperty("--skala-zeit", String(Math.max(0.5, 0.97 / q)));
  }

  function kennzahlen() {
    const z = daten.zaehlung, ng = daten.nicht_gestartet;
    const teile = [];
    if (z.gestartet) teile.push(`${z.gestartet} gestartet`);
    if (z.unterwegs) teile.push(`${z.unterwegs} auf der Strecke`);
    if (z.im_ziel) teile.push(`${z.im_ziel} im Ziel`);
    if (z.dns) teile.push(`${z.dns} DNS`);
    if (z.dnf) teile.push(`${z.dnf} DNF`);
    if (z.dsq) teile.push(`${z.dsq} DSQ`);
    if (ng.anzahl) {
      teile.push(`${ng.anzahl} noch nicht gestartet`);
      if (ng.naechste.length) teile.push(`nächste ${ng.naechste.slice(0, NAECHSTE).join(", ")}`);
    } else if (z.gestartet && !z.unterwegs) {
      teile.push("alle Fahrer im Ziel");
    }
    return teile.join(" · ");
  }

  function zeichnen() {
    if (!daten) return;
    if (!daten.veranstaltung) {
      $("mon-titel").textContent = "Derzeit keine Veranstaltung";
      return;
    }
    $("mon-titel").textContent = `${daten.veranstaltung.name} · ${daten.veranstaltung.datum}`;
    document.body.classList.toggle("keine-ergebnisse", !!daten.keine_ergebnisse);
    const leer = $("mon-keine-ergebnisse");
    leer.hidden = !daten.keine_ergebnisse;
    leer.textContent = daten.keine_ergebnisse || "";
    if (daten.keine_ergebnisse) return; // Segmente 46: vor der Freigabe ohne Zeiten
    const st = $("mon-status");
    st.textContent = daten.kopf;
    st.className = "mon-status " + (daten.offiziell ? "offiziell" : "vorlaeufig");
    $("mon-zaehlung").textContent = kennzahlen();

    const live = (daten.monitor && daten.monitor.live) || [];
    $("mon-unterwegs").classList.toggle("mit-segment", live.length > 0);
    const mitAbschnitt = (u) => live.some((l) => (u.abschnitte || {})[l.id]);
    bereiche.unterwegs.zeilen = daten.unterwegs.map((u) => `<tr data-nr="${u.startnummer}"${mitAbschnitt(u) ? ' class="mit-abschnitt"' : ""}>` +
      `<td class="rang"></td>${nrZelle(u.startnummer)}<td class="name">${esc(u.name)}</td>${klasseZelle(u)}` +
      `<td class="abschnitt${live.length > 1 ? " gestapelt" : ""}">${live.map((l) => segmentFeld((u.abschnitte || {})[l.id])).join("")}</td>` +
      `<td class="zeit laufzeit" data-start="${u.start_ms}">${laufzeit(u.start_ms)}</td><td class="status"></td></tr>`);
    bereiche.letzte.zeilen = daten.letzte.map((l) => `<tr data-nr="${l.startnummer}" class="${zeileKlassen(l)}">` +
      `<td class="rang">${l.rang_klasse ? l.rang_klasse + "." : ""}</td>${nrZelle(l.startnummer)}<td class="name">${esc(l.name)}</td>` +
      `${klasseZelle(l)}<td class="abschnitt"></td><td class="zeit">${esc(l.zeit)}</td>${statusZelle(l)}</tr>`);
    seiteAufbauen();
  }

  // --- Ergebnis und Rangliste als Zeilen des Rasters -------------------------------------------

  function vergleich(a, b) {
    const x = a.ordnung || [], y = b.ordnung || [];
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if (x[i] === y[i]) continue;
      return x[i] < y[i] ? -1 : 1;
    }
    return 0;
  }

  function klassenkopf(k, weiter) {
    return `<tr class="mon-klasse-kopf" data-klasse="${esc(k.name)}"><th colspan="7">${esc(k.name)}${weiter ? " (Forts.)" : ""}` +
      `${k.strecke ? `<span class="mon-strecke">${esc(k.strecke)}</span>` : ""}</th></tr>`;
  }

  // Ergebniszeile: Spalte Klasse leer (steht in der Gruppenzeile), Abschnitt = beste Zeit des ersten
  // gewählten Abschnittsfelds mit mehreren Durchfahrten (A178)
  function ergebniszeile(r) {
    const beste = r.beste ? `<span class="segment"><span class="seg-label">${esc(r.beste.label)}</span><span class="seg"><span class="seg-zeit">${esc(r.beste.zeit)}</span></span></span>` : "";
    return `<tr data-nr="${r.startnummer}" class="${zeileKlassen(r)}${r.beste ? " mit-abschnitt" : ""}"><td class="rang">${r.rang_klasse || ""}</td>${nrZelle(r.startnummer)}` +
      `<td class="name">${esc(r.name)}</td><td class="klasse"></td><td class="abschnitt">${beste}</td><td class="zeit">${esc(r.zeit)}</td>${statusZelle(r)}</tr>`;
  }

  function ranglistenzeile(z) {
    return `<tr data-nr="${z.startnummer}"><td class="rang">${z.rang || (z.ausser_konkurrenz ? "a. K." : "")}</td>${nrZelle(z.startnummer)}` +
      `<td class="name">${esc(z.name)}</td><td class="klasse">${esc(z.klasse)}</td><td class="abschnitt"></td>` +
      `<td class="zeit">${esc(z.zeit)}</td><td class="status">${esc(z.durchfahrt)}</td></tr>`;
  }

  // Posten des Ergebnisbereichs: Gruppenzeilen und Zeilen (bzw. Ranglistenzeilen)
  function ergebnisPosten() {
    const k = konf();
    if (k.typ === "rangliste") return (k.zeilen || []).map((z) => ({ html: ranglistenzeile(z) }));
    const filter = k.strecken || [];
    const klassen = [...daten.klassen].filter((x) => !filter.length || filter.includes((x.ordnung || [0])[0])).sort(vergleich);
    const posten = [];
    for (const kl of klassen) {
      posten.push({ kopf: kl });
      for (const r of kl.zeilen) posten.push({ klasse: kl, html: ergebniszeile(r), beste: !!r.beste });
    }
    return posten;
  }

  // Seiten aus Posten: höchstens p Zeilen; geht eine Klasse auf der nächsten Seite weiter, steht dort
  // ihre Gruppenzeile mit „(Forts.)“; eine Seite endet nie mit einer Gruppenzeile
  function seitenBilden(posten, p) {
    const liste = [];
    let s = [];
    for (const x of posten) {
      if (s.length && (s.length >= p || (x.kopf && s.length >= p - 1))) { liste.push(s); s = []; }
      if (!s.length && x.klasse) s.push(klassenkopf(x.klasse, true));
      s.push(x.kopf ? klassenkopf(x.kopf, false) : x.html);
    }
    if (s.length) liste.push(s);
    return liste;
  }

  // gleich viele Seiten, aber gleichmäßig gefüllt (keine Seite mit einer Zeile bei freiem Platz)
  function ergebnisSeiten(posten, p) {
    const anzahl = seitenBilden(posten, p).length;
    for (let q = Math.max(1, Math.ceil(posten.length / anzahl)); q < p; q++) {
      const versuch = seitenBilden(posten, q);
      if (versuch.length === anzahl) return versuch;
    }
    return seitenBilden(posten, p);
  }

  // --- Monitor-Seiten (Segmente 14) ----------------------------------------------------------

  function seiteAufbauen() {
    const k = konf();
    if (restSek === null) restSek = k.dauer || 10;
    $("mon-bereich").textContent = k.typ === "rangliste" ? `Rangliste ${k.titel || ""}` : "Ergebnis";
    $("mon-kopf-status").textContent = mitAbzeichen() ? "Abz./Status" : "Status";
    aufteilen();
  }

  const aussen = (el) => {
    if (!el || el.hidden || getComputedStyle(el).display === "none") return 0;
    const cs = getComputedStyle(el);
    return el.getBoundingClientRect().height + parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
  };

  function seiteAnzeigen(name, i, n) {
    $(name === "ergebnis" ? "mon-seite" : "seite-" + name).textContent = n > 1 ? `Seite ${i + 1}/${n}` : "";
    const balken = $("fortschritt-" + name);
    balken.hidden = n <= 1;
    balken.firstElementChild.style.width = `${((i + 1) / n) * 100}%`;
  }

  // Live-Bereich: jede Seite voll; die letzte Seite zeigt die letzten Zeilen (überlappt notfalls)
  function bereichZeigen(name) {
    const b = bereiche[name], n = b.zeilen.length, p = Math.max(1, b.proSeite);
    const anzahl = Math.max(1, Math.ceil(n / p));
    b.seite %= anzahl;
    const ab = Math.max(0, Math.min(b.seite * p, n - p));
    $("mon-" + name).tBodies[0].innerHTML = b.zeilen.slice(ab, ab + p).join("");
    seiteAnzeigen(name, b.seite, anzahl);
    einpassen();
  }

  function einpassen() {
    segmenteEinpassen();
    zeitenEinpassen();
  }

  // --- Höhe aufteilen (Segmente 72) ------------------------------------------------------------

  // mittlere Zeilenhöhe eines Bereichs (in Zeilen): zweizeilige Zeilen zählen ZWEIZEILIG
  const gewicht = (n, zweizeilig) => (n ? (n - zweizeilig + zweizeilig * ZWEIZEILIG) / n : 1);

  function aufteilen() {
    const k = konf();
    const H = window.innerHeight, W = window.innerWidth;
    const schmal = W < SCHMAL;
    const live = k.live !== false;
    const posten = ergebnisPosten();
    const teile = [];
    for (const name of Object.keys(bereiche)) {
      const b = bereiche[name], box = $("box-" + name);
      box.hidden = !live || !b.zeilen.length;
      $("anzahl-" + name).textContent = b.zeilen.length || "";
      const zwei = schmal && name === "unterwegs" && $("mon-unterwegs").classList.contains("mit-segment");
      $("mon-" + name).classList.toggle("zweizeilig", zwei);
      const mit = zwei ? b.zeilen.filter((z) => z.includes("mit-abschnitt")).length : 0;
      if (!box.hidden) teile.push({ name, n: b.zeilen.length, w: gewicht(b.zeilen.length, mit) });
    }
    // Ergebnis: entfällt leer, solange ein Live-Bereich etwas zeigt
    const ergLeer = !posten.length;
    $("box-ergebnis").hidden = ergLeer && teile.length > 0 && k.typ !== "rangliste";
    $("anzahl-ergebnis").textContent = posten.filter((x) => !x.kopf).length || "";
    const ergZwei = schmal && posten.some((x) => x.beste);
    const erg = { name: "ergebnis", n: Math.max(1, posten.length), w: gewicht(posten.length, ergZwei ? posten.filter((x) => x.beste).length : 0) };
    if (!$("box-ergebnis").hidden) teile.push(erg);
    // Leisten mit Seitenangabe messen (Platzhalter), dann die freie Höhe bestimmen
    for (const t of teile) seiteAnzeigen(t.name, 0, 2);
    let frei = H - aussen(document.querySelector(".mon-kopf")) - aussen($("mon-spaltenkopf")) - 2;
    for (const t of teile) frei -= aussen($("box-" + t.name).querySelector(".mon-leiste"));
    const zmin = H * ZEILE_MIN, zmax = H * ZEILE_MAX;
    const einheiten = teile.reduce((s, t) => s + t.n * t.w, 0) || 1;
    if (frei / einheiten >= zmin) {
      for (const t of teile) t.pro = t.n;
    } else {
      // jeder Bereich mindestens 3 Zeilen (bzw. alle), der Rest nach dem größten offenen Anteil
      let rest = frei / zmin;
      // (Ergebnis: eine Zeile mehr, weil eine Gruppenzeile mitzählt)
      for (const t of teile) { t.pro = Math.min(MIN_ZEILEN + (t.name === "ergebnis" ? 1 : 0), t.n); rest -= t.pro * t.w; }
      for (;;) {
        const offen = teile.filter((t) => t.pro < t.n && t.w <= rest + 1e-6);
        if (!offen.length) break;
        offen.sort((a, b) => (b.n - b.pro) / b.n - (a.n - a.pro) / a.n);
        offen[0].pro += 1;
        rest -= offen[0].w;
      }
    }
    const belegt = teile.reduce((s, t) => s + t.pro * t.w, 0) || 1;
    const zh = Math.min(zmax, frei / belegt);
    // Schrift folgt der Zeilenhöhe; ist das Fenster für die größte Zeilenhöhe zu schmal (hochkant,
    // Handy), wird sie im selben Verhältnis kleiner – weniger Zeilen bleiben größer als mehr (A179)
    const f = zh * Math.min(1, W / (schmal ? 13 : 22) / zmax);
    document.body.style.setProperty("--zh", `${Math.floor(zh * 10) / 10}px`);
    document.body.style.setProperty("--f", `${Math.floor(f * 10) / 10}px`);
    for (const t of teile) {
      if (t.name === "ergebnis") ergebnisSetzen(posten, t.pro, ergZwei);
      else { bereiche[t.name].proSeite = t.pro; bereichZeigen(t.name); }
    }
    if ($("box-ergebnis").hidden) { seiten = []; $("mon-klassen").innerHTML = ""; }
  }

  function ergebnisSetzen(posten, p, zwei) {
    if (!posten.length) {
      const k = konf();
      $("mon-klassen").innerHTML = `<p class="leer">${k.typ === "rangliste" ? `Noch keine Zeiten für ${esc(k.titel || "")}` : "Noch keine Ergebnisse"}</p>`;
      seiten = [];
      seiteAnzeigen("ergebnis", 0, 1);
      return;
    }
    seiten = ergebnisSeiten(posten, p);
    seite %= seiten.length;
    seiteZeigen(zwei);
  }

  function seiteZeigen(zwei) {
    if (!seiten.length) return;
    const k = konf();
    const extra = k.typ === "rangliste" ? ` mon-rangliste" data-rangliste="${esc(k.strecke + ":" + k.abschnitt)}` : "";
    $("mon-klassen").innerHTML = `<table class="mon-liste mon-ergebnisliste${zwei ? " zweizeilig" : ""}${extra}"><tbody>${seiten[seite].join("")}</tbody></table>`;
    seiteAnzeigen("ergebnis", seite, seiten.length);
    einpassen();
  }

  // Weiter: erst durch die Unterseiten des Ergebnisses, dann zur nächsten Monitor-Seite
  function blaettern() {
    restSek = null;
    if (seite < seiten.length - 1) {
      seite += 1;
      seiteZeigen(!!document.querySelector(".mon-ergebnisliste.zweizeilig"));
      return;
    }
    seite = 0;
    konfIndex = (konfIndex + 1) % konfSeiten().length;
    if (daten && daten.veranstaltung && !daten.keine_ergebnisse) seiteAufbauen();
  }

  // Live-Bereiche, die nicht in ihren Anteil passen, eine Seite weiter
  function bereicheBlaettern() {
    bereichSek = 0;
    for (const name of Object.keys(bereiche)) {
      const b = bereiche[name];
      if (!$("box-" + name).hidden && b.zeilen.length > b.proSeite) {
        b.seite += 1;
        bereichZeigen(name);
      }
    }
  }

  function sekunde() {
    bereichSek += 1;
    if (bereichSek >= BEREICH_BLAETTERN_S) bereicheBlaettern(); // unabhängig vom Ergebnis
    ticken();
    if (restSek === null) restSek = konf().dauer || 10;
    restSek -= 1;
    if (restSek <= 0) blaettern();
  }

  function konfSchluessel(m) {
    if (!m) return "null";
    return JSON.stringify({ ...m, seiten: (m.seiten || []).map(({ zeilen, ...seite }) => seite) });
  }

  async function laden() {
    try {
      const antwort = await fetch("/api/oeffentlich" + (vorschau ? `?veranstaltung=${encodeURIComponent(vorschau)}&vorschau=1` : ""), { cache: "no-store" });
      if (!antwort.ok) throw new Error(antwort.status);
      const version = antwort.headers.get("X-App-Version"); // Segmente 62: neue App-Version → neu laden
      if (version && document.body.dataset.version && version !== document.body.dataset.version) {
        location.reload();
        return;
      }
      daten = await antwort.json();
      // Nur eine geänderte Konfiguration startet von vorn – nicht neue Zeiten auf einer Ranglisten-Seite
      // (sonst springt der Monitor im laufenden Rennen bei jedem Abruf auf die erste Seite, Segmente 34)
      const k = konfSchluessel(daten.monitor);
      if (k !== letzteKonf) { // neue Konfiguration: von vorn
        letzteKonf = k;
        konfIndex = 0;
        seite = 0;
        restSek = null;
      }
      letzterErfolg = Date.now();
      $("mon-stand").hidden = true;
      zeichnen();
    } catch (e) {
      const stand = $("mon-stand");
      stand.hidden = false;
      stand.textContent = letzterErfolg ? `Keine Verbindung – Stand von ${uhr(letzterErfolg)}` : "Keine Verbindung";
    }
  }

  function ticken() {
    document.querySelectorAll(".laufzeit").forEach((el) => (el.textContent = laufzeit(Number(el.dataset.start))));
  }

  laden();
  setInterval(laden, INTERVALL_MS);
  // Vorschau im selben Browser: nach „Speichern“ im Admin sofort neu laden (Segmente 34)
  try {
    new BroadcastChannel("rcb-zielmonitor").onmessage = () => laden();
  } catch (e) { /* ältere Browser: beim nächsten Abruf */ }
  setInterval(sekunde, 1000);
  window.addEventListener("resize", () => daten && daten.veranstaltung && zeichnen());
  window.monitorLaden = laden;
  window.monitorBlaettern = blaettern;
  window.monitorBereicheBlaettern = bereicheBlaettern;
})();
