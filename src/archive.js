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
  const sourceName = (record) => i18n.sourceName(record.source_id, record.source_name);
  const images = catalog.assets.filter((asset) => asset.gallery);
  const fields = ["query", "family", "kind", "minimum", "sort"];
  const initial = new URLSearchParams(location.search);
  for (const field of fields) if (initial.has(field)) $(field).value = initial.get(field);
  let filtered = [], shown = 0, current = -1;
  const size = (bytes) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(2)} MiB` : `${Math.round(bytes / 1024)} KiB`;
  const textValue = (value) => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
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
  // Search covers the verbatim source name and its localized display name, so the index is built per language.
  function searchable(asset) {
    return [asset.title, asset.artist, asset.internal_key, ...asset.provenance.flatMap((p) =>
      [p.title, p.artist, p.composer, p.collection, p.collections, p.collection_aliases, p.song_titles, p.source_name, sourceName(p), p.page_url, p.internal_key])]
      .map(textValue).join(" ").toLocaleLowerCase();
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
    const terms = $("query").value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    filtered = images.filter((asset) =>
      terms.every((term) => index.get(asset.id).includes(term)) &&
      (!$("family").value || asset.provenance.some((p) => p.family === $("family").value)) &&
      (!$("kind").value || asset.provenance.some((p) => p.kind === $("kind").value)) &&
      Math.max(asset.width, asset.height) >= Number($("minimum").value));
    if ($("sort").value === "title") filtered.sort((a, b) => a.title.localeCompare(b.title));
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
    if (p.notes) block.append(element("p", textValue(p.notes)));
    for (const key of ["quality", "variant_note", "layout_note", "delivery_note"]) {
      if (p[key]) block.append(element("p", textValue(p[key])));
    }
    if (p.wiki_original_sha1_matches === false) block.append(element("p", t("provenance.checksum_mismatch")));
    if (p.mapping_status === "unmapped" || p.title_status === "unmapped" || p.title_status === "internal_key") block.append(element("p", t("provenance.unmapped")));
    if (p.download_url) block.append(sourceLink(t("provenance.remote"), p.download_url));
    for (const variant of p.variants || []) {
      const link = element("a", t("provenance.tiny", {width: variant.width, height: variant.height, size: size(variant.bytes)}), "legacy-variant-link");
      link.href = variant.url;
      link.download = variant.path.split("/").pop();
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
      const row = element("tr"), name = element("td");
      name.append(sourceLink(i18n.sourceName(source.id, source.name), source.url));
      row.append(name, element("td", counts.get(source.id) || 0), element("td", `${source.status} · ${textValue(source.notes)}`));
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
  // again, because the index holds localized source names and so can match differently per language.
  i18n.onChange(() => {
    if ($("query").value.trim()) {
      const previous = shown, openId = $("viewer").open ? filtered[current]?.id : null;
      filter();
      while (shown < Math.min(previous, filtered.length)) more();
      if (openId) {
        const index = filtered.findIndex((asset) => asset.id === openId);
        if (index >= 0) open(index); else $("viewer").close();
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
