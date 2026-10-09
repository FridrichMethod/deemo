# DEEMO 1 公开 Wiki 曲绘来源

[English](sources-wikis.md) · **简体中文**

本目录记录 Fandom DEEMO Wiki 与 Bilibili DEEMO Wiki 的公开曲绘。下载对象是原版 DEEMO 及其 Last Recital / Reborn 移植内容；不包含 DEEMO II、音频或谱面。

2026-09-05 导入结果：**757 / 757 个候选图片全部成功，最终失败 0 项**，共 **457,462,452 bytes**（约 436.27 MiB）。Fandom 561 张、BWIKI 196 张；其中单曲图 662 张、曲包封面 95 张；实际格式为 756 PNG、1 JPEG。

全量审计已逐文件核对 byte count、SHA-256、SHA-1、Pillow 实际格式与尺寸，并检查 manifest 唯一 ID 与磁盘文件一一对应；757 张全部通过，无孤立文件。753 张与 Wiki 发布的原上传 SHA-1 一致。4 张不同：`Spring Snowflake Flower`、`Ark of Desire`、`I Race The Dawn x Sunset`、`Protest`，均保留差异标记。7 张通过 full-size `format=png` 回退取得，其 SHA-1 最终也都与原上传一致。

2026-10-08 增量：来源检查工作流发现 57 个 BWIKI 曲包封面候选（约 180×180 的缩略图，均为 2021 年上传，属于 `allimages` 枚举差异而非新发布曲绘），以 `--resume` 导入，全部与 Wiki 原上传 SHA-1 一致，另有 11 条已有记录的 `collections`/`related_pages` 随新候选元数据更新。现为 **814 张**（Fandom 561、BWIKI 253；单曲图 662、曲包封面 152），共 **459,827,277 bytes**（约 438.53 MiB），813 PNG、1 JPEG；SHA-1 不一致的仍为上述 4 张。

## 文件与来源

- `assets/public/wikis/fandom/`：从原版 `Category:Songs` 的歌页，以及原版曲包页面取得图片引用。
- `assets/public/wikis/bwiki/`：枚举 `allimages` 全部分页，以歌曲索引的规范化标题匹配图片名，另收录可确认的曲包封面。
- `data/sources/wikis.json`：最终图片清单、下载状态与失败记录，是图库整合的输入。
- `data/sources/wiki-discovery.json`：可复查和继续下载的候选快照，包含原始 MediaWiki imageinfo。
- `data/sources/wiki-song-index.json`：公开歌页的标题、曲包、作曲家和图片引用。模板字段 `Artist` 是作曲家，存为 `composer`，不会被误当成画师。
- `data/sources/wiki-illustrator-index.json`：日本 Wiki 目录的 479 条按原页面顺序保存的标题/表格行；页面同时含作曲家、Vocalist 和独立 `illustrator` 区段，最后者列出 9 位画师，不把其他区段当成画师归属。
- `data/sources/song-mapping.json`：来自 [syuchan1005/DeemoSongs](https://github.com/syuchan1005/DeemoSongs) 的 legacy 游戏内部 key 与歌曲信息，其 [MIT license](../licenses/DeemoSongs-MIT.txt) 原样保留。该映射较旧，不能代表当前完整曲目集。

Wiki 歌页还包含已移除歌曲、端口独占曲与不同版本图片，因此歌页数/图片数不能直接当成当前手机版的唯一歌曲数量。来自不同 Wiki 的图片、同曲多版本和相同 SHA-256 的重复来源记录均保留，供上层图库按来源比较。

## 图像保真

脚本保存 HTTP 响应的原始 bytes，不重新编码、放大、裁切、调色或去背景。每张图片均用 Pillow 验证实际格式与尺寸，并记录 SHA-256、下载时间、真实 Content-Type、来源页、请求 URL、最终响应 URL 和 Wiki 元数据。

Fandom CDN 请求使用公开的 `format=original`，避免默认协商成 WebP；先请求直接文件 URL，遇到 CDN 后端错误再尝试 API 提供的完整 revision URL。两个 original 端点都失败时，最后尝试公开的全尺寸 `format=png`，明确标记可能经过 CDN 重编码，仍逐项比较原上传 checksum/size。保存文件扩展名来自解码验证的实际格式，不盲信 URL 扩展名。

`wiki_original_size_matches` 与 `wiki_original_sha1_matches` 表示所下载文件是否和 Wiki 发布的原上传元数据一致。少数 CDN 文件可能尺寸相同但 checksum/size 不同；这些记录不宣称与上传母文件相同。1024×2048 或 2048×1024 文件可能是 atlas 或拼图，原样保留并标记，不自动裁开。

公开 Wiki 上传本身不等于画师制作母档，也没有从颜色数量推断原生清晰度。画师和 Rayark 的原有署名及权利归属不因本仓库收集而改变。

## 复现

环境依赖：Python 3.10+、`requests`、`Pillow`；抓取可选日本画师索引时还使用 `beautifulsoup4`。可以隔离模式运行：

```sh
python -I scripts/fetch_wikis.py --workers 4
```

仅继续已保存的候选快照（先验证已存在文件的 SHA-256，再重试缺失下载）：

```sh
python -I scripts/fetch_wikis.py --resume --workers 4
```

脚本限制最多 4 个并发请求，设置超时和有限重试，逐 25 个结果保存 manifest。`--metadata-only` 只更新候选元数据；已经存在的最终图片 manifest 会保留。

## 本次访问限制

初次已完整读取 BWIKI 的 480 项 `allimages` 与 182 个歌页；后续刷新 API 遇到 EdgeOne HTTP 567，继续下载使用先前保存的候选快照。最终清单不宣称包含该站每一项未经核对的图片。源列表 `discovery.excluded_large_images` 保留未能明确映射的大图名和尺寸。

[日本 DEEMO Wiki 的画师目录](https://wikiwiki.jp/deemo/アーティスト別リスト2)最初返回 Cloudflare 403，在下载完成后的正常索引请求中恢复 200，已保存索引。没有绕过挑战或使用登录凭据。实际成败以 `wikis.json` 为准。
