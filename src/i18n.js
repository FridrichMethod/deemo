/* Shared language switch for the static pages: English by default, Simplified Chinese on request.
   The choice is kept in localStorage and mirrored in ?lang= so file:// browsing keeps it as well. */
"use strict";
(() => {
  const DEFAULT = "en", ALTERNATE = "zh-CN", STORAGE_KEY = "deemo-lang";
  const messages = {[DEFAULT]: {}, [ALTERNATE]: {}};
  const listeners = [];
  let ready = false;
  function normalize(value) {
    const tag = String(value ?? "").trim().toLowerCase();
    if (tag === "en" || tag.startsWith("en-")) return DEFAULT;
    if (tag === "zh" || tag.startsWith("zh-")) return ALTERNATE;
    return null;
  }
  function storedLang() {
    try { return normalize(localStorage.getItem(STORAGE_KEY)); } catch { return null; }
  }
  let lang = normalize(new URLSearchParams(location.search).get("lang")) || storedLang() || DEFAULT;
  const has = (key) => Object.hasOwn(messages[lang], key) || Object.hasOwn(messages[DEFAULT], key);
  function t(key, vars) {
    const text = messages[lang][key] ?? messages[DEFAULT][key] ?? key;
    return vars == null ? text : text.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(vars, name) ? String(vars[name]) : match);
  }
  // Same-site relative links carry ?lang= for the non-default language; absolute URLs and bare fragments are left alone.
  function localizeHref(href) {
    const match = /^([^?#]*)(\?[^#]*)?(#.*)?$/s.exec(href ?? "");
    if (!match || /^([a-z][a-z\d+.-]*:|\/\/)/i.test(href) || (!match[1] && !match[2] && match[3])) return href;
    const params = new URLSearchParams(match[2] || "");
    if (lang === DEFAULT) params.delete("lang"); else params.set("lang", lang);
    const query = params.toString();
    return match[1] + (query ? `?${query}` : "") + (match[3] || "");
  }
  function apply(root = document) {
    document.documentElement.lang = lang;
    for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
    for (const node of root.querySelectorAll("[data-i18n-attr]")) {
      for (const pair of node.dataset.i18nAttr.split(";")) {
        const [attribute, key] = pair.split(":").map((part) => part.trim());
        if (attribute && key) node.setAttribute(attribute, t(key));
      }
    }
    for (const link of root.querySelectorAll("a[data-lang-link]")) link.setAttribute("href", localizeHref(link.getAttribute("href")));
    for (const button of root.querySelectorAll("[data-lang-toggle]")) {
      // The label names the other language in that language, so it carries that language's tag.
      button.textContent = t("lang.toggle");
      button.lang = lang === DEFAULT ? ALTERNATE : DEFAULT;
      button.title = t("lang.toggle.title");
    }
  }
  function setLang(value) {
    const next = normalize(value) || DEFAULT;
    if (next === lang) return;
    lang = next;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* ?lang= still carries the choice */ }
    try { history.replaceState(history.state, "", location.pathname + localizeHref(location.search + location.hash)); } catch { /* some file:// contexts reject history updates */ }
    apply();
    for (const listener of listeners) listener(lang);
  }
  document.addEventListener("click", (event) => {
    const toggle = event.target instanceof Element && event.target.closest("[data-lang-toggle]");
    if (!toggle) return;
    event.preventDefault();
    setLang(lang === DEFAULT ? ALTERNATE : DEFAULT);
  });
  function markReady() { ready = true; apply(); }
  if (document.readyState === "complete") markReady(); else document.addEventListener("DOMContentLoaded", markReady);
  window.DEEMO_I18N = Object.freeze({
    defaultLang: DEFAULT,
    languages: [DEFAULT, ALTERNATE],
    get lang() { return lang; },
    register(table) {
      for (const code of Object.keys(messages)) Object.assign(messages[code], table[code]);
      if (ready) apply();
    },
    t, has, apply, setLang, localizeHref,
    sourceName: (id, fallback) => has(`source.${id}`) ? t(`source.${id}`) : fallback,
    onChange(listener) { listeners.push(listener); },
  });
})();
