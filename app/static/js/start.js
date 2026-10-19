// Startgerät (KONZEPT 5): nächster Fahrer, START, Überspringen, DNS, Rückgängig, Nummer wählen.
import { Geraet, formatUhrzeit, streckeMarke } from "./geraet.js";

const RUECKGAENGIG_MS = 30000;
const ENTPRELLUNG_MS = 1000;
const DNS_BESTAETIGUNG_MS = 3000;
const ENDSTATUS = ["DNS", "DNF", "DSQ"];

const g = new Geraet("start");
const $ = (id) => document.getElementById(id);

let uebersprungen = JSON.parse(localStorage.getItem(g.schluessel("uebersprungen")) || "[]");
// bestätigte Wahl für den nächsten Start; lokal gespeichert bis START, DNS oder „Wahl aufheben“
// (übersteht Neuladen und Funkloch; „Gerät zurücksetzen“ löscht rcb_<profil>_*, Segmente 44)
const GEWAEHLT = g.schluessel("gewaehlt");
let manuell = (() => {
  try {
    const wert = Number(localStorage.getItem(GEWAEHLT));
    return wert > 0 ? wert : null;
  } catch {
    return null;
  }
})();

function wahlSetzen(nr) {
  manuell = nr;
  try {
    if (nr == null) localStorage.removeItem(GEWAEHLT);
    else localStorage.setItem(GEWAEHLT, String(nr));
  } catch {}
}
let gewaehlt = null; // in „Nummer wählen“ angetippt, wartet auf Bestätigung
// Anzeige-Umschalter (je Gerät gemerkt; „Gerät zurücksetzen“ löscht rcb_start_* → Standard)
const ANZEIGE = {
  nummer: { schluessel: g.schluessel("anzeige_nummer"), standard: true },
  warteliste: { schluessel: g.schluessel("anzeige_warteliste"), standard: false },
};

function anzeigeAn(name) {
  try {
    const wert = localStorage.getItem(ANZEIGE[name].schluessel);
    return wert == null ? ANZEIGE[name].standard : wert === "1";
  } catch {
    return ANZEIGE[name].standard;
  }
}

function anzeigeSetzen(name, an) {
  try {
    localStorage.setItem(ANZEIGE[name].schluessel, an ? "1" : "0");
  } catch {}
}
let letzterDruck = 0;
let dnsTimer = null;
let dnsAufheben = null; // Startnummer, deren DNS auf den zweiten Tipp wartet (AUSBAU A1)
let dnsAufhebenTimer = null;

function speichereUebersprungen() {
  localStorage.setItem(g.schluessel("uebersprungen"), JSON.stringify(uebersprungen));
}

// Stand vom Server plus eigene, ggf. noch nicht synchronisierte Ereignisse.
function zustand() {
  const teilnehmer = (g.stand && g.stand.teilnehmer) || [];
  const gestartet = new Set((g.stand && g.stand.gestartet) || []);
  const status = {};
  for (const t of teilnehmer) if (ENDSTATUS.includes(t.status)) status[t.startnummer] = t.status;

  const log = g.log;
  const nachUuid = new Map(log.map((e) => [e.uuid, e]));
  const storniert = new Set(log.filter((e) => e.art === "STORNO").map((e) => e.bezug_uuid));
  for (const e of log) {
    if (e.art !== "STORNO") continue;
    const ziel = nachUuid.get(e.bezug_uuid);
    if (ziel && ziel.art === "START") gestartet.delete(ziel.startnummer);
  }
  let letzterStart = null;
  for (const e of log) {
    if (e.art === "START" && !storniert.has(e.uuid)) {
      gestartet.add(e.startnummer);
      letzterStart = e;
    } else if (e.art === "STATUS") {
      if (e.wert === "OK") delete status[e.startnummer];
      else status[e.startnummer] = e.wert;
    }
  }

  const offen = teilnehmer.filter((t) => !gestartet.has(t.startnummer) && !status[t.startnummer]);
  // DNS (nicht gestartet) lässt sich am Start aufheben; DNF/DSQ nicht
  const dns = teilnehmer.filter((t) => !gestartet.has(t.startnummer) && status[t.startnummer] === "DNS");
  const nachNr = new Map(offen.map((t) => [t.startnummer, t]));
  const warteliste = [
    ...offen.filter((t) => !uebersprungen.includes(t.startnummer)),
    ...uebersprungen.map((nr) => nachNr.get(nr)).filter(Boolean),
  ];
  // gewählter Fahrer inzwischen gestartet oder mit Endstatus: Wahl verwerfen (erst wenn der Stand bekannt ist)
  if (manuell != null && teilnehmer.length && !nachNr.has(manuell)) wahlSetzen(null);
  const naechster = (manuell != null && nachNr.get(manuell)) || warteliste[0] || null;
  return { teilnehmer, offen, dns, warteliste, naechster, letzterStart, anzahlGestartet: gestartet.size };
}

function rueckgaengigMoeglich(letzterStart) {
  return letzterStart && Date.now() - letzterStart.zeit_ms <= RUECKGAENGIG_MS;
}

function zeichnen() {
  const z = zustand();
  const n = z.naechster;
  const fertig = !n && z.teilnehmer.length > 0;
  $("alle-gestartet").hidden = !fertig;
  $("naechster-box").hidden = fertig;
  if (n) {
    $("naechster-nr").textContent = String(n.startnummer);
    $("naechster-name").textContent = `${n.vorname} ${n.nachname}`;
    const marke = streckeMarke(g.stand, n);
    $("naechster-strecke").replaceChildren(...(marke ? [marke] : []));
  } else {
    $("naechster-nr").textContent = "–";
    $("naechster-name").textContent = z.teilnehmer.length ? "" : "Keine Teilnehmerliste";
    $("naechster-strecke").replaceChildren();
  }
  $("btn-start").disabled = !n;
  $("btn-ueberspringen").disabled = !n || z.warteliste.length < 2;
  $("btn-dns").disabled = !n;

  const rueck = $("btn-rueckgaengig");
  if (rueckgaengigMoeglich(z.letzterStart)) {
    const rest = Math.ceil((RUECKGAENGIG_MS - (Date.now() - z.letzterStart.zeit_ms)) / 1000);
    rueck.disabled = false;
    rueck.textContent = `Rückgängig Nr. ${z.letzterStart.startnummer} (${rest} s)`;
  } else {
    rueck.disabled = true;
    rueck.textContent = "Rückgängig";
  }
  $("letzter-start").textContent = z.letzterStart
    ? `Zuletzt gestartet: Nr. ${z.letzterStart.startnummer} um ${formatUhrzeit(z.letzterStart.zeit_ms)}`
    : "";
  $("fortschritt").textContent = z.teilnehmer.length
    ? `Gestartet: ${z.anzahlGestartet} von ${z.teilnehmer.length}`
    : "";

  const liste = $("warteliste");
  liste.replaceChildren(
    ...z.warteliste.map((t) => {
      const li = document.createElement("li");
      li.textContent = `Nr. ${t.startnummer} – ${t.vorname} ${t.nachname}`;
      const marke = streckeMarke(g.stand, t);
      if (marke) li.append(" ", marke);
      if (uebersprungen.includes(t.startnummer)) li.classList.add("uebersprungen");
      return li;
    }),
  );

  const nummerAn = anzeigeAn("nummer");
  const wartelisteAn = anzeigeAn("warteliste");
  $("toggle-nummer").setAttribute("aria-pressed", String(nummerAn));
  $("toggle-warteliste").setAttribute("aria-pressed", String(wartelisteAn));
  // Endzustand mit DNS-Fahrern (Segmente 35): deren Kacheln bleiben sichtbar – auch wenn „Nummer
  // wählen“ aus ist –, damit sich DNS weiter aufheben lässt (online wie offline)
  const dnsImEnde = fertig && z.dns.length > 0;
  $("dns-hinweis").hidden = !dnsImEnde;
  if (dnsImEnde) {
    $("dns-hinweis").textContent = z.dns.length === 1
      ? "1 Fahrer mit DNS – zum Aufheben unten die Kachel zweimal antippen."
      : `${z.dns.length} Fahrer mit DNS – zum Aufheben unten die Kachel zweimal antippen.`;
  }
  $("auswahl").hidden = !nummerAn && !dnsImEnde;
  $("warteliste-box").hidden = !wartelisteAn;
  if (nummerAn || dnsImEnde) zeichneAuswahl(z);
  const istGewaehlt = manuell != null && n != null && n.startnummer === manuell;
  $("naechster-box").classList.toggle("gewaehlt", istGewaehlt);
  $("btn-wahl-aufheben").hidden = !istGewaehlt;
  const bestaetigung = gewaehlt != null ? z.offen.find((t) => t.startnummer === gewaehlt) : null;
  $("auswahl-bestaetigung").hidden = !bestaetigung;
  if (bestaetigung) $("auswahl-text").textContent = `Nr. ${bestaetigung.startnummer} – ${bestaetigung.vorname} ${bestaetigung.nachname} als Nächsten starten?`;
}

function zeichneAuswahl(z) {
  const dnsNr = new Set(z.dns.map((t) => t.startnummer));
  const alle = [...z.offen, ...z.dns].sort((a, b) => a.startnummer - b.startnummer);
  $("auswahl-liste").replaceChildren(
    ...alle.map((t) => {
      const b = document.createElement("button");
      const istDns = dnsNr.has(t.startnummer);
      b.className = "kachel" + (istDns ? " dns" : "");
      b.dataset.nr = t.startnummer;
      b.title = `${t.vorname} ${t.nachname}`;
      if (istDns) {
        // grau an ihrem Platz; erster Tipp fragt, zweiter innerhalb von 3 s sendet STATUS OK
        const warte = dnsAufheben === t.startnummer;
        b.innerHTML = `<span class="kachel-nr">${t.startnummer}</span><span class="kachel-status">${warte ? "DNS aufheben?" : "DNS"}</span>`;
        b.classList.toggle("bestaetigen", warte);
        b.addEventListener("click", () => dnsAufhebenGetippt(t.startnummer));
      } else {
        b.textContent = `${t.startnummer}`;
        b.addEventListener("click", () => {
          gewaehlt = t.startnummer; // erst nach Bestätigung als Nächster
          zeichnen();
        });
      }
      return b;
    }),
  );
}

async function dnsAufhebenGetippt(nr) {
  clearTimeout(dnsAufhebenTimer);
  if (dnsAufheben !== nr) {
    dnsAufheben = nr;
    dnsAufhebenTimer = setTimeout(() => {
      dnsAufheben = null;
      zeichnen();
    }, DNS_BESTAETIGUNG_MS);
    zeichnen();
    return;
  }
  dnsAufheben = null;
  await g.erfasse({ art: "STATUS", startnummer: nr, wert: "OK" });
}

function dnsZuruecksetzen() {
  clearTimeout(dnsTimer);
  dnsTimer = null;
  $("btn-dns").textContent = "DNS";
  $("btn-dns").classList.remove("bestaetigen");
}

$("btn-start").addEventListener("click", async () => {
  const jetzt = Date.now();
  if (jetzt - letzterDruck < ENTPRELLUNG_MS) return;
  letzterDruck = jetzt;
  const n = zustand().naechster;
  if (!n) return;
  wahlSetzen(null);
  dnsZuruecksetzen();
  await g.erfasse({ art: "START", startnummer: n.startnummer, zeit_ms: jetzt });
});

$("btn-ueberspringen").addEventListener("click", () => {
  const n = zustand().naechster;
  if (!n) return;
  uebersprungen = uebersprungen.filter((nr) => nr !== n.startnummer);
  uebersprungen.push(n.startnummer);
  wahlSetzen(null);
  speichereUebersprungen();
  zeichnen();
});

$("btn-dns").addEventListener("click", async () => {
  const n = zustand().naechster;
  if (!n) return;
  if (!dnsTimer) {
    $("btn-dns").textContent = `Wirklich DNS für Nr. ${n.startnummer}?`;
    $("btn-dns").classList.add("bestaetigen");
    dnsTimer = setTimeout(dnsZuruecksetzen, DNS_BESTAETIGUNG_MS);
    return;
  }
  dnsZuruecksetzen();
  wahlSetzen(null);
  await g.erfasse({ art: "STATUS", startnummer: n.startnummer, wert: "DNS" });
});

$("btn-rueckgaengig").addEventListener("click", async () => {
  const { letzterStart } = zustand();
  if (!rueckgaengigMoeglich(letzterStart)) return;
  await g.erfasse({ art: "STORNO", bezug_uuid: letzterStart.uuid, startnummer: letzterStart.startnummer });
});

$("btn-auswahl-bestaetigen").addEventListener("click", () => {
  wahlSetzen(gewaehlt);
  gewaehlt = null;
  zeichnen();
});
$("btn-wahl-aufheben").addEventListener("click", () => {
  wahlSetzen(null);
  zeichnen();
});
$("btn-auswahl-abbrechen").addEventListener("click", () => {
  gewaehlt = null;
  zeichnen();
});
$("toggle-nummer").addEventListener("click", () => {
  anzeigeSetzen("nummer", !anzeigeAn("nummer"));
  if (!anzeigeAn("nummer")) gewaehlt = null;
  zeichnen();
});
$("toggle-warteliste").addEventListener("click", () => {
  anzeigeSetzen("warteliste", !anzeigeAn("warteliste"));
  zeichnen();
});

g.beiAenderung = zeichnen;
await g.init();
zeichnen();
setInterval(zeichnen, 1000);
window.geraet = g;
window.geraetBereit = true;
