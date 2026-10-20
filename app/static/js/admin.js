// Admin: kleine Helfer ohne Build-Schritt (auf allen Admin-Seiten geladen).
(function () {
  // Tabellen: ganze Zeile öffnet das Objekt (Links und Buttons in der Zeile behalten ihr Verhalten)
  document.querySelectorAll("tr.row[data-href]").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a, button, input, select, label, summary")) return;
      location.href = tr.dataset.href;
    });
  });
  // Umkehrbare Aktionen: Bestätigungsdialog (data-bestaetigen="Frage", data-bestaetigen-knopf="Aktion")
  // (delegiert: gilt auch für Formulare, die die Live-Aktualisierung neu einsetzt)
  const dialog = document.getElementById("bestaetigung-dialog");
  document.addEventListener("submit", (ev) => {
    const form = ev.target.closest && ev.target.closest("form[data-bestaetigen]");
    if (!form) return;
    // nur unter einer Bedingung nachfragen, z. B. „Ersetzen“ gewählt
    if (form.dataset.bestaetigenWenn && !form.querySelector(form.dataset.bestaetigenWenn)) return;
    {
      if (!dialog || form.dataset.bestaetigt === "1") return;
      ev.preventDefault();
      const absender = ev.submitter;
      document.getElementById("bestaetigung-text").textContent = form.dataset.bestaetigen;
      document.getElementById("dialog-ok").textContent = form.dataset.bestaetigenKnopf || "Bestätigen";
      dialog.returnValue = "";
      dialog.onclose = () => {
        if (dialog.returnValue !== "ja") return;
        form.dataset.bestaetigt = "1";
        const feld = form.querySelector("input[name=bestaetigt]");
        if (feld) feld.value = "1";
        form.requestSubmit(absender || undefined);
      };
      dialog.showModal();
    }
  });
  // Strecke mit Klassen entfernen: Dialog „Die Klassen von … übernimmt“
  const entfernenDialog = document.getElementById("entfernen-dialog");
  if (entfernenDialog) {
    document.getElementById("entfernen-oeffnen").addEventListener("click", () => entfernenDialog.showModal());
    document.getElementById("entfernen-abbrechen").addEventListener("click", () => entfernenDialog.close());
  }
  // Strecke aus Datei laden: beim Ersetzen ist kein neuer Name nötig
  const ladenModus = document.getElementById("laden-modus");
  if (ladenModus) {
    const nameFeld = document.getElementById("laden-name-feld");
    const umschalten = () => {
      const neu = document.getElementById("modus-neu").checked;
      nameFeld.hidden = !neu;
      document.getElementById("laden-name").required = neu;
    };
    ladenModus.addEventListener("change", umschalten);
    umschalten();
  }
  // „Ergebnistabelle“: Auswahl der Strecke zeigt deren Tabelle, nichts geht verloren
  const spaltenWahl = document.getElementById("spalten-strecke");
  if (spaltenWahl) {
    const zeigen = () => document.querySelectorAll(".spalten-strecke").forEach((t) => {
      t.hidden = t.dataset.strecke !== spaltenWahl.value;
    });
    spaltenWahl.addEventListener("change", zeigen);
    zeigen();
  }
  // Zielmonitor gespeichert: offene Vorschau im selben Browser lädt sofort neu
  if (document.getElementById("zielmonitor-gespeichert")) {
    try {
      new BroadcastChannel("rcb-zielmonitor").postMessage("gespeichert");
    } catch (e) { /* Vorschau übernimmt es beim nächsten Abruf */ }
  }
  // Link kopieren
  document.addEventListener("click", async (ev) => {
    const knopf = ev.target.closest && ev.target.closest("button.kopieren[data-text]");
    if (!knopf) return;
    const alt = knopf.dataset.alt || (knopf.dataset.alt = knopf.textContent);
    try {
      await navigator.clipboard.writeText(knopf.dataset.text);
      knopf.textContent = "Kopiert";
    } catch {
      knopf.textContent = "Kopieren nicht möglich";
    }
    setTimeout(() => (knopf.textContent = alt), 1500);
  });
  // Übersicht und Geräte aktualisieren sich selbst: alle 10 s die Seite im
  // Hintergrund holen und die Bereiche mit data-live austauschen – außer, dort ist gerade etwas
  // geöffnet, fokussiert oder geändert. Im Hintergrund-Tab Pause, beim Zurückkehren sofort.
  const LIVE_MS = 10000;
  if (document.querySelector("[data-live]")) {
    const inBenutzung = (b) =>
      (b.contains(document.activeElement) && document.activeElement !== document.body) ||
      b.querySelector("details[open], dialog[open]") ||
      [...b.querySelectorAll("input, select, textarea")].some((e) =>
        e.type === "checkbox" || e.type === "radio" ? e.checked !== e.defaultChecked
          : e.tagName === "SELECT" ? [...e.options].some((o) => o.selected !== o.defaultSelected)
          : e.value !== e.defaultValue);
    let laeuft = false;
    const holen = async () => {
      if (document.hidden || laeuft) return;
      laeuft = true;
      try {
        const antwort = await fetch(location.href, { credentials: "same-origin", headers: { "X-Live": "1" } });
        if (!antwort.ok || antwort.redirected) return;
        const neu = new DOMParser().parseFromString(await antwort.text(), "text/html");
        for (const b of document.querySelectorAll("[data-live]")) {
          const ersatz = neu.getElementById(b.id);
          if (ersatz && !inBenutzung(b) && b.innerHTML !== ersatz.innerHTML) b.innerHTML = ersatz.innerHTML;
        }
        const stand = document.getElementById("live-stand");
        const neuStand = neu.getElementById("live-stand");
        if (stand && neuStand) stand.textContent = neuStand.textContent;
      } catch { /* nächster Versuch in 10 s */ } finally {
        laeuft = false;
      }
    };
    let timer = setInterval(holen, LIVE_MS);
    document.addEventListener("visibilitychange", () => {
      clearInterval(timer);
      if (document.hidden) return;
      holen();
      timer = setInterval(holen, LIVE_MS);
    });
    window.liveAktualisieren = holen; // für Tests
  }
  // Filter: Auswahl ändert die Liste sofort
  document.querySelectorAll("select.auto-senden").forEach((sel) => {
    sel.addEventListener("change", () => sel.form.requestSubmit());
  });

  // Klassen & Wertung: nur Wertungssätze des gewählten Klassensatzes anbieten
  const ks = document.getElementById("wahl-klassensatz");
  const ws = document.getElementById("wahl-wertungssatz");
  if (ks && ws) {
    ks.addEventListener("change", () => {
      let zurueckgesetzt = false;
      for (const opt of ws.options) {
        if (!opt.dataset.klassensatz) continue;
        const passt = opt.dataset.klassensatz === ks.value;
        opt.hidden = !passt;
        opt.disabled = !passt;
        if (!passt && opt.selected) {
          ws.value = "";
          zurueckgesetzt = true;
        }
      }
      document.getElementById("wertung-zurueckgesetzt").hidden = !zurueckgesetzt;
    });
  }

  // Wertungs-Editor: je Zeile nur Klassen ohne Eintrag (+ die eigene) anbieten
  const stufen = document.getElementById("stufen");
  const satzWahl = document.getElementById("stufen-klassensatz");
  const datenEl = document.getElementById("klassen-je-satz");
  if (stufen && satzWahl && datenEl) {
    const klassenJeSatz = JSON.parse(datenEl.textContent);
    const anzahl = document.querySelector('#wertungssatz input[name="anzahl"]');
    const neu = document.getElementById("zeile-neu");
    const hinweis = document.getElementById("alle-klassen");
    const zeilen = () => [...stufen.tBodies[0].rows];
    const aktiv = (tr) => !tr.querySelector(".entfernen").checked;

    function aktualisieren() {
      const klassen = klassenJeSatz[satzWahl.value] || [];
      const benutzt = new Set(
        zeilen().filter(aktiv).map((tr) => tr.querySelector(".klassenwahl").value.toLowerCase()).filter(Boolean)
      );
      for (const tr of zeilen()) {
        const sel = tr.querySelector(".klassenwahl");
        const eigene = sel.value;
        const optionen = klassen.filter((k) => !benutzt.has(k.toLowerCase()) || k.toLowerCase() === eigene.toLowerCase());
        if (eigene && !klassen.some((k) => k.toLowerCase() === eigene.toLowerCase())) optionen.unshift(eigene);
        sel.replaceChildren(new Option("– Klasse wählen –", ""), ...optionen.map((k) => new Option(k, k)));
        sel.value = eigene;
        sel.disabled = optionen.length === 0;
      }
      const alle = klassen.every((k) => benutzt.has(k.toLowerCase()));
      neu.disabled = alle;
      hinweis.hidden = !alle;
    }

    neu.addEventListener("click", () => {
      const i = Number(anzahl.value);
      const tr = stufen.tBodies[0].insertRow();
      tr.innerHTML =
        `<td><select name="klasse_${i}" class="klassenwahl eingabe"></select></td>` +
        ["strecke_km", "gold", "silber", "bronze"].map((f) => `<td><input name="${f}_${i}" class="eingabe eingabe-schmal mono"></td>`).join("") +
        `<td><input type="checkbox" name="entfernen_${i}" value="1" class="entfernen"></td>`;
      anzahl.value = String(i + 1);
      aktualisieren();
      tr.querySelector(".klassenwahl").focus();
    });
    stufen.addEventListener("change", aktualisieren);
    satzWahl.addEventListener("change", aktualisieren);
    aktualisieren();
  }

  // Strecken & Zuordnung: Strecke hinzufügen, Namen live in die Auswahl übernehmen
  const bereich = document.getElementById("strecken-bereich");
  if (bereich) {
    const tbody = document.querySelector("#strecken-tabelle tbody");
    const anzahl = bereich.querySelector('input[name="strecken_anzahl"]');
    const wahlen = () => [...bereich.querySelectorAll("select.strecke-wahl")];
    const FARBEN = 6;

    function optionenAktualisieren() {
      for (const tr of tbody.rows) {
        const i = tr.dataset.index;
        const name = tr.querySelector(".strecke-name").value.trim() || `Strecke ${Number(i) + 1}`;
        const weg = tr.querySelector(".strecke-entfernen")?.checked;
        tr.querySelector(".strecke-marke").textContent = name;
        for (const sel of wahlen()) {
          let opt = [...sel.options].find((o) => o.value === i);
          if (!opt) {
            opt = new Option(name, i);
            sel.add(opt);
          }
          opt.textContent = weg ? `${name} (wird entfernt)` : name;
        }
      }
    }

    const neu = document.getElementById("strecke-neu");
    if (neu) {
      neu.addEventListener("click", () => {
        const i = Number(anzahl.value);
        const tr = tbody.insertRow();
        tr.dataset.index = String(i);
        tr.innerHTML =
          `<td><span class="strecke-marke strecke-farbe-${i % FARBEN}"></span></td>` +
          `<td><input type="hidden" name="strecke_id_${i}" value=""><input name="strecke_name_${i}" class="eingabe strecke-name" maxlength="100" value="Strecke ${i + 1}" required></td>` +
          `<td><input name="strecke_laenge_${i}" inputmode="decimal" placeholder="z. B. 20,0" class="eingabe eingabe-schmal mono"></td>` +
          `<td class="strecke-art hint">einfach – Zwischenzeiten nach dem Speichern</td>` +
          `<td><input type="checkbox" name="strecke_entfernen_${i}" value="1" class="strecke-entfernen"></td>`;
        anzahl.value = String(i + 1);
        optionenAktualisieren();
        tr.querySelector(".strecke-name").select();
      });
    }
    tbody.addEventListener("input", optionenAktualisieren);
    tbody.addEventListener("change", optionenAktualisieren);

    // anderer Klassen-/Wertungssatz: Strecken werden beim Speichern neu vorgeschlagen
    const ksWahl = document.getElementById("wahl-klassensatz");
    const wsWahl = document.getElementById("wahl-wertungssatz");
    const start = [ksWahl && ksWahl.value, wsWahl && wsWahl.value].join("|");
    const satzGeaendert = () => {
      const geaendert = [ksWahl && ksWahl.value, wsWahl && wsWahl.value].join("|") !== start;
      document.getElementById("strecken-neu-hinweis").hidden = !geaendert;
      bereich.querySelectorAll(".tbl-box, #strecke-neu").forEach((el) => el.classList.toggle("ausgegraut", geaendert));
    };
    if (ksWahl && !ksWahl.disabled) ksWahl.addEventListener("change", () => setTimeout(satzGeaendert));
    if (wsWahl) wsWahl.addEventListener("change", satzGeaendert);
  }
  // Zeilen-Editoren: Messpunkte (Seite „Messpunkte“) und
  // Auswertungsabschnitte (Seite der Strecke). Neue Zeilen tragen die Strecke aus data-strecke.
  function neueZeile(tabelle, anzahlName, html) {
    const anzahl = document.querySelector(`input[name="${anzahlName}"]`);
    const j = Number(anzahl.value);
    const body = document.querySelector(`#${tabelle} tbody`);
    body.querySelector(".leer-zeile")?.remove();
    const vorige = body.rows.length ? body.rows[body.rows.length - 1] : null;
    const tr = body.insertRow();
    tr.dataset.index = String(j);
    tr.innerHTML = html(j, document.getElementById(tabelle).dataset.strecke || "0");
    anzahl.value = String(j + 1);
    return { tr, vorige, j };
  }
  // Seite „Messpunkte“: neue Zeile mit der gewählten Art (Start, Zwischenzeit, Ziel)
  document.getElementById("messpunkt-neu")?.addEventListener("click", () => {
    const artWahl = document.getElementById("ort-neu-art");
    const art = artWahl ? artWahl.value : "punkt";
    const artText = artWahl ? artWahl.selectedOptions[0].textContent : "Zwischenzeit";
    const { tr, j } = neueZeile("messpunkte", "mp_anzahl", (j) =>
      `<td class="ort-art"><input type="hidden" name="mp_art_${j}" value="${art}">${artText}</td>` +
      `<td><input type="hidden" name="mp_id_${j}" value=""><input name="mp_name_${j}" maxlength="100" class="eingabe mp-name" required></td>` +
      `<td>–</td><td class="mp-verbunden"><span class="badge">nicht verbunden</span></td>` +
      `<td><input type="checkbox" name="mp_entfernen_${j}" value="1"></td>`);
    tr.dataset.art = art;
    tr.querySelector(`input[name="mp_name_${j}"]`).focus();
  });
  // Verlauf als Punktfolge: Start · Punkte und Rundenblöcke · Ziel. Daraus abgeleitet
  // die Segmente (Name, Länge, anzeigen, Vergleich), die Abfolge und die Durchfahrten je Messpunkt.
  const vStart = document.getElementById("verlauf-start");
  if (vStart) {
    const daten = JSON.parse(vStart.textContent);
    const fest = daten.eingefroren;
    let folge = daten.punkte.map((x) => (typeof x === "string" ? x : { block: x.block, wdh: x.wdh, punkte: [...x.punkte] }));
    const werte = new Map(); // Schlüssel des Segments → {name, km, anzeigen, vergleich}
    const liste = document.getElementById("punktfolge");
    const tabelle = document.querySelector("#segmente-tabelle tbody");
    const feld = document.getElementById("verlauf-daten");
    const el = (tag, attrs = {}, ...kinder) => {
      const e = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "text") e.textContent = v;
        else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v === true ? "" : v);
      }
      e.append(...kinder);
      return e;
    };
    const geaendert = () => document.getElementById("strecke-form")?.dispatchEvent(new Event("editor-geaendert"));

    // abgeleitete Segmente in Fahrreihenfolge, wie segmente.teile_aus_verlauf
    function segmente() {
      const segs = [];
      let vorher = "Start";
      folge.forEach((x, t) => {
        if (typeof x === "string") {
          segs.push({ teil: t, j: 0, von: vorher, nach: x, block: null, wdh: 1 });
          vorher = x;
        } else {
          x.punkte.forEach((p, j) => {
            segs.push({ teil: t, j, von: vorher, nach: p, block: x.block, wdh: Number(x.wdh) || 1 });
            vorher = p;
          });
        }
      });
      if (vorher !== "Ziel") segs.push({ teil: folge.length, j: 0, von: vorher, nach: "Ziel", block: null, wdh: 1 });
      const gesehen = {};
      for (const s of segs) {
        const basis = `${s.block ?? ""}|${s.von}|${s.nach}`;
        gesehen[basis] = (gesehen[basis] || 0) + 1;
        s.key = `${basis}|${gesehen[basis]}`;
      }
      return segs;
    }
    segmente().forEach((s, k) => werte.set(s.key, daten.segmente[k] || { name: "", km: "", anzeigen: true, vergleich: false }));

    function punktWahl(wert, label, onchange) {
      const sel = el("select", { class: "eingabe pf-wahl", "aria-label": label, disabled: fest });
      const namen = daten.messpunkte.includes(wert) || !wert ? daten.messpunkte : [wert, ...daten.messpunkte];
      if (!wert) sel.append(el("option", { value: "", text: "– Zwischenzeit wählen –" }));
      for (const n of namen) sel.append(el("option", { value: n, text: n, selected: n === wert }));
      sel.addEventListener("change", () => onchange(sel.value));
      return sel;
    }
    function knoepfe(arr, i, label) {
      if (fest) return el("span");
      const box = el("span", { class: "pf-knoepfe" });
      const knopf = (text, titel, aktion, aus) => {
        const b = el("button", { type: "button", class: "btn btn-quiet btn-sm", title: titel, "aria-label": `${label}: ${titel}`, text, disabled: aus });
        b.addEventListener("click", () => { aktion(); neu(); geaendert(); });
        return b;
      };
      box.append(
        knopf("↑", "nach oben", () => arr.splice(i - 1, 0, arr.splice(i, 1)[0]), i === 0),
        knopf("↓", "nach unten", () => arr.splice(i + 1, 0, arr.splice(i, 1)[0]), i === arr.length - 1),
        knopf("✕", "entfernen", () => arr.splice(i, 1), false),
      );
      return box;
    }
    // Start- und Zielort: Name im ersten bzw. letzten Punkt der Folge
    const ortName = (id, standard) => document.getElementById(id)?.selectedOptions[0]?.textContent || standard;
    for (const id of ["s-startort", "s-zielort"]) document.getElementById(id)?.addEventListener("change", () => neu());
    function zeichnen() {
      liste.replaceChildren(el("li", { class: "pf-fest", text: ortName("s-startort", "Start") }));
      folge.forEach((x, i) => {
        if (typeof x === "string") {
          liste.append(el("li", { class: "pf-punkt", "data-index": i },
            punktWahl(x, `Punkt ${i + 1}`, (w) => { folge[i] = w; neu(); geaendert(); }), knoepfe(folge, i, `Punkt ${i + 1}`)));
          return;
        }
        const innen = el("ol", { class: "pf-innen" });
        x.punkte.forEach((p, j) => innen.append(el("li", { class: "pf-punkt" },
          punktWahl(p, `${x.block || "Rundenblock"}: Punkt ${j + 1}`, (w) => { x.punkte[j] = w; neu(); geaendert(); }),
          knoepfe(x.punkte, j, `${x.block || "Rundenblock"}: Punkt ${j + 1}`))));
        const name = el("input", { class: "eingabe pf-block-name", value: x.block, maxlength: 100, "aria-label": "Name des Rundenblocks", readonly: fest });
        name.addEventListener("change", () => { x.block = name.value.trim(); neu(); });
        const wdh = el("input", { class: "eingabe eingabe-schmal mono pf-wdh", value: x.wdh, inputmode: "numeric", "aria-label": `Wiederholungen (${x.block || "Rundenblock"})`, readonly: fest });
        wdh.addEventListener("change", () => { x.wdh = Number(wdh.value) || 1; neu(); });
        const kopf = el("div", { class: "pf-block-kopf" }, el("span", { class: "badge", text: "Rundenblock" }), name,
          el("label", { class: "pf-wdh-label" }, "×", wdh), knoepfe(folge, i, `Rundenblock ${x.block}`));
        const li = el("li", { class: "pf-block", "data-index": i }, kopf, innen);
        if (!fest) {
          const plus = el("button", { type: "button", class: "btn btn-secondary btn-sm pf-block-punkt", text: "Punkt im Block hinzufügen" });
          plus.addEventListener("click", () => { x.punkte.push(""); neu(); geaendert(); });
          li.append(plus);
        }
        liste.append(li);
      });
      liste.append(el("li", { class: "pf-fest", text: ortName("s-zielort", "Ziel") }));
    }
    function segmentTabelle() {
      const segs = segmente();
      tabelle.replaceChildren();
      segs.forEach((s, k) => {
        const w = werte.get(s.key) || { name: "", km: "", anzeigen: true, vergleich: false };
        werte.set(s.key, w);
        const titel = `${s.von || "?"} → ${s.nach || "?"}`;
        const name = el("input", { name: `sg_name_${k}`, value: w.name, maxlength: 100, class: "eingabe", placeholder: titel, "aria-label": `Name (${titel})` });
        const km = el("input", { name: `sg_km_${k}`, value: w.km, inputmode: "decimal", class: "eingabe eingabe-schmal mono", "aria-label": `Länge km (${titel})` });
        // „anzeigen“ und „Vergleich“ stehen nicht mehr hier (Zielmonitor und Ergebnisse)
        name.addEventListener("input", () => (w.name = name.value));
        km.addEventListener("input", () => (w.km = km.value));
        tabelle.append(el("tr", { "data-segment": s.key },
          el("td", {}, el("span", { class: "mono", text: titel }), s.block !== null ? el("span", { class: "hint", text: ` · ${s.block || "Rundenblock"} ×${s.wdh}` }) : ""),
          el("td", {}, name), el("td", {}, km)));
      });
      // Abfolge und Durchfahrten je Messpunkt
      const punkte = ["Start"];
      const teile = [];
      for (const s of segs) (teile[s.teil] ||= []).push(s);
      for (const t of teile.filter(Boolean)) for (let r = 0; r < t[0].wdh; r++) for (const s of t) punkte.push(s.nach || "?");
      const anzeige = [...punkte]; // Start-/Zielort mit Namen (die Werte bleiben „Start“/„Ziel“)
      anzeige[0] = ortName("s-startort", "Start");
      if (anzeige.at(-1) === "Ziel") anzeige[anzeige.length - 1] = ortName("s-zielort", "Ziel");
      document.querySelector("#verlauf-vorschau .abfolge").textContent = anzeige.join(" · ");
      const zahl = new Map();
      for (const p of punkte.slice(1, -1)) zahl.set(p, (zahl.get(p) || 0) + 1);
      document.querySelector("#verlauf-vorschau .durchfahrten").textContent =
        zahl.size ? "Durchfahrten je Zwischenzeit: " + [...zahl].map(([p, n]) => `${p} ${n}×`).join(", ") : "Keine Zwischenzeiten – Zeitnahme nur an Start und Ziel.";
      window.streckenAbfolge = punkte;
      // Auswahl für Anfang/Ende eigener Abschnitte, wie segmente.durchfahrt_optionen
      const einmalig = [{ wert: "1:Start", text: "Start" }];
      const zaehler = new Map();
      for (const t of teile.filter(Boolean)) {
        for (let r = 1; r <= t[0].wdh; r++) {
          t.forEach((s, j) => {
            if (s.nach === "Ziel" || !s.nach) return;
            const n = (zaehler.get(s.nach) || 0) + 1;
            zaehler.set(s.nach, n);
            let nach = "";
            if (j === t.length - 1) nach = s.block !== null ? `${s.block || "Rundenblock"} ${r}` : (werte.get(s.key)?.name || `${s.von} → ${s.nach}`);
            einmalig.push({ wert: `${n}:${s.nach}`, text: `${s.nach} · ${n}. Durchfahrt${nach ? ` (nach ${nach})` : ""}` });
          });
        }
      }
      einmalig.push({ wert: "1:Ziel", text: "Ziel" });
      const wiederkehrend = [...new Set(punkte.filter((p) => p !== "?"))].map((p) => ({ wert: `1:${p}`, text: p }));
      window.streckenOptionen = { einmalig, wiederkehrend };
      window.dispatchEvent(new CustomEvent("abfolge-geaendert", { detail: punkte }));
    }
    function neu() {
      zeichnen();
      segmentTabelle();
      feld.value = JSON.stringify({ punkte: folge.map((x) => (typeof x === "string" ? x : { block: x.block, wdh: Number(x.wdh) || 1, punkte: x.punkte })) });
    }
    document.getElementById("punkt-neu")?.addEventListener("click", () => {
      folge.push("");
      neu();
      geaendert();
      liste.querySelector("li.pf-punkt:last-of-type select, li:nth-last-child(2) select")?.focus();
    });
    document.getElementById("block-neu")?.addEventListener("click", () => {
      const vorher = [...folge].reverse().find((x) => typeof x === "string") || "";
      folge.push({ block: "Runde", wdh: 2, punkte: ["", vorher].filter((p, i) => i === 0 || p) });
      neu();
      geaendert();
    });
    neu();
  }
  document.getElementById("abschnitt-neu")?.addEventListener("click", () => {
    const { tr, j } = neueZeile("abschnitte", "ab_anzahl", (j, strecke) =>
      `<td><input type="hidden" name="ab_strecke_${j}" value="${strecke}"><input type="hidden" name="ab_id_${j}" value=""><input name="ab_name_${j}" maxlength="100" class="eingabe ab-name" aria-label="Name des neuen Abschnitts"><span class="hint ab-vorschau"></span></td>` +
      `<td><select name="ab_von_${j}" class="eingabe ab-von" aria-label="Anfang"></select></td>` +
      `<td><select name="ab_nach_${j}" class="eingabe ab-nach" aria-label="Ende"></select></td>` +
      `<td><select name="ab_art_${j}" class="eingabe ab-art" aria-label="Zeit für"><option value="wiederkehrend">jede Durchfahrt</option><option value="einmalig">eine bestimmte Durchfahrt</option></select></td>` +
      `<td><input type="checkbox" name="ab_entfernen_${j}" value="1" aria-label="entfernen"></td>`);
    tr.classList.add("ab-zeile");
    tr.querySelector(`input[name="ab_name_${j}"]`).focus();
  });
  // Auswertungsabschnitte: Anfang und Ende als Auswahl aus der Abfolge – bei „eine
  // bestimmte Durchfahrt“ je Durchfahrt, bei „jede Durchfahrt“ je Punkt; Vorschau wie segmente.abschnitt_text
  const abTabelle = document.getElementById("abschnitte");
  if (abTabelle) {
    const teilen = (wert) => {
      const i = (wert || "").indexOf(":");
      return i < 0 ? [1, wert || ""] : [Number(wert.slice(0, i)) || 1, wert.slice(i + 1)];
    };
    // Optionen neu aufbauen, Auswahl behalten (beim Wechsel der Art über den Punkt-Namen)
    function fuellen(sel, art) {
      const optionen = (window.streckenOptionen || {})[art] || [];
      const [nr, punkt] = teilen(sel.value || sel.dataset.wert);
      let wert = art === "einmalig" ? `${nr}:${punkt}` : `1:${punkt}`;
      if (punkt && !optionen.some((o) => o.wert === wert) && optionen.some((o) => o.wert === `1:${punkt}`)) wert = `1:${punkt}`;
      sel.replaceChildren();
      if (punkt && !optionen.some((o) => o.wert === wert)) {
        sel.append(new Option(`${punkt}${art === "einmalig" ? ` · ${nr}. Durchfahrt` : ""} (nicht in der Abfolge)`, wert));
      }
      for (const o of optionen) sel.append(new Option(o.text, o.wert));
      if (punkt) sel.value = wert;
      sel.dataset.wert = sel.value;
    }
    function positionen(punkt, p) {
      return p.flatMap((x, i) => (x === punkt ? [i] : []));
    }
    function abschnittText(tr) {
      const p = window.streckenAbfolge || [];
      const name = tr.querySelector(".ab-name").value.trim() || "Abschnitt";
      const [vonNr, von] = teilen(tr.querySelector(".ab-von").value);
      const [nachNr, nach] = teilen(tr.querySelector(".ab-nach").value);
      const bestimmt = tr.querySelector(".ab-art").value === "einmalig";
      const vor = positionen(von, p);
      const bis = positionen(nach, p);
      let paare = [];
      if (bestimmt) {
        const i = vor[vonNr - 1];
        const k = bis[nachNr - 1];
        if (i !== undefined && k !== undefined && k > i) paare = [[i, k]];
      } else {
        for (const i of vor) {
          const k = bis.find((x) => x > i);
          if (k !== undefined) paare.push([i, k]);
        }
      }
      if (!von || !nach || !paare.length) return `${name}: kommt in der Abfolge nicht vor`;
      const [i, k] = paare[0];
      const weg = p.slice(i, k + 1).join(" → ");
      const wie = bestimmt ? `nur die ${vonNr}. Durchfahrt an ${p[i]}` : paare.length === 1 ? "einmal" : `${paare.length}-mal`;
      return `${name}: ${weg}, ${wie}`;
    }
    function abAktualisieren() {
      const p = window.streckenAbfolge || [];
      for (const tr of abTabelle.querySelectorAll("tr.ab-zeile")) {
        const art = tr.querySelector(".ab-art").value;
        const bestimmt = art === "einmalig";
        const vonSel = tr.querySelector(".ab-von");
        const nachSel = tr.querySelector(".ab-nach");
        fuellen(vonSel, art);
        fuellen(nachSel, art);
        // „eine bestimmte Durchfahrt“: Ende muss nach dem Anfang liegen
        const [vonNr, von] = teilen(vonSel.value);
        const anfang = bestimmt ? positionen(von, p)[vonNr - 1] : undefined;
        for (const o of nachSel.options) {
          const [n, punkt] = teilen(o.value);
          const pos = positionen(punkt, p)[n - 1];
          o.disabled = anfang !== undefined && pos !== undefined && pos <= anfang;
        }
        nachSel.classList.toggle("feld-fehler", !!nachSel.selectedOptions[0]?.disabled);
        tr.querySelector(".ab-vorschau").textContent = abschnittText(tr);
      }
    }
    abTabelle.addEventListener("input", abAktualisieren);
    abTabelle.addEventListener("change", abAktualisieren);
    document.getElementById("abschnitt-neu")?.addEventListener("click", abAktualisieren);
    window.addEventListener("abfolge-geaendert", abAktualisieren);
    abAktualisieren();
  }
  // Strecken-Editor: Reiter ohne Speichern wechseln, „Alles speichern“ per fetch mit
  // Fehlern am Feld und am Reiter, „Abbrechen“ verwirft, „Zurück“ warnt bei ungespeicherten Änderungen
  const editor = document.getElementById("strecke-form");
  if (editor) {
    const knoepfe = [...document.querySelectorAll("#strecke-reiter [data-reiter]")];
    const inhalte = [...document.querySelectorAll("[data-reiter-inhalt]")];
    const schluessel = knoepfe.map((k) => k.dataset.reiter);
    const zeigen = (key) => {
      for (const k of knoepfe) k.setAttribute("aria-selected", String(k.dataset.reiter === key));
      for (const el of inhalte) el.hidden = el.dataset.reiterInhalt !== key;
      history.replaceState(null, "", location.pathname + location.search + "#" + key);
    };
    knoepfe.forEach((k) => k.addEventListener("click", () => zeigen(k.dataset.reiter)));
    zeigen(schluessel.includes(location.hash.slice(1)) ? location.hash.slice(1) : schluessel[0]);

    let geaendert = false;
    const markieren = () => {
      geaendert = true;
      document.getElementById("ungespeichert").hidden = false;
    };
    editor.addEventListener("input", markieren);
    editor.addEventListener("change", markieren);
    editor.addEventListener("editor-geaendert", markieren);
    document.getElementById("aenderungen-verwerfen").addEventListener("click", () => {
      geaendert = false;
      location.reload(); // autocomplete="off": der Browser stellt keine Eingaben wieder her
    });
    document.getElementById("zurueck-strecken").addEventListener("click", (ev) => {
      if (!geaendert || !dialog) return;
      ev.preventDefault();
      const ziel = ev.currentTarget.href;
      document.getElementById("bestaetigung-text").textContent =
        "Es gibt ungespeicherte Änderungen. Ohne Speichern zurück? Die Änderungen gehen verloren.";
      document.getElementById("dialog-ok").textContent = "Verwerfen und zurück";
      dialog.returnValue = "";
      dialog.onclose = () => {
        if (dialog.returnValue === "ja") location.href = ziel;
      };
      dialog.showModal();
    });

    const fehlerFeld = document.getElementById("strecke-fehler");
    const aufraeumen = () => {
      fehlerFeld.hidden = true;
      knoepfe.forEach((k) => k.classList.remove("reiter-fehler"));
      editor.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
      editor.querySelectorAll(".feld-fehler").forEach((el) => el.remove());
    };
    editor.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      aufraeumen();
      editor.dispatchEvent(new Event("vor-speichern"));
      let daten;
      try {
        const antwort = await fetch(editor.action, {
          method: "POST", body: new FormData(editor), headers: { Accept: "application/json" },
        });
        daten = await antwort.json();
      } catch {
        daten = { ok: false, fehler: "Speichern nicht möglich – Verbindung prüfen.", reiter: null, feld: null };
      }
      if (daten.ok) {
        geaendert = false;
        const ziel = new URL(daten.ziel, location.href);
        ziel.hash = location.hash;
        if (ziel.href === location.href) location.reload();
        else location.href = ziel.href;
        return;
      }
      fehlerFeld.textContent = daten.fehler;
      fehlerFeld.hidden = false;
      const knopf = knoepfe.find((k) => k.dataset.reiter === daten.reiter);
      if (knopf) {
        knopf.classList.add("reiter-fehler");
        zeigen(daten.reiter);
      }
      const feld = daten.feld && document.getElementById(daten.feld);
      if (feld) {
        feld.setAttribute("aria-invalid", "true");
        const hinweis = document.createElement("span");
        hinweis.className = "feld-fehler";
        hinweis.textContent = daten.fehler;
        feld.insertAdjacentElement("afterend", hinweis);
        feld.focus();
      } else {
        fehlerFeld.scrollIntoView({ block: "nearest" });
      }
    });
  }
  // „Abbrechen“ in Aufklapp-Formularen: Eingaben verwerfen und zuklappen
  document.addEventListener("click", (e) => {
    const knopf = e.target.closest("[data-zuklappen]");
    if (!knopf) return;
    knopf.closest("form")?.reset();
    const details = knopf.closest("details");
    if (details) details.open = false;
  });
  // Assistent: „mit Messpunkten“ zeigt „Messpunkte einrichten“
  document.addEventListener("change", (e) => {
    const radio = e.target.closest('input[type=radio][name^="strecke_art_"]');
    if (!radio) return;
    const td = radio.closest("td");
    const knopf = td.querySelector(".messpunkte-einrichten");
    if (knopf) knopf.hidden = radio.value !== "messpunkte";
    // Wechsel von „mit Messpunkten“ auf „einfach“ nur nach Bestätigung
    const tr = radio.closest("tr");
    const bestaetigt = td.querySelector(".art-bestaetigt");
    if (radio.value === "messpunkte" && bestaetigt) bestaetigt.value = "";
    if (radio.value !== "einfach" || !tr.dataset.mitSegmenten || !dialog || !bestaetigt) return;
    const name = tr.querySelector(".strecke-name")?.value.trim() || tr.dataset.name;
    document.getElementById("bestaetigung-text").textContent =
      `Verlauf und Abschnitte von ‚${name}‘ werden gelöscht. Die Zwischenzeiten bleiben erhalten.`;
    document.getElementById("dialog-ok").textContent = "Löschen";
    dialog.returnValue = "";
    dialog.onclose = () => {
      if (dialog.returnValue === "ja") {
        bestaetigt.value = "1";
        return;
      }
      td.querySelector('input[type=radio][value="messpunkte"]').checked = true;
      if (knopf) knopf.hidden = false;
    };
    dialog.showModal();
  });
  // Zielmonitor: „mit Pfeil und Differenz“ folgt dem Feld – abgewählt und gesperrt,
  // solange der Abschnitt nicht gewählt ist; beim Anwählen wieder frei mit der Vorgabe
  document.addEventListener("change", (e) => {
    const feld = e.target.closest && e.target.closest(".live-feld input[name=live_abschnitt]");
    if (!feld) return;
    const pfeil = feld.closest(".live-feld").querySelector("input[name=live_vergleich]");
    if (!pfeil) return;
    pfeil.disabled = !feld.checked;
    pfeil.checked = feld.checked && pfeil.dataset.vorgabe === "1";
  });
})();
