// Posten an einem Messpunkt (Segmente): wie „Erfassen“ am Ziel, aber jede Kachel zeigt die
// nächste Durchfahrt („Durchfahrt 2/3“) und bleibt bis zur letzten. Korrekturleiste wie am Ziel.
import { Geraet, formatFahrzeit, formatUhrzeit, streckeMarke } from "./geraet.js";

const ENTPRELLUNG_MS = 1000;
const BESTAETIGUNG_MS = 3000;
const KORREKTUR_MS = 1000;
const MAX_KORREKTUR_MS = 5000;
const ENDSTATUS = ["DNS", "DNF", "DSQ"];

const g = new Geraet("posten");
const $ = (id) => document.getElementById(id);
const letzterTipp = new Map();
let bestaetigen = null;
let bestaetigungsTimer = null;
let korrekturHinweis = null;
// „Durchfahrten anzeigen“ (Standard an), gemerkt bis „Gerät zurücksetzen“
const DURCHFAHRTEN = g.schluessel("anzeige_durchfahrten");
// „Alle Fahrer“ – auch Fahrer, die hier als vorbei gelten (z. B. nach einem Fehlklick
// an einem anderen Posten), bleiben antippbar; ein solcher Klick zählt normal
const ALLE = g.schluessel("anzeige_alle");
function alleAn() {
  try {
    return localStorage.getItem(ALLE) === "1";
  } catch {
    return false;
  }
}
function durchfahrtenAn() {
  try {
    return localStorage.getItem(DURCHFAHRTEN) !== "0";
  } catch {
    return true;
  }
}

function korrekturText(ms) {
  return `${ms >= 0 ? "+" : "−"}${(Math.abs(ms) / 1000).toFixed(1).replace(".", ",")} s`;
}

function zustand() {
  const s = g.stand || {};
  const mp = s.messpunkt || null;
  const startBekannt = !!g.stand;
  const gestartet = new Set(s.gestartet || []);
  // gemeinsamer Messpunkt: nur Fahrer der Strecken, die hier vorbeiführen
  const alle = (s.teilnehmer || []).filter((t) => !mp || (mp.strecken || []).includes(t.strecke));
  const nachNr = new Map(alle.map((t) => [t.startnummer, t]));

  // Durchfahrten an diesem Messpunkt: Serverstand plus eigene, noch nicht im Stand enthaltene
  const durch = new Map();
  for (const d of s.durchfahrten || [])
    durch.set(d.uuid, { uuid: d.uuid, zeit_ms: d.zeit_ms, nr: d.startnummer, korrektur: d.korrektur_ms || 0, imStand: true });
  const offen = new Map(); // Startnummer → Anzahl eigener Durchfahrten, die der Stand noch nicht kennt
  const storniert = new Set();
  for (const e of g.nichtImStand()) {
    if (e.art === "DURCHFAHRT") {
      durch.set(e.uuid, { uuid: e.uuid, zeit_ms: e.zeit_ms, nr: e.startnummer, korrektur: 0, imStand: false });
      offen.set(e.startnummer, (offen.get(e.startnummer) || 0) + 1);
    } else if (e.art === "ZEITKORREKTUR") {
      const d = durch.get(e.bezug_uuid);
      if (!d || Math.abs(d.korrektur + e.delta_ms) > MAX_KORREKTUR_MS) continue;
      d.korrektur += e.delta_ms;
      d.zeit_ms += e.delta_ms;
    } else if (e.art === "STORNO") {
      const d = durch.get(e.bezug_uuid);
      storniert.add(e.bezug_uuid);
      if (!d) continue;
      offen.set(d.nr, (offen.get(d.nr) || 0) - 1); // zurückgenommen: Runde wieder offen
      durch.delete(e.bezug_uuid);
    }
  }

  const erwartetVon = (t) => (mp && mp.erwartet ? mp.erwartet[String(t.strecke)] || 1 : 1);
  const jetzt = Date.now();
  const kurzVorher = (nr) => jetzt - (letzterTipp.get(nr) || 0) < ENTPRELLUNG_MS;
  const mitInfo = (t) => {
    const naechste = ((t.naechste_runde || {})[String(mp && mp.id)] || 1) + (offen.get(t.startnummer) || 0);
    const runden = erwartetVon(t);
    return { ...t, naechste: Math.max(1, naechste), runden, aktiv: !startBekannt || gestartet.has(t.startnummer), erledigt: naechste > runden };
  };
  const kacheln = alle
    .map(mitInfo)
    .filter((t) => !ENDSTATUS.includes(t.status) && (!t.erledigt || kurzVorher(t.startnummer) || alleAn()));
  const unterwegs = alle.map(mitInfo).filter((t) => t.aktiv && !t.erledigt && !ENDSTATUS.includes(t.status)).length;

  // letzte eigene Durchfahrt (Leiste); zurückgenommen → leer
  let eigener = null;
  const eigene = g.log.filter((e) => e.art === "DURCHFAHRT").sort((a, b) => b.erstellt_ms - a.erstellt_ms);
  const eigeneStornos = new Set(g.log.filter((e) => e.art === "STORNO").map((e) => e.bezug_uuid));
  if (eigene.length && !eigeneStornos.has(eigene[0].uuid)) {
    const e = eigene[0];
    let d = durch.get(e.uuid);
    if (!d) {
      const korrektur = g.log
        .filter((k) => k.art === "ZEITKORREKTUR" && k.bezug_uuid === e.uuid)
        .reduce((summe, k) => (Math.abs(summe + k.delta_ms) > MAX_KORREKTUR_MS ? summe : summe + k.delta_ms), 0);
      d = { uuid: e.uuid, zeit_ms: e.zeit_ms + korrektur, nr: e.startnummer, korrektur };
    }
    eigener = { ...d, t: nachNr.get(d.nr) };
  }
  return { mp, kacheln, unterwegs, eigener, startBekannt };
}

function kachel(t) {
  const runden = t.runden;
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
  if (t.erledigt && alleAn()) {
    const r = document.createElement("span");
    r.className = "kachel-runde kachel-vorbei";
    r.textContent = "gilt als vorbei";
    b.append(r);
  } else if (runden > 1 && durchfahrtenAn()) {
    const r = document.createElement("span");
    r.className = "kachel-runde";
    r.textContent = `${Math.min(t.naechste, runden)}. von ${runden} Durchfahrten`;
    b.append(r);
  }
  const marke = streckeMarke(g.stand, t);
  if (marke) {
    marke.classList.add("kachel-strecke");
    b.append(marke);
  }
  b.addEventListener("click", () => getippt(t.startnummer));
  return b;
}

function zeichnen() {
  const z = zustand();
  $("posten-titel").textContent = z.mp ? `Zwischenzeit ${z.mp.name}` : "Zwischenzeit";
  $("posten-info").textContent = z.startBekannt ? `${z.unterwegs} noch erwartet` : "";
  $("fahrer").replaceChildren(...z.kacheln.map((t) => kachel(t)));
  const an = durchfahrtenAn();
  $("toggle-durchfahrten").setAttribute("aria-pressed", String(an));
  $("toggle-alle").setAttribute("aria-pressed", String(alleAn()));
  $("durchfahrten-hinweis").hidden = !an || !z.kacheln.some((t) => t.runden > 1);
  const e = z.eigener;
  const text = $("letzter-text");
  $("btn-minus").disabled = $("btn-plus").disabled = $("btn-einlauf-zurueck").disabled = !e;
  const warte = !!e && bestaetigen === `zurueck:${e.uuid}`;
  $("btn-einlauf-zurueck").textContent = warte ? "Wirklich?" : "Rückgängig";
  $("btn-einlauf-zurueck").classList.toggle("bestaetigen", warte);
  if (!e) {
    text.textContent = "Noch keine Durchfahrt erfasst";
    return;
  }
  const wer = `Nr. ${e.nr}${e.t ? " " + e.t.nachname : ""}`;
  const fahrzeit = e.t && e.t.start_ms ? ` · ${formatFahrzeit(e.zeit_ms - e.t.start_ms)}` : "";
  text.textContent = `${wer} · ${formatUhrzeit(e.zeit_ms)}${fahrzeit}`;
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
    h.textContent = "Mehr als ±5 s – bitte der Rennleitung melden";
    text.append(document.createElement("br"), h);
  }
}

async function getippt(nr) {
  const jetzt = Date.now();
  if (jetzt - (letzterTipp.get(nr) || 0) < ENTPRELLUNG_MS) return; // Doppeltipp ignorieren
  letzterTipp.set(nr, jetzt);
  setTimeout(zeichnen, ENTPRELLUNG_MS + 50);
  const mp = (g.stand || {}).messpunkt;
  if (!mp) return;
  korrekturHinweis = null;
  await g.erfasse({ art: "DURCHFAHRT", startnummer: nr, zeit_ms: jetzt, wert: String(mp.id) });
}

async function korrigieren(delta) {
  const e = zustand().eigener;
  if (!e) return;
  if (Math.abs((e.korrektur || 0) + delta) > MAX_KORREKTUR_MS) {
    korrekturHinweis = e.uuid;
    zeichnen();
    return;
  }
  korrekturHinweis = null;
  await g.erfasse({ art: "ZEITKORREKTUR", bezug_uuid: e.uuid, delta_ms: delta, startnummer: e.nr });
}

$("btn-minus").addEventListener("click", () => korrigieren(-KORREKTUR_MS));
$("btn-plus").addEventListener("click", () => korrigieren(KORREKTUR_MS));
$("btn-einlauf-zurueck").addEventListener("click", async () => {
  const e = zustand().eigener;
  if (!e) return;
  const schluessel = `zurueck:${e.uuid}`;
  if (bestaetigen !== schluessel) {
    bestaetigen = schluessel;
    clearTimeout(bestaetigungsTimer);
    bestaetigungsTimer = setTimeout(() => {
      bestaetigen = null;
      zeichnen();
    }, BESTAETIGUNG_MS);
    zeichnen();
    return;
  }
  bestaetigen = null;
  clearTimeout(bestaetigungsTimer);
  await g.erfasse({ art: "STORNO", bezug_uuid: e.uuid, startnummer: e.nr });
});

$("toggle-alle").addEventListener("click", () => {
  try {
    localStorage.setItem(ALLE, alleAn() ? "0" : "1");
  } catch {}
  zeichnen();
});
$("toggle-durchfahrten").addEventListener("click", () => {
  try {
    localStorage.setItem(DURCHFAHRTEN, durchfahrtenAn() ? "0" : "1");
  } catch {}
  zeichnen();
});

g.beiAenderung = zeichnen;
await g.init();
zeichnen();
window.geraet = g;
window.geraetBereit = true;
