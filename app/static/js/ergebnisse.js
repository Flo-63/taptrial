// Ergebnisseite: alle 10 s neu laden (HTML-Fragment vom Server), bei Ausfall „Stand von …“.
(function () {
  const INTERVALL_MS = 10000;
  let letzterErfolg = Date.now();
  const zwei = (n) => String(n).padStart(2, "0");
  const uhr = (ms) => { const d = new Date(ms); return `${zwei(d.getHours())}:${zwei(d.getMinutes())}:${zwei(d.getSeconds())}`; };

  async function aktualisieren() {
    try {
      const antwort = await fetch("/ergebnisse/inhalt" + location.search, { cache: "no-store" });
      if (!antwort.ok) throw new Error(antwort.status);
      const html = await antwort.text();
      const scroll = window.scrollY;
      document.getElementById("inhalt").innerHTML = html;
      window.scrollTo(0, scroll);
      letzterErfolg = Date.now();
      document.getElementById("stand").hidden = true;
    } catch (e) {
      const stand = document.getElementById("stand");
      stand.hidden = false;
      stand.textContent = `Keine Verbindung – Stand von ${uhr(letzterErfolg)}`;
    }
  }
  setInterval(aktualisieren, INTERVALL_MS);
  window.ergebnisseAktualisieren = aktualisieren;
})();
