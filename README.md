# DEEMO 1 曲绘档案

[FridrichMethod / deemo](https://github.com/FridrichMethod/deemo) 的个人曲绘收藏，由 FridrichMethod（Zhaoyang Li）维护。收集 DEEMO 原版及相关 Last Recital / Reborn 的公开曲绘；DEEMO II 不在收集范围内。

原项目来自 [mashirozx/deemo](https://github.com/mashirozx/deemo)。上游历史已压缩为一个初始快照；游戏纹理、界面设计署名和 Apache-2.0 软件许可证保留。此版本已替换维护者链接、页面信息和 manifest，移除原站 CNAME、Google Analytics 和社交账号入口。仓库公开，网站通过 GitHub Pages 发布。

## 浏览

在线访问：[可搜索图库](https://fridrichmethod.github.io/deemo/archive.html) · [幻灯片](https://fridrichmethod.github.io/deemo/)。仓库、网站和曲绘均公开，无需登录即可访问。

启动仅限本机的静态服务：

```sh
python -I -m http.server 8765 --bind 127.0.0.1
```

打开 [http://127.0.0.1:8765/archive.html](http://127.0.0.1:8765/archive.html)：可搜索曲名、画师与曲包，按来源、类型、尺寸筛选；点击图片查看原文件、SHA-256、来源页面和下载地址。[index.html](index.html) 保留原来的幻灯片风格，已经接入新增素材，也可从档案页跳到指定图片。两页均只加载本地素材，没有统计或远程字体请求。

目录也可直接双击 `archive.html` 离线浏览；HTTP 服务对下载、幻灯片截图等浏览器功能兼容性更好。

## 网站部署

推送到 `main` 后，[Pages 工作流](.github/workflows/pages.yml) 自动发布。`scripts/prepare_pages.py` 只复制 Git 跟踪的 `assets/`、`data/`、`src/`、`docs/`、`licenses/` 和明确列出的根目录网页、配置、署名文件；不会发布 `.git/`、工作流、抓取脚本、测试或未跟踪文件。上传目标仅为独立生成的站点目录，不是仓库根目录。白名单只限定网站发布范围，不是敏感内容检测器；仓库本身公开，提交任何文件前仍需检查内容。

页面保留 `noindex,nofollow`，但这不是访问控制。发布包以 950 MB 为硬上限，给 GitHub Pages 的 1 GB 上限留出余量；新增素材超限时部署会停止，现有线上版本不变。保留全部原图字节，不在发布时重新压缩素材。

## 来源更新检查

[检查工作流](.github/workflows/check-sources.yml) 每周一 03:00 UTC 自动运行，也可在 Actions 页手动触发。它以 `--metadata-only` 重新枚举 Fandom 与 BWIKI 的曲绘候选，不下载任何图片，再用 `scripts/check_sources.py` 与 `main` 上的 `data/sources/wiki-discovery.json` 比较。候选集合没有变化时只在运行摘要里记录；出现新增、重新上传（SHA-1 或尺寸变化）或移除的文件时，工作流把新的发现快照和歌曲索引推到 `auto/wiki-source-check` 分支，并创建或更新一个标题形如"Wiki 来源更新：新增 N · 重新上传 M · 移除 K"的 PR，正文列出每个文件的来源页、尺寸、曲名和本地是否已有下载。

合并该 PR 只更新快照，不改变图库。随后在本地运行 `scripts/fetch_wikis.py --resume` 下载新增和重新上传的文件，再 build、verify、检查 `failures` 与 checksum 字段并提交图片与清单（命令见 PR 正文和下文"目录与复现"）。首次启用前需要在仓库 Settings → Actions → General → Workflow permissions 勾选 "Allow GitHub Actions to create and approve pull requests"，否则工作流能推分支但无法创建 PR。公开仓库 60 天没有提交时 GitHub 会暂停 `schedule` 触发，需在 Actions 页重新启用。

画师来源（`fetch_artists.py`）的 Pixiv 作品 ID 写在脚本里，公开档案来源基本是静态内容，二者都不在自动检查范围内；要补充新作品仍需手动编辑脚本并重新抓取。

## 已整理的来源

| 来源 | 内容与说明 |
| --- | --- |
| [画师本人](docs/sources-artists.md) | 86 张公开上传：ころころさん、SnowEgg、Ryori、K@I、Blaze Wu、Siyouko。包括曲绘、曲包封面、长拼图和单独标记的过程参考。最高单张曲绘为 3030×3030。 |
| [Wiki](docs/sources-wikis.md) | 814 张：Fandom 561、BWIKI 253。包括原版歌曲/曲包图片、不同版本及纹理 atlas，保留实际下载文件与原站 metadata 校验结果。另保存日本 Wiki 的画师目录。 |
| [公开档案](docs/sources-archives.md) | 189 张历史 Tumblr 封面、19 张 OST 包装/内页扫描、4 张官网插画、2 份官方参考 PDF。低分辨率转载与扫描有单独分类。 |
| 原仓库 | `assets/legacy/trans/` 的 320 张素材用于展示；`assets/legacy/tiny/` 的 320 个量化副本作为配对版本，在原图详情中提供下载入口。 |

2026-09-05 共归档 1,057 个公开来源文件；2026-10-08 经来源检查补入 57 张 BWIKI 曲包封面缩略图，公开来源文件合计 1,114 个。另保留原仓库 320 张图片及其 320 个量化副本。量化副本记录在 `variants` 中，不重复计入图库张数。精确去重数量与获取状态以 [data/catalog.json](data/catalog.json) 为准。图片数包括同曲不同版本、曲包封面及参考资料，**不等于独立歌曲数，也不代表全曲母图已齐全**。字节完全相同的文件合并展示，同时保留所有来源；没有按尺寸或文件大小盲目覆盖其他版本。

所有下载均保留响应的原始 bytes，没有 AI 放大、裁切、去水印、去背景或格式转换。`original` 表示站点提供的原始下载规格，不自动等同于画师工作母档。Fandom 中部分下载的 checksum 与 Wiki 上传 metadata 不同，清单会明确记录。

旧素材曲名使用公开映射的精确内部 key 补全。画师帖子中无法直接确认的单图曲名保留原帖与页序，并标记 `unmapped`；原帖分组不自动当作附图的曲包归属。作曲家和画师使用不同字段。

付费画集与游戏只记录购买来源，未下载。失效分享、访问失败、音频波形/谱图、视频和无法确认归属的混合 fanart 站点记录在来源清单中，不作为成功下载的曲绘。

## 目录与复现

```text
assets/
  public/{artists,wikis,archives}/  新增公开素材与参考资料
  legacy/trans/                    原仓库未量化的透明 PNG
  legacy/tiny/                     配对的历史量化副本
  site/{icons,fonts,images,audio}/  图标、字体、背景/截图、音频
src/                               页面 JS/CSS；vendor/ 为原第三方 bundle
templates/slideshow.html           幻灯片模板（生成根 index.html）
scripts/                           抓取、构建与校验；legacy/ 为历史实验脚本
data/sources/                      各来源 manifest、候选与曲名映射
data/catalog.{json,js}              完整来源记录及离线浏览目录
data/legacy-inventory.json          670 个旧文件的迁移路径、Git blob 和 SHA-256
licenses/                          第三方许可文本及素材归属索引
LICENSE / NOTICE                   标准软件许可证与署名入口
archive.html / index.html          两个静态网页入口
site.webmanifest / browserconfig.xml  浏览器配置入口
```

建议 Python 3.10+。安装依赖并构建：

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/build_catalog.py --verify
```

按需刷新公开来源，或继续已保存的 Wiki 候选：

```sh
.venv/bin/python -I scripts/fetch_artists.py --workers 4
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python -I scripts/fetch_archives.py
.venv/bin/python scripts/build_catalog.py --verify
```

如需重新发现 Wiki 条目，去掉 `--resume`。网络来源可能限流或失效；检查各 manifest 中的 `failures`、`status`、`access_status` 和 Git diff 后再提交更新。每个来源脚本的具体限制见上表对应文档。构建统一使用 `scripts/build_catalog.py`；旧根目录 `html.py` 入口已移除。`scripts/legacy/` 中的上游实验脚本仅供历史追溯，不参与当前流程。

离线校验与浏览器验收：

```sh
.venv/bin/python -I tests/test_catalog.py
.venv/bin/python -I tests/test_layout.py
.venv/bin/python -I tests/test_pages.py
.venv/bin/python scripts/build_catalog.py --verify --check
.venv/bin/python scripts/build_legacy_inventory.py
.venv/bin/python -I scripts/fetch_archives.py --verify

# 可选：本机 HTTP 服务运行时，使用已安装的 Chrome 做浏览器验收
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -I tests/browser_smoke.py --browser /usr/bin/google-chrome
```

## 署名

原界面由 Mashiro 设计；本收藏及新增工具由 FridrichMethod 维护。曲绘及其他游戏素材的权利仍属于 Rayark 与对应创作者；仓库代码许可证不为这些媒体赋予新许可。参见 [NOTICE](NOTICE)、[LICENSE](LICENSE)、[第三方许可与素材归属索引](licenses/README.md) 和每张图片的来源记录。
