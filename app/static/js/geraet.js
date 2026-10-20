// Gemeinsame Gerätelogik für Start- und Zielgerät:
// IndexedDB-Log (zugleich Outbox), Sync-Loop, gecachter Stand, Statusleiste.

const DB_NAME = "rcb-zeitfahren";
const DB_VERSION = 1;
const SYNC_INTERVALL_MS = 5000;
const TIMEOUT_MS = 8000;
const UHR_MESSUNGEN = 5; // Uhrabweichung: kürzeste Laufzeit der letzten 5 Messungen zählt
const UHR_GRENZE_MS = 500; // ab mehr als 0,5 s deutlich warnen

// gesendet: 0 = ausstehend, 1 = vom Server bestätigt, 2 = vom Server abgelehnt
const AUSSTEHEND = 0;
const BESTAETIGT = 1;
const ABGELEHNT = 2;

function anfrage(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function fertig(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("Transaktion abgebrochen"));
  });
}

function oeffneDb() {
  const req = indexedDB.open(DB_NAME, DB_VERSION);
  req.onupgradeneeded = () => {
    const db = req.result;
    db.createObjectStore("log", { keyPath: "uuid" });
    db.createObjectStore("kv");
  };
  return anfrage(req);
}

export function neueUuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const zwei = (n) => String(n).padStart(2, "0");

// Streckenkennzeichen: Name als Text plus Farbe, nur wenn es mehrere Strecken gibt.
export const STRECKEN_FARBEN = 6;
export function streckeMarke(stand, t) {
  const strecken = (stand && stand.strecken) || [];
  if (strecken.length < 2 || !t || t.strecke == null || !strecken[t.strecke]) return null;
  const s = document.createElement("span");
  s.className = `strecke-marke strecke-farbe-${t.strecke % STRECKEN_FARBEN}`;
  s.textContent = strecken[t.strecke].name;
  return s;
}

// Text der Uhrprüfung in der Statusleiste; null = ohne Verbindung/Messung
export function uhrText(abweichungMs) {
  if (abweichungMs === null) return { text: "Uhr ungeprüft", warnung: false };
  const z = Math.round(Math.abs(abweichungMs) / 100);
  const s = `${Math.floor(z / 10)},${z % 10} s`;
  if (Math.abs(abweichungMs) > UHR_GRENZE_MS) return { text: `Uhr weicht ${s} ab – Gerät prüfen`, warnung: true };
  return { text: `Uhr ±${s}`, warnung: false };
}

export function formatUhrzeit(ms, mitZehntel = true) {
  if (ms == null) return "–";
  const d = new Date(ms);
  const basis = `${zwei(d.getHours())}:${zwei(d.getMinutes())}:${zwei(d.getSeconds())}`;
  return mitZehntel ? `${basis},${Math.floor(d.getMilliseconds() / 100)}` : basis;
}

// Fahrzeit auf Zehntel abschneiden
export function formatFahrzeit(ms) {
  if (ms == null || ms < 0) return "–";
  const z = Math.floor(ms / 100);
  const zehntel = z % 10;
  const sek = Math.floor(z / 10) % 60;
  const min = Math.floor(z / 600) % 60;
  const std = Math.floor(z / 36000);
  return std ? `${std}:${zwei(min)}:${zwei(sek)},${zehntel}` : `${min}:${zwei(sek)},${zehntel}`;
}

class HttpFehler extends Error {
  constructor(status, text) {
    super(`HTTP ${status}: ${text}`);
    this.status = status;
    this.text = text || "";
  }
}

const SERVERFELDER = [
  "uuid", "art", "zeit_ms", "startnummer", "bezug_uuid", "wert", "geraet", "erstellt_ms",
  "notiz", "veranstaltung_id", "delta_ms",
];

// Mehrere Geräte im selben Browser: Token, Gerätekennung, Outbox, Stand und
// Einstellungen gelten je „Profil“ = Rolle + Messpunkt (?ort=, aus dem Gerätelink) – für das
// umschaltbare Ziel zusätzlich „-rl“ beim Rennleitungs-Link. Ohne ?ort= ist das Profil die Rolle
// (bisherige Schlüssel, bisherige Links). Ohne Token in der Adresse gilt das zuletzt geöffnete Profil.
export function profilVon(rolle, suche = location.search) {
  const p = new URLSearchParams(suche);
  if (!p.get("token")) {
    try {
      return localStorage.getItem(`rcb_zuletzt_${rolle}`) || rolle;
    } catch {
      return rolle;
    }
  }
  const ort = (p.get("ort") || "").replace(/[^0-9]/g, "");
  const rl = rolle === "ziel" && p.get("sicht") === "rennleitung" ? "-rl" : "";
  return `${rolle}${ort ? `-${ort}` : ""}${rl}`;
}

// Fingerabdruck des Tokens (FNV-1a) – bindet Einträge an Veranstaltung und Zugang, ohne
// den Token selbst in Einträgen oder Exportdateien abzulegen
export function bindungVon(token) {
  let h = 0x811c9dc5;
  for (const c of String(token || "")) h = Math.imul(h ^ c.codePointAt(0), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}

export class Geraet {
  constructor(rolle) {
    this.rolle = rolle;
    this.profil = profilVon(rolle);
    this.log = [];
    this.stand = null;
    this.letzterSync = null;
    this.serverErreichbar = false;
    this.fehlermeldung = null;
    this.beiAenderung = () => {};
    this._syncLaeuft = false;
    this._syncNochmal = false;
    this.uhrMessungen = []; // letzte Messungen der Uhrabweichung {laufzeit, abweichung}
    this.frueher = []; // Nicht übertragene Einträge einer früheren Veranstaltung
    this._bindungVorbereiten();
  }

  // Alle Gerätedaten gehören zu Veranstaltung und Token (Tokens sind je Veranstaltung
  // eindeutig, gelöschte bleiben gesperrt). Öffnet der Link einen anderen Token als den gespeicherten,
  // beginnt das Profil leer: Einstellungen (Startfolge, Anzeige …) weg –
  // schon hier, bevor die Seite sie liest; Log und Stand trennt init().
  _bindungVorbereiten() {
    try {
      const neu = new URLSearchParams(location.search).get("token");
      const alt = localStorage.getItem(`rcb_token_${this.profil}`);
      this.alteBindung = alt ? bindungVon(alt) : null;
      this.wechsel = !!(neu && alt && neu !== alt);
      if (this.wechsel) this._einstellungenLoeschen();
    } catch {
      this.wechsel = false;
    }
  }

  _einstellungenLoeschen() {
    for (const schluessel of Object.keys(localStorage)) {
      if (schluessel.startsWith(`rcb_${this.profil}_`)) localStorage.removeItem(schluessel);
    }
  }

  async init() {
    const params = new URLSearchParams(location.search);
    const tokenSchluessel = `rcb_token_${this.profil}`;
    if (params.get("token")) localStorage.setItem(tokenSchluessel, params.get("token"));
    this.token = localStorage.getItem(tokenSchluessel);
    localStorage.setItem(`rcb_zuletzt_${this.rolle}`, this.profil);

    const idSchluessel = `rcb_geraet_${this.profil}`;
    if (!localStorage.getItem(idSchluessel)) {
      localStorage.setItem(idSchluessel, `${this.rolle}-${neueUuid().slice(0, 4)}`);
    }
    this.geraetId = localStorage.getItem(idSchluessel);

    this.bindung = this.token ? bindungVon(this.token) : null;
    this.db = await oeffneDb();
    this.log = (await anfrage(this.db.transaction("log").objectStore("log").getAll())).filter((e) => this.eigenerEintrag(e));
    this.log.sort((a, b) => a.erstellt_ms - b.erstellt_ms);
    this.stand = (await this.kvLesen("stand")) || null;
    // ohne Token bleibt alles, wie es ist (nur früher abgetrennte Einträge stehen getrennt)
    await this._trennen((e) => !!this.bindung && (e.bindung || (this.wechsel ? this.alteBindung : this.bindung)) !== this.bindung, this.wechsel);
    this.ortZeigen();
    this.sperreZeigen();
    this.letzterSync = (await this.kvLesen("letzter_sync")) || null;
    this.standZeit = (await this.kvLesen("stand_zeit")) || 0;

    setInterval(() => this.zeigeUhr(), 100);
    this.zeigeStatus();
    this._zuruecksetzenVorbereiten();
    if (!this.token) {
      this.zeigeFehler("Kein Zugangstoken. Bitte den Gerätelink mit ?token=… öffnen.");
      this.zeigeUngesendet();
      return;
    }
    window.addEventListener("online", () => this.sync());
    window.addEventListener("offline", () => {
      this.serverErreichbar = false;
      this.zeigeStatus();
    });
    setInterval(() => this.sync(), SYNC_INTERVALL_MS);
    this.sync();
  }

  // Sperrbildschirm „Zeitnahme noch nicht freigegeben“: statt der Bedienung die
  // fehlenden Schritte; schaltet sich mit dem nächsten Stand von selbst frei.
  sperreZeigen() {
    const f = this.stand && this.stand.freigabe;
    const gesperrt = !!f && f.frei === false;
    document.body.classList.toggle("gesperrt", gesperrt);
    const box = document.getElementById("sperre");
    if (!box) return;
    box.hidden = !gesperrt;
    if (!gesperrt) return;
    document.getElementById("sperre-schritte").replaceChildren(
      ...(f.fehlt || []).map((s) => Object.assign(document.createElement("li"), { textContent: s.text })),
    );
    const n = this.ausstehend().length;
    const hinweis = document.getElementById("sperre-ausstehend");
    hinweis.hidden = n === 0;
    hinweis.textContent = `${n} ${n === 1 ? "Eintrag wartet" : "Einträge warten"} auf die Freigabe und ${n === 1 ? "wird" : "werden"} danach gesendet.`;
  }

  // Weiterer Start-/Zielort: Name in der Statusleiste, z. B. „Start · Nord“
  ortZeigen() {
    const ort = this.stand && this.stand.ort;
    const feld = document.querySelector("#statusleiste .rolle");
    if (!feld) return;
    feld.dataset.titel ||= feld.textContent;
    const standard = ["start", "ziel"].includes((ort && ort.name || "").toLowerCase());
    feld.textContent = ort && !standard ? `${feld.dataset.titel} · ${ort.name}` : feld.dataset.titel;
  }

  // Einträge dieses Profils; ältere Einträge ohne Profil gehören zum Profil der Rolle
  eigenerEintrag(e) {
    return e.profil ? e.profil === this.profil : this.profil === this.rolle && String(e.geraet || "").startsWith(`${this.rolle}-`);
  }

  // Schlüssel für Einstellungen im localStorage, z. B. „rcb_start_gewaehlt“ bzw. „rcb_start-5_gewaehlt“
  schluessel(name) {
    return `rcb_${this.profil}_${name}`;
  }

  async kvLesen(schluessel) {
    return anfrage(this.db.transaction("kv").objectStore("kv").get(`${this.profil}:${schluessel}`));
  }

  async kvSchreiben(schluessel, wert) {
    const tx = this.db.transaction("kv", "readwrite");
    tx.objectStore("kv").put(wert, `${this.profil}:${schluessel}`);
    await fertig(tx);
  }

  // Speichert ein Ereignis lokal. Erst nach erfolgreichem Schreiben in IndexedDB zurück.
  async erfasse(daten) {
    const ereignis = {
      uuid: neueUuid(),
      geraet: this.geraetId,
      erstellt_ms: Date.now(),
      ...daten,
      profil: this.profil,
      bindung: this.bindung,
      gesendet: AUSSTEHEND,
    };
    if (this.stand && this.stand.veranstaltung) ereignis.veranstaltung_id = this.stand.veranstaltung.id;
    const tx = this.db.transaction("log", "readwrite");
    tx.objectStore("log").put(ereignis);
    await fertig(tx);
    this.log.push(ereignis);
    this.beiAenderung();
    this.zeigeStatus();
    this.sync();
    return ereignis;
  }

  // Eigene Ereignisse, die im gecachten Serverstand noch nicht enthalten sein können:
  // nicht bestätigt, oder erst nach Beginn des letzten Stand-Abrufs erfasst.
  nichtImStand() {
    return this.log.filter(
      (e) => e.gesendet === AUSSTEHEND || (e.gesendet === BESTAETIGT && e.erstellt_ms >= this.standZeit),
    );
  }

  ausstehend() {
    return this.log.filter((e) => e.gesendet === AUSSTEHEND);
  }

  abgelehnt() {
    return this.log.filter((e) => e.gesendet === ABGELEHNT);
  }

  online() {
    return navigator.onLine && this.serverErreichbar;
  }

  async sync() {
    if (!this.token || this.beendet) return;
    // navigator.onLine === false ist ein verlässliches Offline-Signal (true dagegen nicht).
    // Kein Versuch spart Timeouts; das online-Event stößt den Sync wieder an.
    if (!navigator.onLine) {
      this.serverErreichbar = false;
      this.zeigeStatus();
      return;
    }
    if (this._syncLaeuft) {
      this._syncNochmal = true;
      return;
    }
    this._syncLaeuft = true;
    try {
      do {
        this._syncNochmal = false;
        await this._syncEinmal();
      } while (this._syncNochmal);
    } finally {
      this._syncLaeuft = false;
      this.zeigeStatus();
    }
  }

  async _syncEinmal() {
    try {
      const offen = this.ausstehend();
      if (offen.length) await this._senden(offen);
      const abgerufen = Date.now();
      const antwort = await this._fetch("/api/stand");
      const empfangen = Date.now();
      this.stand = await antwort.json();
      this.ortZeigen();
      this.sperreZeigen();
      this.uhrMessen(abgerufen, empfangen, this.stand.server_ms);
      this.standZeit = abgerufen;
      this.letzterSync = Date.now();
      this.serverErreichbar = true;
      this.zeigeFehler(null);
      await this.kvSchreiben("stand", this.stand);
      await this.kvSchreiben("stand_zeit", this.standZeit);
      await this.kvSchreiben("letzter_sync", this.letzterSync);
      this.beiAenderung();
    } catch (err) {
      this.serverErreichbar = false;
      if (err instanceof HttpFehler && err.status === 410) {
        // gelöscht oder abgeschlossen: aufräumen (nicht übertragene Einträge bleiben immer)
        // Gelöscht → „gibt es nicht mehr“ (nicht übertragene Einträge bleiben zum Export)
        await this.aufraeumen(err.text.includes("gelöscht")
          ? "Diese Veranstaltung gibt es nicht mehr – Daten auf diesem Gerät gelöscht."
          : "Veranstaltung beendet – Daten auf diesem Gerät gelöscht.");
      } else if (err instanceof HttpFehler && (err.status === 401 || err.status === 403)) {
        this.zeigeFehler("Zugangstoken ungültig. Bitte den richtigen Gerätelink öffnen.");
      } else if (err instanceof HttpFehler && err.status === 409) {
        this.zeigeFehler("Keine aktive Veranstaltung auf dem Server.");
      }
    }
  }

  async _senden(ereignisse) {
    try {
      const gesendet = Date.now();
      const antwort = await this._fetch("/api/ereignisse", {
        method: "POST",
        body: JSON.stringify({
          ereignisse: ereignisse.map((e) => Object.fromEntries(SERVERFELDER.filter((f) => e[f] != null).map((f) => [f, e[f]]))),
        }),
      });
      const empfangen = Date.now();
      const { bestaetigt, server_ms } = await antwort.json();
      this.uhrMessen(gesendet, empfangen, server_ms);
      await this._markieren(bestaetigt, BESTAETIGT);
    } catch (err) {
      if (err instanceof HttpFehler && err.status === 423) {
        // Noch nicht freigegeben – Einträge bleiben ausstehend und gehen nach der
        // Freigabe beim nächsten Sync durch (nie löschen)
        let fehlt = [];
        try {
          fehlt = JSON.parse(err.text).detail.fehlt || [];
        } catch {}
        this.stand = { ...(this.stand || {}), freigabe: { frei: false, fehlt } };
        this.sperreZeigen();
        return;
      }
      if (!(err instanceof HttpFehler && err.status === 422)) throw err;
      // Ein ungültiges Ereignis darf die Outbox nicht dauerhaft blockieren: einzeln senden.
      if (ereignisse.length > 1) {
        for (const e of ereignisse) await this._senden([e]);
      } else {
        await this._markieren([ereignisse[0].uuid], ABGELEHNT);
      }
    }
  }

  async _markieren(uuids, wert) {
    const menge = new Set(uuids);
    const tx = this.db.transaction("log", "readwrite");
    const store = tx.objectStore("log");
    for (const e of this.log) {
      if (menge.has(e.uuid)) {
        e.gesendet = wert;
        store.put(e);
      }
    }
    await fertig(tx);
  }

  // Uhrabweichung = Serverzeit − Mitte aus Sende- und Empfangszeitpunkt.
  // Es zählt die Messung mit der kürzesten Laufzeit der letzten UHR_MESSUNGEN. Nur Anzeige:
  // gemessene Zeiten werden nie korrigiert.
  uhrMessen(gesendet, empfangen, serverMs) {
    if (!Number.isFinite(serverMs)) return;
    this.uhrMessungen.push({ laufzeit: empfangen - gesendet, abweichung: Math.round(serverMs - (gesendet + empfangen) / 2) });
    if (this.uhrMessungen.length > UHR_MESSUNGEN) this.uhrMessungen.shift();
  }

  uhrAbweichung() {
    if (!this.uhrMessungen.length) return null;
    return this.uhrMessungen.reduce((a, b) => (b.laufzeit < a.laufzeit ? b : a)).abweichung;
  }

  async _fetch(url, optionen = {}) {
    const abbruch = new AbortController();
    const timer = setTimeout(() => abbruch.abort(), TIMEOUT_MS);
    try {
      const antwort = await fetch(url, {
        ...optionen,
        cache: "no-store",
        signal: abbruch.signal,
        headers: this._kopfzeilen(),
      });
      if (!antwort.ok) throw new HttpFehler(antwort.status, await antwort.text());
      return antwort;
    } finally {
      clearTimeout(timer);
    }
  }

  _kopfzeilen() {
    const kopf = { "Content-Type": "application/json", Authorization: `Bearer ${this.token}`, "X-Geraet": this.geraetId };
    const abweichung = this.uhrAbweichung();
    if (abweichung !== null) kopf["X-Uhr-Abweichung"] = String(abweichung); // letzte Abweichung mitmelden
    return kopf;
  }

  // Einträge einer anderen Veranstaltung abtrennen. Übertragene werden gelöscht (sie
  // liegen auf dem Server), nicht übertragene NIE: sie bleiben als „früher“ getrennt auf dem Gerät,
  // werden nicht gesendet und lassen sich als Datei sichern. alles: auch Stand und Sync-Zeit verwerfen.
  async _trennen(fremd, alles) {
    const alterStand = this.stand;
    const tx = this.db.transaction(["log", "kv"], "readwrite");
    const behalten = [];
    for (const e of this.log) {
      if (!e.frueher && !fremd(e)) {
        if (!e.bindung && this.bindung) {
          e.bindung = this.bindung;
          tx.objectStore("log").put(e);
        }
        behalten.push(e);
      } else if (e.gesendet === BESTAETIGT) {
        tx.objectStore("log").delete(e.uuid);
      } else {
        if (!e.frueher) {
          e.frueher = true;
          e.bindung ||= this.alteBindung || "";
          if (alterStand && alterStand.veranstaltung) e.frueher_veranstaltung = alterStand.veranstaltung;
          if (alterStand && alterStand.ort) e.frueher_ort = alterStand.ort;
          tx.objectStore("log").put(e);
        }
        this.frueher.push(e);
      }
    }
    if (alles) {
      for (const schluessel of ["stand", "stand_zeit", "letzter_sync"]) tx.objectStore("kv").delete(`${this.profil}:${schluessel}`);
    }
    await fertig(tx);
    this.log = behalten;
    if (alles) {
      this.stand = null;
      this.standZeit = 0;
      this.letzterSync = null;
    }
    this.zeigeFrueher();
  }

  zeigeFrueher() {
    const box = document.getElementById("frueher-box");
    if (!box) return;
    const n = this.frueher.length;
    box.hidden = n === 0;
    if (n) {
      document.getElementById("frueher-text").textContent =
        `${n} ${n === 1 ? "Eintrag" : "Einträge"} einer früheren Veranstaltung – als Datei sichern`;
    }
  }

  // Räumt das Gerät auf (Veranstaltung beendet oder „Gerät zurücksetzen“):
  // Teilnehmerliste, Stand, Token und bestätigte Ereignisse werden gelöscht.
  // Nicht übertragene Ereignisse werden NIE automatisch gelöscht.
  async aufraeumen(meldung) {
    this.beendet = true;
    const bestaetigt = this.log.filter((e) => e.gesendet === BESTAETIGT);
    const tx = this.db.transaction(["log", "kv"], "readwrite");
    for (const e of bestaetigt) tx.objectStore("log").delete(e.uuid);
    for (const schluessel of ["stand", "stand_zeit", "letzter_sync"]) {
      tx.objectStore("kv").delete(`${this.profil}:${schluessel}`);
    }
    await fertig(tx);
    for (const schluessel of Object.keys(localStorage)) {
      if (schluessel.startsWith(`rcb_${this.profil}_`) || schluessel === `rcb_token_${this.profil}`) {
        localStorage.removeItem(schluessel);
      }
    }
    this.log = this.log.filter((e) => e.gesendet !== BESTAETIGT);
    this.stand = null;
    this.token = null;
    this.letzterSync = null;
    this.standZeit = 0;
    history.replaceState(null, "", location.pathname); // Token aus der Adresszeile entfernen
    this.zeigeFehler(null);
    const box = document.getElementById("ende-box");
    box.hidden = false;
    document.getElementById("ende-text").textContent = meldung;
    this.zeigeUngesendet();
    this.zeigeStatus();
    this.beiAenderung();
  }

  nichtUebertragen() {
    return this.log.filter((e) => e.gesendet !== BESTAETIGT);
  }

  zeigeUngesendet() {
    const n = this.nichtUebertragen().length;
    const box = document.getElementById("ungesendet-box");
    box.hidden = n === 0;
    if (n) {
      document.getElementById("ende-box").hidden = false;
      document.getElementById("ungesendet-text").textContent =
        `${n} ${n === 1 ? "Eintrag" : "Einträge"} nicht übertragen`;
    }
  }

  // Export der nicht übertragenen Einträge als Datei (oder über „Teilen“ am Handy)
  async exportieren(frueher = false) {
    const quelle = frueher ? this.frueher : this.nichtUebertragen();
    const eintraege = quelle.map(({ bindung, frueher_veranstaltung, frueher_ort, ...e }) => ({ ...e, zeit: e.zeit_ms ? formatUhrzeit(e.zeit_ms) : null }));
    // Admin warnt bei fremder Veranstaltung; frühere Einträge nennen ihre eigene
    const veranstaltung = frueher ? (quelle[0] && quelle[0].frueher_veranstaltung) || null : this.stand ? this.stand.veranstaltung : null;
    const ort = frueher ? (quelle[0] && quelle[0].frueher_ort) || null : this.stand && this.stand.ort ? this.stand.ort : null;
    const inhalt = JSON.stringify({ geraet: this.geraetId, exportiert: new Date().toISOString(), veranstaltung, ort, eintraege }, null, 2);
    const name = `rcb-${this.geraetId}-${frueher ? "fruehere-veranstaltung" : "nicht-uebertragen"}-${new Date().toISOString().slice(0, 10)}.json`;
    const datei = new File([inhalt], name, { type: "application/json" });
    if (navigator.canShare && navigator.canShare({ files: [datei] })) {
      try {
        await navigator.share({ files: [datei], title: "Nicht übertragene Einträge" });
        return;
      } catch (e) {
        if (e && e.name === "AbortError") return;
      }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(datei);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  _zuruecksetzenVorbereiten() {
    const knopf = document.getElementById("btn-zuruecksetzen");
    let timer = null;
    knopf.addEventListener("click", async () => {
      if (!timer) {
        knopf.textContent = "Wirklich zurücksetzen?";
        knopf.classList.add("bestaetigen");
        timer = setTimeout(() => {
          timer = null;
          knopf.textContent = "Gerät zurücksetzen";
          knopf.classList.remove("bestaetigen");
        }, 3000);
        return;
      }
      clearTimeout(timer);
      timer = null;
      knopf.textContent = "Gerät zurücksetzen";
      knopf.classList.remove("bestaetigen");
      await this.aufraeumen("Gerät zurückgesetzt – Daten auf diesem Gerät gelöscht.");
    });
    document.getElementById("btn-exportieren").addEventListener("click", () => this.exportieren());
    const frueher = document.getElementById("btn-frueher-exportieren");
    if (frueher) frueher.addEventListener("click", () => this.exportieren(true));
  }

  zeigeStatus() {
    const netz = document.getElementById("netz");
    const on = this.online();
    netz.textContent = on ? "online" : "offline";
    netz.classList.toggle("ist-online", on);
    netz.classList.toggle("ist-offline", !on);
    const n = this.ausstehend().length;
    const a = this.abgelehnt().length;
    document.getElementById("ausstehend").textContent =
      `${n} ausstehend` + (a ? ` · ${a} abgelehnt` : "");
    document.getElementById("letzter-sync").textContent = this.letzterSync
      ? formatUhrzeit(this.letzterSync, false)
      : "–";
    const pruefung = document.getElementById("uhr-pruefung");
    const { text, warnung } = uhrText(on ? this.uhrAbweichung() : null);
    pruefung.textContent = text;
    pruefung.classList.toggle("uhr-warnung", warnung);
  }

  zeigeUhr() {
    const uhr = document.getElementById("uhr");
    if (uhr) uhr.textContent = formatUhrzeit(Date.now(), false); // laufende Uhr: nur hh:mm:ss
  }

  zeigeFehler(text) {
    this.fehlermeldung = text;
    const el = document.getElementById("fehler");
    el.hidden = !text;
    el.textContent = text || "";
  }
}
