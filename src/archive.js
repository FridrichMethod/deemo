/* FridrichMethod's local provenance catalog. No network fetches are needed to browse. */
"use strict";
(() => {
  const catalog = window.DEEMO_CATALOG;
  const $ = (id) => document.getElementById(id);
  if (!catalog) { $("count").textContent = "缺少 data/catalog.js，请运行 python scripts/build_catalog.py。"; return; }
  const familyNames = {artists: "画师本人", wikis: "Wiki", archives: "公开档案 / 转载", legacy: "原仓库"};
  const kindNames = {song_art: "单曲曲绘", collection_cover: "曲包封面", contact_sheet: "长图 / 拼图", illustration: "插画 / 未映射", reference: "扫描 / 参考"};
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
  function searchable(asset) {
    return [asset.title, asset.artist, asset.internal_key, ...asset.provenance.flatMap((p) =>
      [p.title, p.artist, p.composer, p.collection, p.collections, p.song_titles, p.source_name, p.page_url, p.internal_key])]
      .map(textValue).join(" ").toLocaleLowerCase();
  }
  const searchText = new Map(images.map((asset) => [asset.id, searchable(asset)]));
  function filter() {
    const terms = $("query").value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    filtered = images.filter((asset) =>
      terms.every((term) => searchText.get(asset.id).includes(term)) &&
      (!$("family").value || asset.provenance.some((p) => p.family === $("family").value)) &&
      (!$("kind").value || asset.provenance.some((p) => p.kind === $("kind").value)) &&
      Math.max(asset.width, asset.height) >= Number($("minimum").value));
    if ($("sort").value === "title") filtered.sort((a, b) => a.title.localeCompare(b.title));
    if ($("sort").value === "resolution") filtered.sort((a, b) => b.width * b.height - a.width * a.height);
    $("count").textContent = `${filtered.length} / ${images.length} 个图片文件 · 字节相同的文件合并展示，曲目不同版本保留`;
    $("grid").replaceChildren();
    $("empty").hidden = filtered.length > 0;
    shown = 0;
    more();
    const params = new URLSearchParams();
    for (const field of fields) if ($(field).value) params.set(field, $(field).value);
    try { history.replaceState(null, "", `${location.pathname}?${params}`); } catch { /* file:// remains usable */ }
  }
  function more() {
    const stop = Math.min(shown + 60, filtered.length);
    const fragment = document.createDocumentFragment();
    for (let index = shown; index < stop; index++) {
      const asset = filtered[index];
      const card = element("article", null, "card");
      const button = element("button", null, "card-image");
      button.type = "button";
      button.setAttribute("aria-label", `查看 ${asset.title}`);
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
        element("p", [asset.artist, asset.source_name].filter(Boolean).join(" · ")),
        element("p", `${familyNames[asset.family]} / ${kindNames[asset.kind] || asset.kind}${asset.provenance.length > 1 ? ` · ${asset.provenance.length} 个来源记录` : ""}`, "tag"));
      fragment.append(card);
    }
    $("grid").append(fragment);
    shown = stop;
    $("more").hidden = shown >= filtered.length;
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
    $("slideshow").href = `index.html?asset=${encodeURIComponent(asset.id)}`;
    $("slideshow").hidden = asset.kind === "reference";
    $("file-info").textContent = `${asset.path}\nSHA-256\n${asset.sha256}\n${asset.fetched_at || "随原仓库保留"}`;
    $("provenance").replaceChildren();
    for (const p of asset.provenance) {
      const block = element("section", null, "provenance-item");
      block.append(sourceLink(p.source_name, p.page_url), element("p", p.title));
      if (p.artist) block.append(element("p", `画师：${textValue(p.artist)}`));
      if (p.collection) block.append(element("p", `${p.collection_scope === "source_post_grouping" ? "原帖分组" : "曲包"}：${textValue(p.collection)}`));
      if (p.collections?.length) block.append(element("p", `相关曲包：${p.collections.join(" / ")}`));
      if (p.song_titles?.length) block.append(element("p", `关联曲名：${p.song_titles.join(" / ")}`));
      if (p.notes) block.append(element("p", textValue(p.notes)));
      for (const key of ["quality", "variant_note", "layout_note", "delivery_note"]) {
        if (p[key]) block.append(element("p", textValue(p[key])));
      }
      if (p.wiki_original_sha1_matches === false) block.append(element("p", "此下载文件与 Wiki 上传元数据的 checksum 不一致；实际文件已原样保留。"));
      if (p.mapping_status === "unmapped" || p.title_status === "unmapped" || p.title_status === "internal_key") block.append(element("p", "此版本尚未确认完整曲名映射。"));
      if (p.download_url) block.append(sourceLink("远程图片地址 ↗", p.download_url));
      $("provenance").append(block);
    }
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
  $("inventory-summary").textContent = `${catalog.summary.source_count} 个来源条目；${catalog.summary.source_asset_records} 条文件记录。来源状态也包括失效、需购买及已排除的候选。`;
  const counts = new Map();
  for (const asset of catalog.assets) for (const p of asset.provenance) counts.set(p.source_id, (counts.get(p.source_id) || 0) + 1);
  for (const source of catalog.sources) {
    const row = element("tr"), name = element("td");
    name.append(sourceLink(source.name, source.url));
    row.append(name, element("td", counts.get(source.id) || 0), element("td", `${source.status} · ${textValue(source.notes)}`));
    $("sources").append(row);
  }
  for (const asset of catalog.assets.filter((asset) => !asset.gallery)) {
    const link = element("a", `${asset.title} (${asset.format}, ${size(asset.bytes)})`);
    link.href = asset.url;
    const p = element("p");
    p.append(link, document.createTextNode(" · "), sourceLink(asset.source_name, asset.page_url));
    $("references").append(p);
  }
  if (catalog.failures.length) {
    const details = element("details"), list = element("ul");
    details.append(element("summary", `${catalog.failures.length} 条未能获取的记录`));
    for (const failure of catalog.failures) {
      const item = element("li");
      item.append(sourceLink(failure.source_id || "来源", failure.url), document.createTextNode(` · ${textValue(failure.error)}`));
      list.append(item);
    }
    details.append(list);
    $("failures").append(details);
  }
  filter();
})();
