# DEEMO 1 公开 Wiki 曲绘来源

[English](sources-wikis.md) · **简体中文**

本目录记录 Fandom DEEMO Wiki 与 Bilibili DEEMO Wiki 的公开曲绘。下载对象是原版 DEEMO 及其 Last Recital / Reborn 移植内容；不包含 DEEMO II、音频或谱面。

2026-09-05 导入结果：**757 / 757 个候选图片全部成功，最终失败 0 项**，共 **457,462,452 bytes**（约 436.27 MiB）。Fandom 561 张、BWIKI 196 张；其中单曲图 662 张、曲包封面 95 张；实际格式为 756 PNG、1 JPEG。

全量审计已逐文件核对 byte count、SHA-256、SHA-1、Pillow 实际格式与尺寸，并检查 manifest 唯一 ID 与磁盘文件一一对应；757 张全部通过，无孤立文件。753 张与 Wiki 发布的原上传 SHA-1 一致。4 张不同：`Spring Snowflake Flower`、`Ark of Desire`、`I Race The Dawn x Sunset`、`Protest`，均保留差异标记。7 张通过 full-size `format=png` 回退取得，其 SHA-1 最终也都与原上传一致。

2026-10-08 增量：来源检查工作流发现 57 个 BWIKI 曲包封面候选（均为 2021 年上传，属于 `allimages` 枚举差异而非新发布曲绘），以 `--resume` 导入，全部与 Wiki 原上传 SHA-1 一致，另有 11 条已有记录的 `collections`/`related_pages` 随新候选元数据更新。其中 5 个 70×47 的 `* Collections Titletab` 是 UI 标题页签而非封面：BWIKI 发现流程当时尚未套用 Fandom 的 UI 过滤。它们已于 2026-10-09 从快照、清单和仓库中移除，发现流程现已将其排除。其余 52 个为约 180×180 的缩略图。现为 **809 张**（Fandom 561、BWIKI 248；单曲图 662、曲包封面 147），共 **459,809,756 bytes**（约 438.51 MiB），808 PNG、1 JPEG；SHA-1 不一致的仍为上述 4 张。

2026-10-09 元数据修正（未改动任何图片）：快照（`wiki-discovery.json`）与清单（`wikis.json`）已按当前抓取脚本的输出修补。112 条记录的 `collections` 把同一曲包以仅大小写、空格或标点不同的多种拼写重复列出（`Etude Collection` / `Etude collection`），现各保留一种拼写。16 个 BWIKI 曲包封面的曲包名及 `related_pages` 中的 BWIKI 页面改用 BWIKI 自己的拼写（`RAC collection -1` 而非 `RAC Collection #1`）；其中 5 个曲包没有任何 BWIKI 歌页提及，改用 Fandom 各拼写中按码位排序的第一个。7 张经 `format=png` 取得的图片 SHA-1 与大小均与原上传一致，其 `delivery_note` 不再称其可能经过 CDN 重编码。随后又有两处修补，同样以抓取脚本自身的函数从已提交的快照与歌曲索引算出。290 条记录新增 `collection_aliases`，即两个 Wiki 的歌页对其曲包使用的其他拼写（列出 `RAC Collection #1` 的记录同时带有 `RAC collection -1`）；档案馆搜索会匹配这些拼写，查看器不显示。BWIKI 曲包封面 `RAC collection -4`、`-5`、`-6` 的 `related_pages` 改为以其文件名命名的 BWIKI 页面，因为 MediaWiki 页面标题不能含 `#`。最后一处修补同样以抓取脚本的 `with_composers()` 算出，为 463 条单曲图记录（Fandom 287、BWIKI 176）新增 `composer`：记录所映射歌曲的作曲家，取自其所属 Wiki 的歌页；该页未写作曲家时，取另一个 Wiki 的同名歌页。档案馆搜索以及查看器和轮播中的“曲师”栏都会用到它。取自另一个 Wiki 的作曲家（92 条 Fandom 记录，均取自 BWIKI）另带 `composer_source`，即该 Wiki 的来源 ID，查看器会在署名旁注明该 Wiki。

## 文件与来源

- `assets/public/wikis/fandom/`：从原版 `Category:Songs` 的歌页，以及原版曲包页面取得图片引用。
- `assets/public/wikis/bwiki/`：枚举 `allimages` 全部分页，以歌曲索引的规范化标题匹配图片名，另收录可确认的曲包封面。UI 图片（标题页签、标志、截图、DEEMO II）按与 Fandom 相同的规则排除；两个 Wiki 的曲包封面短边均须至少 100 px。
- `data/sources/wikis.json`：最终图片清单、下载状态与失败记录，是图库整合的输入。`collections` 每个曲包只列一种拼写；`collection_aliases` 保存歌页使用的其他拼写，仅供搜索。单曲图的 `composer` 复制自歌曲索引（见上文）；署名取自另一个 Wiki 的歌页时，`composer_source` 注明该 Wiki。
- `data/sources/wiki-discovery.json`：可复查和继续下载的候选快照，包含原始 MediaWiki imageinfo，并在 `stats` 中记录产生该快照的枚举统计（歌页、曲包页或 `allimages` 项数，以及被排除的图片）。`--resume` 会把这些统计连同快照时间 `snapshot_fetched_at` 复制到 `wikis.json` 的 `discovery`。
- `data/sources/wiki-song-index.json`：公开歌页的标题、曲包、作曲家和图片引用。模板字段 `Artist` 是作曲家，存为 `composer`，不会被误当成画师。
- `data/sources/wiki-illustrator-index.json`：日本 Wiki 目录的 479 条按原页面顺序保存的标题/表格行；页面同时含作曲家、Vocalist 和独立 `illustrator` 区段，最后者列出 9 位画师，不把其他区段当成画师归属。
- `data/sources/song-mapping.json`：来自 [syuchan1005/DeemoSongs](https://github.com/syuchan1005/DeemoSongs) 的 legacy 游戏内部 key 与歌曲信息，其 [MIT license](../licenses/DeemoSongs-MIT.txt) 原样保留。该映射较旧，不能代表当前完整曲目集。

Wiki 歌页还包含已移除歌曲、端口独占曲与不同版本图片，因此歌页数/图片数不能直接当成当前手机版的唯一歌曲数量。来自不同 Wiki 的图片、同曲多版本和相同 SHA-256 的重复来源记录均保留，供上层图库按来源比较。

## 图像保真

脚本保存 HTTP 响应的原始 bytes，不重新编码、放大、裁切、调色或去背景。每张图片均用 Pillow 验证实际格式与尺寸，并记录 SHA-256、下载时间、真实 Content-Type、来源页、请求 URL、最终响应 URL 和 Wiki 元数据。

Fandom CDN 请求使用公开的 `format=original`，避免默认协商成 WebP；先请求直接文件 URL，遇到 CDN 后端错误再尝试 API 提供的完整 revision URL。两个 original 端点都失败时，最后尝试公开的全尺寸 `format=png`，仍逐项比较原上传 checksum/size；只有两者不一致时，`delivery_note` 才标记可能经过 CDN 重编码（一致时注明相符）。保存文件扩展名来自解码验证的实际格式，不盲信 URL 扩展名。

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

`--resume` 不会从 `wikis.json` 删去任何记录，也不会删除文件。候选已不在快照中的记录会保留，并标记 `"upstream_status": "removed"`。重新上传的文件下载成功后，新版本沿用原资产 ID，深链接保持不变；旧版本连同文件与记录改用 `<资产 ID>:<其 SHA-256 前 12 位十六进制>` 作为 ID，标记 `"upstream_status": "superseded"`，并以 `"superseded_by"` 指向当前 ID。重新下载失败时保留旧记录并标记 `"upstream_status": "fetch_failed"`，同时在 `failures` 中记录；下次 `--resume` 会重试。

脚本限制最多 4 个并发请求，设置超时和有限重试（连接错误、超时、HTTP 429、500、502、503、504 与 BWIKI 的 EdgeOne 567 最多尝试 3 次，遵循 `Retry-After`，上限 30 秒；其他 HTTP 错误立即失败），逐 25 个结果保存 manifest；每次保存都以原子方式替换文件，并仍列出全部已有记录，中断的运行不会丢失记录。`--metadata-only` 不下载图片，只重写 `wiki-discovery.json`（候选与统计）与 `wiki-song-index.json`；已有的 `wikis.json` 保持不变，`song-mapping.json` 也不受影响，因为 DeemoSongs 映射只在完整运行（不带 `--resume` 或 `--metadata-only`）时抓取。

## 本次访问限制

2026-09-05 的初次运行读取了 BWIKI 的 480 项 `allimages` 与 182 个歌页；后续刷新 API 遇到 EdgeOne HTTP 567，继续下载使用先前保存的候选快照。该列表并不完整：2026-10-08 来源检查的枚举又返回了 57 个曲包封面候选，均于 2021 年上传。2026-10-08 的快照没有记录统计信息，因此在导入更新的发现结果之前，`wikis.json` 中的 `discovery`（480 项、40 个 `excluded_large_images`）描述的仍是 2026-09-05 的枚举。最终清单不宣称包含该站每一项未经核对的图片。源列表 `discovery.excluded_large_images` 保留未能明确映射的大图名和尺寸。

[日本 DEEMO Wiki 的画师目录](https://wikiwiki.jp/deemo/アーティスト別リスト2)最初返回 Cloudflare 403，在下载完成后的正常索引请求中恢复 200，已保存索引。没有绕过挑战或使用登录凭据。实际成败以 `wikis.json` 为准。
