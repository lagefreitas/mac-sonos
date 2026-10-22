/* page language: the saved choice, else the browser's, else English */
(function () {
  const root = document.documentElement;
  const stored = (function () { try { return localStorage.getItem("lang"); } catch (e) { return null; } })();
  const wanted = stored || ((navigator.language || "en").toLowerCase().indexOf("pt") === 0 ? "pt" : "en");
  function setLang(lang) {
    root.setAttribute("data-lang", lang);
    root.lang = lang;
    const title = root.dataset[lang === "pt" ? "titlePt" : "titleEn"];
    if (title) document.title = title;
    document.querySelectorAll("[data-set-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.setLang === lang)); });
    document.querySelectorAll("[data-copy]").forEach(function (b) { b.textContent = lang === "pt" ? "Copiar" : "Copy"; });
    try { localStorage.setItem("lang", lang); } catch (e) {}
  }
  document.querySelectorAll("[data-set-lang]").forEach(function (b) { b.addEventListener("click", function () { setLang(b.dataset.setLang); }); });
  setLang(wanted === "pt" ? "pt" : "en");
})();

/* copy buttons: the Clipboard API when it answers, else the old way, so they also work where it does not */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await Promise.race([navigator.clipboard.writeText(text), new Promise(function (_, reject) { setTimeout(reject, 800); })]);
      return true;
    }
  } catch (e) { /* fall through */ }
  try {
    const box = document.createElement("textarea");
    box.value = text; box.setAttribute("readonly", ""); box.style.cssText = "position:fixed;opacity:0;top:0;left:0";
    document.body.appendChild(box); box.select();
    const ok = document.execCommand("copy");
    box.remove();
    return ok;
  } catch (e) { return false; }
}
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  btn.addEventListener("click", async function () {
    const text = btn.parentElement.querySelector("code").innerText;
    const pt = document.documentElement.dataset.lang === "pt";
    btn.textContent = (await copyText(text)) ? (pt ? "Copiado" : "Copied") : "—";
    setTimeout(function () { btn.textContent = pt ? "Copiar" : "Copy"; }, 1400);
  });
});
