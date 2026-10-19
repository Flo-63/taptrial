// Import-Vorschau: Zellen bearbeiten, sofort prüfen (Server), Zeilen hinzufügen/löschen, Filter.
(function () {
  const daten = JSON.parse(document.getElementById("import-daten").textContent);
  const tbody = document.querySelector("#import-tabelle tbody");
  const zaehler = document.getElementById("zaehler");
  const knopf = document.getElementById("importieren");
  let timer = null;

  function modus() {
    const r = document.querySelector("input[name=modus]:checked");
    return r ? r.value : "ergaenzen";
  }

  function zeilen() {
    return [...tbody.querySelectorAll("tr")].map((tr) => {
      const z = {};
      tr.querySelectorAll("input[data-feld]").forEach((inp) => (z[inp.dataset.feld] = inp.value));
      return z;
    });
  }

  function nummerieren() {
    [...tbody.querySelectorAll("tr")].forEach((tr, i) => {
      tr.dataset.index = i;
      tr.querySelector(".nr").textContent = i + 1;
    });
    document.getElementById("anzahl-zeilen").textContent = tbody.querySelectorAll("tr").length;
  }

  function anzeigen(p) {
    tbody.querySelectorAll("td.zelle").forEach((td) => {
      td.classList.remove("zelle-fehler", "zelle-warnung");
      td.querySelector(".zellmeldung").textContent = "";
    });
    const zeilenTr = [...tbody.querySelectorAll("tr")];
    for (const [art, klasse] of [["warnungen", "zelle-warnung"], ["fehler", "zelle-fehler"]]) {
      for (const [i, feld, text] of p[art]) {
        const td = zeilenTr[i] && zeilenTr[i].querySelector(`td[data-feld="${feld}"]`);
        if (!td) continue;
        td.classList.add(klasse);
        const span = td.querySelector(".zellmeldung");
        span.textContent = span.textContent ? `${span.textContent}; ${text}` : text;
      }
    }
    zaehler.textContent = `${p.anzahl_fehler} Fehler, ${p.anzahl_warnungen} Warnungen`;
    zaehler.classList.toggle("mit-fehlern", p.anzahl_fehler > 0);
    knopf.disabled = p.anzahl_fehler > 0 || p.zeilen === 0;
    filtern();
  }

  async function pruefen() {
    const antwort = await fetch(daten.url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": daten.csrf },
      body: JSON.stringify({ zeilen: zeilen(), modus: modus() }),
    });
    if (antwort.ok) anzeigen(await antwort.json());
  }

  function spaeterPruefen() {
    clearTimeout(timer);
    knopf.disabled = true; // erst nach der Prüfung wieder freigeben
    timer = setTimeout(pruefen, 300);
  }

  function filtern() {
    const nur = document.getElementById("nur-fehler").checked;
    tbody.querySelectorAll("tr").forEach((tr) => {
      tr.hidden = nur && !tr.querySelector(".zelle-fehler");
    });
  }

  function zeileAnlegen() {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="nr"></td>${daten.spalten
      .map((f) => `<td class="zelle" data-feld="${f}"><input data-feld="${f}" aria-label="${f}"><span class="zellmeldung"></span></td>`)
      .join("")}<td><button type="button" class="btn btn-quiet btn-sm btn-danger-text zeile-loeschen" title="Zeile löschen" aria-label="Zeile löschen">×</button></td>`;
    tbody.append(tr);
    nummerieren();
    tr.querySelector("input").focus();
    spaeterPruefen();
  }

  tbody.addEventListener("input", spaeterPruefen);
  tbody.addEventListener("click", (ev) => {
    if (!ev.target.classList.contains("zeile-loeschen")) return;
    ev.target.closest("tr").remove();
    nummerieren();
    spaeterPruefen();
  });
  document.getElementById("zeile-hinzufuegen").addEventListener("click", zeileAnlegen);
  document.getElementById("nur-fehler").addEventListener("change", filtern);
  document.querySelectorAll("input[name=modus]").forEach((r) =>
    r.addEventListener("change", () => {
      const w = document.getElementById("ersetzen-warnung");
      if (w) w.hidden = modus() !== "ersetzen";
      spaeterPruefen();
    }),
  );
  window.importPruefen = pruefen;
})();
