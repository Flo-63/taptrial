// Zielgerät mit zwei Sichten:
//  „Erfassen“: nur Kacheln (einstufig) und „Zeit ohne Nummer“, feste Reihenfolge, Entprellung.
//  „Rennleitung“: offene Zeiten zuordnen, letzte Zieleinläufe aller Geräte umbuchen/löschen,
//  Status DNF bzw. „DSQ vorläufig“ (mit Grund) setzen und aufheben; endgültiger DSQ nur im Admin. Auch allein auf einem eigenen Gerät nutzbar (?sicht=rennleitung).
import { Geraet, formatFahrzeit, formatUhrzeit, streckeMarke } from "./geraet.js";

const ENTPRELLUNG_MS = 1000;
const BESTAETIGUNG_MS = 3000;
const ENDSTATUS = ["DNS", "DNF", "DSQ"];
const SICHTEN = ["erfassen", "rennleitung"];
const STUFE_TEXT = { GOLD: "Gold", SILBER: "Silber", BRONZE: "Bronze" };
// Schnellkorrektur: je Tipp ±1 s, Summe je Stempel höchstens ±5 s
const KORREKTUR_MS = 1000;
const MAX_KORREKTUR_MS = 5000;
let korrekturHinweis = null; // uuid des Stempels, an dem die Grenze erreicht wurde
// Erwartete Fahrer: Rahmen ab 2 min vor der nach Median erwarteten Ankunft
const ERWARTET_VORLAUF_MS = 2 * 60 * 1000;

// Eigener Rennleitungs-Link (/rennleitung, Ziel und Rennleitung getrennt): eigene Rolle und
// eigener Speicher; der Ziel-Link zeigt dann nur „Erfassen“.
const ROLLE = document.body.classList.contains("geraet-rennleitung") ? "rennleitung" : "ziel";
const g = new Geraet(ROLLE);
const $ = (id) => document.getElementById(id);

const letzterTipp = new Map(); // Startnummer -> Zeitpunkt des letzten Tipps
let zuordnen = null; // { uuid, titel } während eine Zeit zugeordnet/umgebucht wird
let statusFahrer = null; // Startnummer, deren Status gerade geändert wird
let bestaetigen = null; // Schlüssel der Aktion, die auf den zweiten Tipp wartet
let bestaetigungsTimer = null;
let aufgeklappt = null; // uuid der einzigen aufgeklappten Zeile in „Letzte Einläufe“
// Reiter der Rennleitung: genau ein Bereich sichtbar. null = noch nicht gewählt; dann beim ersten Öffnen der Rennleitung mit
// bekanntem Stand „Offene Zeiten“, wenn es welche gibt, sonst „Letzte Einläufe“; danach fest.
const BEREICHE = ["offen", "letzte", "status"];
let bereich = null;

const sichtAusUrl = new URLSearchParams(location.search).get("sicht");
if (SICHTEN.includes(sichtAusUrl)) localStorage.setItem(g.schluessel("sicht"), sichtAusUrl);
let sicht = localStorage.getItem(g.schluessel("sicht")) === "rennleitung" ? "rennleitung" : "erfassen";
if (ROLLE === "rennleitung") sicht = "rennleitung";

// Serverstand plus eigene Ereignisse, die der Stand noch nicht enthält.
function zustand() {
  const s = g.stand || {};
  const teilnehmer = s.teilnehmer || [];
  const startBekannt = !!g.stand;
  const gestartet = new Set(s.gestartet || []);
  const imZiel = new Set(s.im_ziel || []);
  const status = new Map();
  const vorbehalt = new Map(); // Startnummer -> Grund eines offenen vorläufigen DSQ
  for (const t of teilnehmer) {
    if (ENDSTATUS.includes(t.status)) status.set(t.startnummer, t.status);
    if (t.dsq_vorlaeufig) vorbehalt.set(t.startnummer, t.dsq_vorlaeufig);
  }

  // Zielstempel: uuid -> { uuid, zeit_ms, nr }
  const stempel = new Map();
  for (const z of s.letzte_ziele || [])
    stempel.set(z.uuid, { uuid: z.uuid, zeit_ms: z.zeit_ms, nr: z.startnummer, korrektur: z.korrektur_ms || 0 });
  for (const o of s.offene_ziele || [])
    stempel.set(o.uuid, { uuid: o.uuid, zeit_ms: o.zeit_ms, nr: null, korrektur: o.korrektur_ms || 0, ort: o.ort });

  for (const e of g.nichtImStand()) {
    if (e.art === "ZIEL") {
      stempel.set(e.uuid, { uuid: e.uuid, zeit_ms: e.zeit_ms, nr: e.startnummer ?? null, korrektur: 0, ort: s.ort && String(s.ort.id) });
      if (e.startnummer != null) imZiel.add(e.startnummer);
    } else if (e.art === "ZUORDNUNG") {
      const st = stempel.get(e.bezug_uuid);
      if (!st) continue;
      if (st.nr != null) imZiel.delete(st.nr);
      st.nr = e.startnummer ?? null;
      if (st.nr != null) imZiel.add(st.nr);
    } else if (e.art === "STORNO") {
      const st = stempel.get(e.bezug_uuid);
      if (!st) continue;
      if (st.nr != null) imZiel.delete(st.nr);
      stempel.delete(e.bezug_uuid);
    } else if (e.art === "ZEITKORREKTUR") {
      // wie auf dem Server: Summe der Deltas, höchstens ±5 s
      const st = stempel.get(e.bezug_uuid);
      if (!st || Math.abs(st.korrektur + e.delta_ms) > MAX_KORREKTUR_MS) continue;
      st.korrektur += e.delta_ms;
      st.zeit_ms += e.delta_ms;
    } else if (e.art === "STATUS") {
      // wie auf dem Server: der letzte Status gilt
      status.delete(e.startnummer);
      vorbehalt.delete(e.startnummer);
      if (e.wert === "DSQ_VORLAEUFIG") vorbehalt.set(e.startnummer, e.notiz || "");
      else if (e.wert !== "OK") status.set(e.startnummer, e.wert);
    }
  }

  const alleStempel = [...stempel.values()];
  const offeneZeiten = alleStempel.filter((st) => st.nr == null).sort((a, b) => a.zeit_ms - b.zeit_ms);
  const nachNr = new Map(teilnehmer.map((t) => [t.startnummer, t]));
  const letzte = alleStempel
    .sort((a, b) => b.zeit_ms - a.zeit_ms)
    .slice(0, 5)
    .map((st) => {
      const t = st.nr != null ? nachNr.get(st.nr) : null;
      return { ...st, t, fahrzeit: t && t.start_ms ? st.zeit_ms - t.start_ms : null };
    });

  const jetzt = Date.now();
  const kurzVorher = (nr) => jetzt - (letzterTipp.get(nr) || 0) < ENTPRELLUNG_MS;
  const mitInfo = (t) => ({
    ...t,
    status: status.get(t.startnummer) || null,
    vorbehalt: vorbehalt.has(t.startnummer) ? vorbehalt.get(t.startnummer) : null,
    aktiv: !startBekannt || gestartet.has(t.startnummer),
    erledigt: imZiel.has(t.startnummer),
  });
  // Erfassen: feste Reihenfolge nach Startnummer; DNS/DNF/DSQ ausgeblendet,
  // nicht gestartete ausgegraut an ihrem Platz, angetippte bleiben 1 s stehen.
  // Zielort: „Erfassen“ und Endzustand nur mit Fahrern der Strecken, die hier enden
  const hier = (t) => !s.ort || s.ort.strecken.includes(t.strecke ?? 0);
  const erfassen = teilnehmer
    .filter(hier)
    .map(mitInfo)
    .filter((t) => !t.status && (!t.erledigt || kurzVorher(t.startnummer)));
  // Zuordnen/Umbuchen: alle ohne Zielzeit, auch ausgeblendete (Fahrer kommt doch ins Ziel)
  const zuordenbar = teilnehmer.map(mitInfo).filter((t) => !t.erledigt);
  const alle = teilnehmer.map(mitInfo);

  // Endzustand/Überblick für „Erfassen“ (feste Zeilenhöhe, verschiebt keine Kacheln)
  let info = "";
  let fertig = false;
  const eigene = alle.filter(hier);
  if (startBekannt && eigene.length) {
    const ohneEnde = eigene.filter((t) => !t.status);
    const aufStrecke = ohneEnde.filter((t) => t.aktiv && !t.erledigt).length;
    const nichtGestartet = ohneEnde.filter((t) => !t.aktiv && !t.erledigt).length;
    if (!eigene.some((t) => t.aktiv)) {
      info = "Noch niemand gestartet";
    } else if (aufStrecke === 0) {
      fertig = true;
      info = "Alle gestarteten Fahrer im Ziel" + (nichtGestartet ? ` · ${nichtGestartet} noch nicht gestartet` : "");
    } else {
      info = `${aufStrecke} auf der Strecke`;
    }
  }

  const letzteMitVorbehalt = letzte.map((l) => ({ ...l, vorbehalt: l.nr != null && vorbehalt.has(l.nr) }));

  // zuletzt auf DIESEM Gerät erfasster Einlauf (für die Leiste in „Erfassen“)
  let eigener = null;
  const eigeneZiele = g.log.filter((e) => e.art === "ZIEL").sort((a, b) => b.erstellt_ms - a.erstellt_ms);
  const eigeneStornos = new Set(g.log.filter((e) => e.art === "STORNO").map((e) => e.bezug_uuid));
  if (eigeneZiele.length && !eigeneStornos.has(eigeneZiele[0].uuid)) {  // zurückgenommen: Leiste leer
    const e = eigeneZiele[0];
    let st = stempel.get(e.uuid);
    if (!st) {
      const korrektur = g.log
        .filter((k) => k.art === "ZEITKORREKTUR" && k.bezug_uuid === e.uuid)
        .reduce((summe, k) => (Math.abs(summe + k.delta_ms) > MAX_KORREKTUR_MS ? summe : summe + k.delta_ms), 0);
      st = { uuid: e.uuid, zeit_ms: e.zeit_ms + korrektur, nr: e.startnummer ?? null, korrektur };
    }
    const t = st.nr != null ? nachNr.get(st.nr) : null;
    eigener = { ...st, t, fahrzeit: t && t.start_ms ? st.zeit_ms - t.start_ms : null };
  }
  return { erfassen, zuordenbar, alle, offeneZeiten, letzte: letzteMitVorbehalt, nachNr, info, fertig, eigener, stempel };
}

// Zuordnen/Umbuchen: Fahrer der Strecken, die am Zielort des Stempels enden, zuerst
function bevorzugt(z, fahrer) {
  const s = g.stand || {};
  const st = zuordnen && z.stempel.get(zuordnen.uuid);
  const strecken = (st && st.ort && s.ziel_orte && s.ziel_orte[st.ort]) || (s.ort && s.ort.strecken);
  if (!strecken) return fahrer;
  const vorne = (t) => (strecken.includes(t.strecke ?? 0) ? 0 : 1);
  return [...fahrer].sort((a, b) => vorne(a) - vorne(b)); // stabil: sonst Startnummer
}

function median(werte) {
  const w = [...werte].sort((a, b) => a - b);
  const m = Math.floor(w.length / 2);
  return w.length % 2 ? w[m] : (w[m - 1] + w[m]) / 2;
}

// Startnummern, die nach der bisherigen Medianzeit ihrer Strecke in etwa 2 min erwartet werden
// oder überfällig sind. Ohne Startstand oder ohne Zieleinlauf auf der Strecke: keine.
function erwartete(fahrer) {
  const teilnehmer = (g.stand && g.stand.teilnehmer) || [];
  const zeiten = new Map(); // Strecke -> Fahrzeiten
  for (const t of teilnehmer) {
    if (t.fahrzeit_ms == null) continue;
    const k = t.strecke ?? 0;
    if (!zeiten.has(k)) zeiten.set(k, []);
    zeiten.get(k).push(t.fahrzeit_ms);
  }
  const jetzt = Date.now();
  const ergebnis = new Set();
  for (const t of fahrer) {
    const k = t.strecke ?? 0;
    if (!t.aktiv || t.erledigt || t.start_ms == null || !zeiten.has(k)) continue;
    if (jetzt >= t.start_ms + median(zeiten.get(k)) - ERWARTET_VORLAUF_MS) ergebnis.add(t.startnummer);
  }
  return ergebnis;
}

// Rahmen setzen, ohne Kacheln neu zu bauen oder zu verschieben
function erwarteteMarkieren() {
  if (sicht !== "erfassen") return;
  const menge = erwartete(zustand().erfassen);
  for (const b of $("fahrer").children) {
    const an = menge.has(Number(b.dataset.nr));
    b.classList.toggle("erwartet", an);
    if (an) b.dataset.erwartet = "1";
    else delete b.dataset.erwartet;
  }
}

function kachel(t, beiTipp, mitStatus = false) {
  const b = document.createElement("button");
  b.className = "kachel" + (t.aktiv ? "" : " nicht-gestartet") + (t.erledigt ? " erledigt" : "");
  b.dataset.nr = t.startnummer;
  const nr = document.createElement("span");
  nr.className = "kachel-nr";
  nr.textContent = t.startnummer;
  const name = document.createElement("span");
  name.className = "kachel-name";
  name.textContent = t.nachname;
  b.append(nr, name);
  const marke = streckeMarke(g.stand, t);
  if (marke) {
    marke.classList.add("kachel-strecke");
    b.append(marke);
    b.dataset.strecke = marke.textContent;
  }
  if (mitStatus && t.erledigt) {
    // Status-/Auswahl-Kacheln: „im Ziel“ dezent mit Text statt grüner Fläche
    b.classList.remove("erledigt");
    b.classList.add("im-ziel");
    const z = document.createElement("span");
    z.className = "kachel-im-ziel";
    z.textContent = "✓ im Ziel";
    b.append(z);
  }
  if (mitStatus && (t.status || t.vorbehalt != null)) {
    const st = document.createElement("span");
    st.className = "kachel-status" + (t.status === "DSQ" ? " dsq" : "");
    st.textContent = t.status || "DSQ vorläufig";
    if (!t.status) b.classList.add("vorbehalt");
    b.append(st);
  }
  b.addEventListener("click", () => beiTipp(t.startnummer));
  return b;
}

function bestaetigungAnfordern(schluessel) {
  if (bestaetigen === schluessel) return true;
  bestaetigen = schluessel;
  clearTimeout(bestaetigungsTimer);
  bestaetigungsTimer = setTimeout(() => {
    bestaetigen = null;
    zeichnen();
  }, BESTAETIGUNG_MS);
  zeichnen();
  return false;
}

function bestaetigungZuruecksetzen() {
  bestaetigen = null;
  clearTimeout(bestaetigungsTimer);
}

function zeichnen() {
  const z = zustand();
  const nurErfassen = ROLLE === "ziel" && !!(g.stand && g.stand.rennleitung_getrennt);
  if (nurErfassen) sicht = "erfassen";
  $("haupt-reiter").hidden = nurErfassen || ROLLE === "rennleitung";
  const erfassen = sicht === "erfassen";

  $("tab-erfassen").classList.toggle("aktiv", erfassen);
  $("tab-erfassen").setAttribute("aria-selected", String(erfassen));
  $("tab-rennleitung").classList.toggle("aktiv", !erfassen);
  $("tab-rennleitung").setAttribute("aria-selected", String(!erfassen));
  $("sicht-erfassen").hidden = !erfassen;
  $("kopf-erfassen").hidden = !erfassen;
  $("sicht-rennleitung").hidden = erfassen;
  $("kopf-rennleitung").hidden = erfassen;
  if (bereich === null && !erfassen && (g.stand || z.offeneZeiten.length)) bereich = z.offeneZeiten.length ? "offen" : "letzte";
  const sichtbar = bereich || "letzte";
  for (const b of BEREICHE) {
    $(`rl-${b}`).hidden = b !== sichtbar;
    $(`rl-tab-${b}`).classList.toggle("aktiv", b === sichtbar);
    $(`rl-tab-${b}`).setAttribute("aria-selected", String(b === sichtbar));
  }
  $("rl-tab-offen").textContent = `Offene Zeiten (${z.offeneZeiten.length})`;
  $("link-hilfe").href = erfassen ? "/hilfe/ziel" : "/hilfe/rennleitung";
  const badge = $("badge-offen");
  badge.hidden = z.offeneZeiten.length === 0;
  badge.textContent = `(${z.offeneZeiten.length})`;

  $("fahrer").replaceChildren(...z.erfassen.map((t) => kachel(t, kachelGetippt)));
  erwarteteMarkieren();
  $("ziel-info").textContent = z.info;
  $("ziel-info").classList.toggle("fertig", z.fertig);
  zeichneLetztenEinlauf(z.eigener);

  // Rennleitung: Zuordnen/Umbuchen
  $("zuordnen-box").hidden = !zuordnen;
  if (zuordnen) {
    $("zuordnen-text").textContent = `${zuordnen.titel} – Fahrer antippen`;
    $("auswahl").replaceChildren(...bevorzugt(z, z.zuordenbar).map((t) => kachel(t, fahrerGewaehlt, true)));
  } else {
    $("auswahl").replaceChildren();
  }

  $("keine-offenen").hidden = z.offeneZeiten.length > 0;
  $("offene-zeiten").replaceChildren(
    ...z.offeneZeiten.map((o) => {
      const b = document.createElement("button");
      b.className = "offen" + (zuordnen && zuordnen.uuid === o.uuid ? " gewaehlt" : "");
      b.dataset.uuid = o.uuid;
      b.textContent = formatUhrzeit(o.zeit_ms);
      b.addEventListener("click", () => zuordnenStarten(o.uuid, `Zeit ${formatUhrzeit(o.zeit_ms)} zuordnen`));
      return b;
    }),
  );

  $("keine-letzten").hidden = z.letzte.length > 0;
  $("letzte").replaceChildren(...z.letzte.map(zeichneLetzten));

  // Rennleitung: Status
  const gewaehlt = statusFahrer != null ? z.alle.find((t) => t.startnummer === statusFahrer) : null;
  $("status-box").hidden = !gewaehlt;
  if (gewaehlt) {
    const endgueltig = gewaehlt.status === "DSQ";
    let aktuell = gewaehlt.status || "kein";
    if (gewaehlt.vorbehalt != null) aktuell = `DSQ vorläufig (${gewaehlt.vorbehalt})`;
    if (endgueltig) aktuell = "DSQ (bestätigt, nur im Admin änderbar)";
    $("status-text").textContent = `Nr. ${gewaehlt.startnummer} ${gewaehlt.vorname} ${gewaehlt.nachname} – Status: ${aktuell}`;
    $("status-text").classList.toggle("vorbehalt-text", gewaehlt.vorbehalt != null);
    const hatStatus = !!gewaehlt.status || gewaehlt.vorbehalt != null;
    const sichtbar = {
      DNF: !endgueltig && gewaehlt.status !== "DNF",
      DSQ_VORLAEUFIG: !endgueltig && gewaehlt.vorbehalt == null,
      OK: !endgueltig && hatStatus,
    };
    $("status-grund").hidden = !sichtbar.DSQ_VORLAEUFIG;
    const grund = $("status-grund").value.trim();
    for (const [id, wert, label] of [
      ["btn-status-dnf", "DNF", "DNF"],
      ["btn-status-dsq", "DSQ_VORLAEUFIG", "DSQ vorläufig"],
      ["btn-status-ok", "OK", "Status aufheben"],
    ]) {
      const b = $(id);
      b.hidden = !sichtbar[wert];
      b.disabled = wert === "DSQ_VORLAEUFIG" && !grund;
      const warte = bestaetigen === `status:${gewaehlt.startnummer}:${wert}`;
      b.classList.toggle("bestaetigen", warte);
      b.textContent = warte ? `Wirklich ${label}?` : label;
    }
  }
  $("status-auswahl").replaceChildren(
    ...z.alle.map((t) => {
      const b = kachel(t, statusFahrerGewaehlt, true);
      if (t.startnummer === statusFahrer) b.classList.add("gewaehlt");
      return b;
    }),
  );
}

function korrekturText(ms) {
  return `${ms >= 0 ? "+" : "−"}${(Math.abs(ms) / 1000).toFixed(1).replace(".", ",")} s`;
}

function einlaufText(e) {
  const wer = e.nr != null ? `Nr. ${e.nr}${e.t ? " " + e.t.nachname : ""}` : "Zeit ohne Nummer";
  return `${wer} · ${formatUhrzeit(e.zeit_ms)}` + (e.fahrzeit != null ? ` · ${formatFahrzeit(e.fahrzeit)}` : "");
}

async function zeitKorrigieren(st, delta) {
  if (Math.abs((st.korrektur || 0) + delta) > MAX_KORREKTUR_MS) {
    korrekturHinweis = st.uuid; // Grenze erreicht: nichts senden
    zeichnen();
    return;
  }
  korrekturHinweis = null;
  await g.erfasse({ art: "ZEITKORREKTUR", bezug_uuid: st.uuid, delta_ms: delta, startnummer: st.nr });
}

// Leiste fester Höhe in „Erfassen“: letzter Einlauf dieses Geräts mit −1 s / +1 s / Rückgängig
function zeichneLetztenEinlauf(e) {
  const text = $("letzter-text");
  $("btn-minus").disabled = $("btn-plus").disabled = $("btn-einlauf-zurueck").disabled = !e;
  $("letzter-einlauf").dataset.uuid = e ? e.uuid : "";
  const warte = !!e && bestaetigen === `einlauf:${e.uuid}`;
  $("btn-einlauf-zurueck").textContent = warte ? "Wirklich?" : "Rückgängig";
  $("btn-einlauf-zurueck").classList.toggle("bestaetigen", warte);
  if (!e) {
    text.textContent = "Noch kein Einlauf erfasst";
    return;
  }
  text.textContent = einlaufText(e);
  if (e.korrektur) {
    const k = document.createElement("span");
    k.className = "korrigiert-text";
    k.textContent = ` · korrigiert ${korrekturText(e.korrektur)}`;
    text.append(k);
  }
  if (korrekturHinweis === e.uuid) {
    const h = document.createElement("span");
    h.className = "korrektur-hinweis";
    h.id = "korrektur-hinweis";
    h.textContent = "Mehr als ±5 s – bitte über Rennleitung korrigieren";
    text.append(document.createElement("br"), h);
  }
}

// Segmentzeiten des Fahrers unter der kompakten Zeile (Segmente)
function zwischenText(t) {
  if (!t || !t.zwischenzeiten || !t.zwischenzeiten.length) return "";
  return t.zwischenzeiten.map(([label, ms]) => `${label} ${ms == null ? "–" : formatFahrzeit(ms)}`).join(" · ");
}

function zeichneLetzten(eintrag) {
  const li = document.createElement("li");
  li.dataset.uuid = eintrag.uuid;
  const offen = aufgeklappt === eintrag.uuid;
  li.classList.toggle("aufgeklappt", offen);
  const text = document.createElement("button"); // kompakte Zeile: antippen klappt die Aktionen auf
  text.className = "letzte-text";
  text.setAttribute("aria-expanded", String(offen));
  text.addEventListener("click", () => {
    aufgeklappt = offen ? null : eintrag.uuid;
    bestaetigungZuruecksetzen();
    zeichnen();
  });
  const wer = eintrag.nr != null ? `Nr. ${eintrag.nr}${eintrag.t ? " " + eintrag.t.nachname : ""}` : "ohne Nummer";
  text.textContent =
    `${formatUhrzeit(eintrag.zeit_ms)} · ${wer}` +
    (eintrag.fahrzeit != null ? ` · ${formatFahrzeit(eintrag.fahrzeit)}` : "");
  const marke = streckeMarke(g.stand, eintrag.t);
  if (marke) text.append(" ", marke);
  if (eintrag.t && eintrag.t.ausser_konkurrenz) text.append(" · a. K.");
  if (eintrag.t && eintrag.t.stufe) {
    const st = document.createElement("span");
    st.className = "stufe-text";
    st.textContent = ` · ${STUFE_TEXT[eintrag.t.stufe]}${eintrag.vorbehalt ? " (unter Vorbehalt)" : ""}`;
    text.append(st);
  }
  if (eintrag.vorbehalt) {
    const vb = document.createElement("span");
    vb.className = "vorbehalt-text";
    vb.textContent = " · DSQ vorläufig";
    text.append(vb);
  }
  if (eintrag.korrektur) {
    const k = document.createElement("span");
    k.className = "korrigiert-text";
    k.textContent = ` · korrigiert ${korrekturText(eintrag.korrektur)}`;
    text.append(k);
  }
  if (korrekturHinweis === eintrag.uuid) {
    const h = document.createElement("span");
    h.className = "korrektur-hinweis";
    h.textContent = " · Mehr als ±5 s – bitte als manuelle Zeit im Admin";
    text.append(h);
  }
  const minus = document.createElement("button");
  minus.className = "btn-klein btn-korrektur-minus";
  minus.textContent = "−1 s";
  minus.addEventListener("click", () => zeitKorrigieren(eintrag, -KORREKTUR_MS));
  const plus = document.createElement("button");
  plus.className = "btn-klein btn-korrektur-plus";
  plus.textContent = "+1 s";
  plus.addEventListener("click", () => zeitKorrigieren(eintrag, KORREKTUR_MS));
  const umbuchen = document.createElement("button");
  umbuchen.className = "btn-klein btn-umbuchen";
  umbuchen.textContent = "Umbuchen";
  umbuchen.addEventListener("click", () =>
    zuordnenStarten(eintrag.uuid, `Umbuchen: Zeit ${formatUhrzeit(eintrag.zeit_ms)}`),
  );
  const rueck = document.createElement("button");
  rueck.className = "btn-klein btn-rueckgaengig";
  const schluessel = `storno:${eintrag.uuid}`;
  rueck.textContent = bestaetigen === schluessel ? "Wirklich löschen?" : "Rückgängig";
  rueck.classList.toggle("bestaetigen", bestaetigen === schluessel);
  rueck.addEventListener("click", async () => {
    if (!bestaetigungAnfordern(schluessel)) return;
    bestaetigungZuruecksetzen();
    if (zuordnen && zuordnen.uuid === eintrag.uuid) zuordnen = null;
    aufgeklappt = null;
    await g.erfasse({ art: "STORNO", bezug_uuid: eintrag.uuid, startnummer: eintrag.nr });
  });
  const aktionen = document.createElement("div");
  aktionen.className = "letzte-aktionen";
  aktionen.hidden = !offen;
  aktionen.append(minus, plus, umbuchen, rueck);
  li.append(text);
  const zwischen = zwischenText(eintrag.t);
  if (zwischen) {
    const zw = document.createElement("div");
    zw.className = "letzte-zwischen";
    zw.textContent = zwischen;
    li.append(zw);
  }
  li.append(aktionen);
  return li;
}

function sichtWechseln(neu) {
  sicht = neu;
  localStorage.setItem(g.schluessel("sicht"), neu);
  if (neu === "erfassen") {
    zuordnen = null;
    statusFahrer = null;
  }
  zeichnen();
  window.scrollTo({ top: 0 });
}

function zuordnenStarten(uuid, titel) {
  zuordnen = { uuid, titel };
  zeichnen();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function kachelGetippt(nr) {
  const jetzt = Date.now();
  if (jetzt - (letzterTipp.get(nr) || 0) < ENTPRELLUNG_MS) return; // Doppeltipp ignorieren
  letzterTipp.set(nr, jetzt);
  setTimeout(zeichnen, ENTPRELLUNG_MS + 50);
  await g.erfasse({ art: "ZIEL", startnummer: nr, zeit_ms: jetzt });
}

async function fahrerGewaehlt(nr) {
  if (!zuordnen) return;
  const bezug = zuordnen.uuid;
  zuordnen = null;
  await g.erfasse({ art: "ZUORDNUNG", bezug_uuid: bezug, startnummer: nr });
}

function statusFahrerGewaehlt(nr) {
  statusFahrer = nr;
  $("status-grund").value = "";
  bestaetigungZuruecksetzen();
  zeichnen();
  $("status-box").scrollIntoView({ block: "nearest" });
}

for (const id of ["btn-status-dnf", "btn-status-dsq", "btn-status-ok"]) {
  $(id).addEventListener("click", async (ev) => {
    if (statusFahrer == null) return;
    const wert = ev.currentTarget.dataset.wert;
    const grund = $("status-grund").value.trim();
    if (wert === "DSQ_VORLAEUFIG" && !grund) return; // Grund ist Pflicht
    if (!bestaetigungAnfordern(`status:${statusFahrer}:${wert}`)) return;
    bestaetigungZuruecksetzen();
    const nr = statusFahrer;
    statusFahrer = null;
    $("status-grund").value = "";
    const daten = { art: "STATUS", startnummer: nr, wert };
    if (wert === "DSQ_VORLAEUFIG") daten.notiz = grund;
    await g.erfasse(daten);
  });
}

$("status-grund").addEventListener("input", () => zeichnen());

$("btn-status-abbrechen").addEventListener("click", () => {
  statusFahrer = null;
  $("status-grund").value = "";
  bestaetigungZuruecksetzen();
  zeichnen();
});

// Reiter der Rennleitung: gewählter Bereich bleibt bis zum nächsten Wechsel
for (const b of document.querySelectorAll("#kopf-rennleitung button")) {
  b.addEventListener("click", () => {
    bereich = b.dataset.bereich;
    zeichnen();
    window.scrollTo({ top: 0 });
  });
}

$("tab-erfassen").addEventListener("click", () => sichtWechseln("erfassen"));
$("tab-rennleitung").addEventListener("click", () => sichtWechseln("rennleitung"));

$("btn-ohne-nummer").addEventListener("click", async () => {
  const jetzt = Date.now(); // bewusst ohne Entprellung: zwei Fahrer können dicht folgen
  await g.erfasse({ art: "ZIEL", zeit_ms: jetzt });
});

$("btn-einlauf-zurueck").addEventListener("click", async () => {
  const e = zustand().eigener;
  if (!e || !bestaetigungAnfordern(`einlauf:${e.uuid}`)) return; // zweiter Tipp bestätigt
  bestaetigungZuruecksetzen();
  await g.erfasse({ art: "STORNO", bezug_uuid: e.uuid, startnummer: e.nr });
});

$("btn-minus").addEventListener("click", () => {
  const e = zustand().eigener;
  if (e) zeitKorrigieren(e, -KORREKTUR_MS);
});
$("btn-plus").addEventListener("click", () => {
  const e = zustand().eigener;
  if (e) zeitKorrigieren(e, KORREKTUR_MS);
});

$("btn-zuordnen-abbrechen").addEventListener("click", () => {
  zuordnen = null;
  zeichnen();
});

g.beiAenderung = zeichnen;
setInterval(erwarteteMarkieren, 5000); // auch offline: Erwartung hängt an der Uhr
await g.init();
zeichnen();
window.geraet = g;
window.geraetBereit = true;
