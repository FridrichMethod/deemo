/* FridrichMethod's local provenance catalog. No network fetches are needed to browse.
   Every UI string comes from src/i18n/archive.js through DEEMO_I18N; catalog values are shown verbatim. */
"use strict";
(() => {
  const catalog = window.DEEMO_CATALOG;
  const i18n = window.DEEMO_I18N;
  const {t} = i18n;
  const $ = (id) => document.getElementById(id);
  if (!catalog) {
    const showMissing = () => { $("count").textContent = t("results.missing_catalog"); };
    showMissing();
    i18n.onChange(showMissing);
    return;
  }
  const familyName = (family) => i18n.has(`family.${family}`) ? t(`family.${family}`) : family;
  const kindName = (kind) => i18n.has(`kind.${kind}`) ? t(`kind.${kind}`) : kind;
  const qualityName = (quality) => i18n.has(`quality.${quality}`) ? t(`quality.${quality}`) : quality;
  const statusName = (status) => i18n.has(`status.${status}`) ? t(`status.${status}`) : status;
  // Archives records carry a class token (community_repost, official_website, …); every wiki upload carries the same
  // lineage caveat as prose, labelled as the "wiki" class. Without a label the recorded value is shown as it is.
  function provenanceClass(p) {
    const token = p.family === "wikis" ? "wiki" : p.provenance;
    return i18n.has(`provenance.class.${token}`) ? t(`provenance.class.${token}`) : p.provenance;
  }
  const sourceName = (record) => i18n.sourceName(record.source_id, record.source_name);
  const images = catalog.assets.filter((asset) => asset.gallery);
  const fields = ["query", "family", "kind", "minimum", "sort"];
  const initial = new URLSearchParams(location.search);
  // A stale or hand-edited value that matches no option is ignored, so the select keeps showing the default it applies.
  for (const field of fields) {
    const value = initial.get(field), control = $(field);
    if (value !== null && (!control.options || [...control.options].some((option) => option.value === value))) control.value = value;
  }
  let filtered = [], shown = 0, current = -1;
  const size = (bytes) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(2)} MiB` : `${Math.round(bytes / 1024)} KiB`;
  const textValue = (value) => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  // Maintainer notes in the manifests are English prose, shown verbatim; the tag keeps a zh-CN page from voicing them as Chinese.
  function note(tag, text) {
    const node = element(tag, textValue(text));
    node.lang = "en";
    return node;
  }
  function sourceLink(text, href) {
    const node = element("a", text);
    try {
      const url = new URL(href);
      if (!["http:", "https:"].includes(url.protocol)) return element("span", text);
      node.href = url.href;
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    } catch { return element("span", text); }
    return node;
  }
  // The index and the query share one fold: NFKC turns full-width and compatibility forms (Ｍａｇ, ：, ﾏﾄﾒ) into plain ones,
  // toLowerCase() ignores the browser locale (toLocaleLowerCase() maps I to dotless ı under tr/az), and katakana
  // folds to hiragana.
  const fold = (text) => text.normalize("NFKC").toLowerCase().replace(/[\u30a1-\u30f6]/g, (kana) => String.fromCharCode(kana.charCodeAt(0) - 0x60));
  // Search covers the verbatim source name and its localized display name, so the index is built per language.
  function searchable(asset) {
    return fold([asset.title, asset.artist, asset.internal_key, ...asset.provenance.flatMap((p) =>
      [p.title, p.artist, p.composer, p.collection, p.collections, p.collection_aliases, p.song_titles, p.source_name, sourceName(p), p.page_url, p.internal_key])]
      .map(textValue).join(" "));
  }
  const searchIndexes = new Map();
  function searchText() {
    if (!searchIndexes.has(i18n.lang)) searchIndexes.set(i18n.lang, new Map(images.map((asset) => [asset.id, searchable(asset)])));
    return searchIndexes.get(i18n.lang);
  }
  function renderCount() {
    $("count").textContent = t("results.count", {count: filtered.length, total: images.length});
  }
  function filter() {
    const index = searchText();
    // A colon also separates terms, so "Re: the" and "Re：the" (one term after NFKC) match the same titles.
    const terms = fold($("query").value).split(/[\s:]+/).filter(Boolean);
    filtered = images.filter((asset) =>
      terms.every((term) => index.get(asset.id).includes(term)) &&
      (!$("family").value || asset.provenance.some((p) => p.family === $("family").value)) &&
      (!$("kind").value || asset.provenance.some((p) => p.kind === $("kind").value)) &&
      Math.max(asset.width, asset.height) >= Number($("minimum").value));
    // Numeric collation keeps "page 2" before "page 10"; the page language picks the collation.
    if ($("sort").value === "title") {
      const collator = new Intl.Collator(i18n.lang, {numeric: true});
      filtered.sort((a, b) => collator.compare(a.title, b.title));
    }
    if ($("sort").value === "resolution") filtered.sort((a, b) => b.width * b.height - a.width * a.height);
    renderCount();
    $("grid").replaceChildren();
    $("empty").hidden = filtered.length > 0;
    shown = 0;
    more();
    const params = new URLSearchParams();
    for (const field of fields) if ($(field).value) params.set(field, $(field).value);
    if (i18n.lang !== i18n.defaultLang) params.set("lang", i18n.lang);
    try { history.replaceState(null, "", `${location.pathname}?${params}`); } catch { /* file:// remains usable */ }
  }
  // Language-dependent parts of a card; card.children is [image button, title, size line, source line, tag].
  function localizeCard(card, asset) {
    const [button, , , source, tag] = card.children;
    button.setAttribute("aria-label", t("card.view", {title: asset.title}));
    source.textContent = [asset.artist, sourceName(asset)].filter(Boolean).join(" · ");
    const records = asset.provenance.length > 1 ? [t("card.records", {count: asset.provenance.length})] : [];
    tag.textContent = [`${familyName(asset.family)} / ${kindName(asset.kind)}`, ...records].join(" · ");
  }
  function more() {
    const stop = Math.min(shown + 60, filtered.length);
    const fragment = document.createDocumentFragment();
    for (let index = shown; index < stop; index++) {
      const asset = filtered[index];
      const card = element("article", null, "card");
      const button = element("button", null, "card-image");
      button.type = "button";
      const image = element("img");
      image.src = asset.url;
      image.alt = asset.title;
      image.loading = "lazy";
      image.decoding = "async";
      image.width = asset.width;
      image.height = asset.height;
      button.append(image);
      button.addEventListener("click", () => open(index));
      card.append(button, element("h2", asset.title),
        element("p", `${asset.width} × ${asset.height} · ${asset.format} · ${size(asset.bytes)}`),
        element("p"), element("p", null, "tag"));
      localizeCard(card, asset);
      fragment.append(card);
    }
    $("grid").append(fragment);
    shown = stop;
    $("more").hidden = shown >= filtered.length;
  }
  function provenanceBlock(p) {
    const block = element("section", null, "provenance-item");
    block.append(sourceLink(sourceName(p), p.page_url), element("p", p.title));
    if (p.artist) block.append(element("p", t("provenance.artist", {artist: textValue(p.artist)})));
    if (p.collection) {
      const collection = {collection: textValue(p.collection)};
      block.append(element("p", p.collection_scope === "source_post_grouping" ? t("provenance.post_grouping", collection) : t("provenance.collection", collection)));
    }
    if (p.collections?.length) block.append(element("p", t("provenance.collections", {collections: p.collections.join(" / ")})));
    if (p.song_titles?.length) block.append(element("p", t("provenance.song_titles", {titles: p.song_titles.join(" / ")})));
    if (p.notes) block.append(note("p", p.notes));
    if (p.quality) block.append(element("p", t("provenance.quality", {quality: qualityName(p.quality)})));
    if (typeof p.provenance === "string" && p.provenance) block.append(element("p", t("provenance.class", {label: provenanceClass(p)})));
    for (const key of ["quality_notes", "variant_note", "layout_note", "delivery_note"]) {
      if (p[key]) block.append(note("p", p[key]));
    }
    if (p.rights_holder) block.append(element("p", t("provenance.rights_holder", {holder: textValue(p.rights_holder)})));
    if (p.wiki_original_sha1_matches === false) block.append(element("p", t("provenance.checksum_mismatch")));
    if (p.mapping_status === "unmapped" || p.title_status === "unmapped" || p.title_status === "internal_key") block.append(element("p", t("provenance.unmapped")));
    if (p.download_url) block.append(sourceLink(t("provenance.remote"), p.download_url));
    for (const variant of p.variants || []) {
      const link = element("a", t("provenance.tiny", {width: variant.width, height: variant.height, size: size(variant.bytes)}), "legacy-variant-link");
      link.href = variant.url;
      // The copy shares its basename with the original, so its role goes into the saved name (magnolia-palette-quantized.png).
      link.download = variant.path.split("/").pop().replace(/(\.[^.]+)?$/, (extension) => `-${(variant.role || "variant").replaceAll("_", "-")}${extension}`);
      const paragraph = element("p");
      paragraph.append(link);
      block.append(paragraph);
    }
    return block;
  }
  // Language-dependent parts of the viewer; open() sets the image and the language-neutral fields.
  function localizeViewer(asset) {
    $("slideshow").href = i18n.localizeHref(`index.html?asset=${encodeURIComponent(asset.id)}`);
    $("file-info").textContent = `${asset.path}\nSHA-256\n${asset.sha256}\n${asset.fetched_at || t("viewer.inherited")}`;
    $("provenance").replaceChildren(...asset.provenance.map(provenanceBlock));
  }
  function open(index) {
    current = (index + filtered.length) % filtered.length;
    const asset = filtered[current];
    $("full-image").src = asset.url;
    $("full-image").alt = asset.title;
    $("image-error").hidden = true;
    $("viewer-title").textContent = asset.title;
    $("viewer-meta").textContent = `${asset.width} × ${asset.height} · ${asset.format} · ${size(asset.bytes)}`;
    $("position").textContent = `${current + 1} / ${filtered.length}`;
    $("download").href = asset.url;
    $("download").download = asset.path.split("/").pop();
    $("original").href = asset.url;
    $("slideshow").hidden = asset.kind === "reference";
    localizeViewer(asset);
    if (!$("viewer").open) $("viewer").showModal();
  }
  $("full-image").addEventListener("error", () => { $("image-error").hidden = false; });
  $("filters").addEventListener("submit", (event) => event.preventDefault());
  for (const field of fields) $(field).addEventListener(field === "query" ? "input" : "change", filter);
  $("reset-filters").addEventListener("click", () => { $("filters").reset(); filter(); });
  $("more").addEventListener("click", more);
  $("close").addEventListener("click", () => $("viewer").close());
  // The browser hands focus back to the card that opened the viewer; after Prev/Next that is the wrong card and may be
  // far away, so focus the card of the image last shown instead (rendering cards up to it) and bring it into view.
  $("viewer").addEventListener("close", () => {
    if (!filtered[current]) return;
    while (shown <= current) more();
    const card = $("grid").children[current];
    card.querySelector(".card-image").focus({preventScroll: true});
    card.scrollIntoView({block: "nearest"});
  });
  $("previous").addEventListener("click", () => open(current - 1));
  $("next").addEventListener("click", () => open(current + 1));
  document.addEventListener("keydown", (event) => {
    if (!$("viewer").open) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); open(current - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); open(current + 1); }
  });
  const counts = new Map();
  for (const asset of catalog.assets) for (const p of asset.provenance) counts.set(p.source_id, (counts.get(p.source_id) || 0) + 1);
  // Rebuilt from scratch on every language change, so rows are never duplicated.
  function renderInventory() {
    $("inventory-summary").textContent = t("inventory.summary", {sources: catalog.summary.source_count, records: catalog.summary.source_asset_records});
    $("references").replaceChildren(...catalog.assets.filter((asset) => !asset.gallery).map((asset) => {
      const link = element("a", `${asset.title} (${asset.format}, ${size(asset.bytes)})`);
      link.href = asset.url;
      const p = element("p");
      p.append(link, document.createTextNode(" · "), sourceLink(sourceName(asset), asset.page_url));
      return p;
    }));
    $("sources").replaceChildren(...catalog.sources.map((source) => {
      const row = element("tr"), name = element("td"), status = element("td", `${statusName(source.status)} · `);
      name.append(sourceLink(i18n.sourceName(source.id, source.name), source.url));
      status.append(note("span", source.notes));
      row.append(name, element("td", counts.get(source.id) || 0), status);
      return row;
    }));
    const wasOpen = $("failures").querySelector("details")?.open ?? false;
    $("failures").replaceChildren();
    if (catalog.failures.length) {
      const details = element("details"), list = element("ul");
      details.open = wasOpen;
      details.append(element("summary", t("inventory.failures", {count: catalog.failures.length})));
      for (const failure of catalog.failures) {
        const item = element("li");
        item.append(sourceLink(failure.source_id || t("inventory.failure_source"), failure.url), document.createTextNode(` · ${textValue(failure.error)}`));
        list.append(item);
      }
      details.append(list);
      $("failures").append(details);
    }
  }
  renderInventory();
  filter();
  // Static [data-i18n] text is handled by the runtime; this re-renders the script-built text in place.
  // Filters, the number of cards shown and the open image stay as they are. A search query is matched
  // again, because the index holds localized source names and so can match differently per language;
  // a title sort is redone in the new language's collation.
  i18n.onChange(() => {
    if ($("query").value.trim() || $("sort").value === "title") {
      const previous = shown, openId = $("viewer").open ? filtered[current]?.id : null;
      filter();
      while (shown < Math.min(previous, filtered.length)) more();
      if (openId) {
        const index = filtered.findIndex((asset) => asset.id === openId);
        if (index >= 0) open(index); else { current = -1; $("viewer").close(); }
      }
    } else {
      renderCount();
      const cards = $("grid").children;
      for (let index = 0; index < cards.length; index++) localizeCard(cards[index], filtered[index]);
      if ($("viewer").open && filtered[current]) localizeViewer(filtered[current]);
    }
    renderInventory();
  });
})();
