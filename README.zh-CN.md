# DEEMO 1 曲绘档案

[English](README.md) · **简体中文**

[FridrichMethod / deemo](https://github.com/FridrichMethod/deemo) 的个人曲绘收藏，由 FridrichMethod（Zhaoyang Li）维护。收集 DEEMO 原版及相关 Last Recital / Reborn 的公开曲绘；DEEMO II 不在收集范围内。

原项目来自 [mashirozx/deemo](https://github.com/mashirozx/deemo)。上游历史已压缩为一个初始快照；游戏纹理、界面设计署名和 Apache-2.0 软件许可证保留。此版本已替换维护者链接、页面信息和 manifest，移除原站 CNAME、Google Analytics 和社交账号入口。仓库公开，网站通过 GitHub Pages 发布。

## 浏览

在线访问：[可搜索的曲绘档案](https://fridrichmethod.github.io/deemo/archive.html) · [幻灯片](https://fridrichmethod.github.io/deemo/)。仓库、网站和曲绘均公开，无需登录即可访问。

启动仅限本机的静态服务：

```sh
python -I -m http.server 8765 --bind 127.0.0.1
```

打开 [http://127.0.0.1:8765/archive.html](http://127.0.0.1:8765/archive.html)：可搜索曲名、画师与曲包，按来源、类型、尺寸筛选；点击图片查看原文件、SHA-256、来源页面和下载地址。[index.html](index.html) 保留原来的幻灯片风格，已经接入新增素材，也可从档案页跳到指定图片。两页均只加载本地素材，没有统计或远程字体请求。

目录也可直接双击 `archive.html` 离线浏览；HTTP 服务对下载、幻灯片截图等浏览器功能兼容性更好。

两个页面默认显示英文。每页都有语言切换按钮（英文界面显示"中文"，中文界面显示"English"），可在英文与简体中文之间切换。选择保存在浏览器（`localStorage`）和 URL 参数 `?lang=zh-CN` 中；直接打开带 `?lang=zh-CN` 的地址（如 `archive.html?lang=zh-CN`）即显示中文。切换完全在页面内完成，不发起网络请求，直接打开文件（`file://`）时同样可用。曲名、画师名及引用自来源的元数据在两种语言下都按原文显示，只翻译界面文字。来源清单中维护者撰写的说明（文件质量、版式、权利及来源备注）以英文写成，在中文界面中同样以英文显示。

## 网站部署

网站只从发布版部署，即只部署这样的 `vX.Y.Z` 标签：其提交在 `main` 上，且有一个已发布、未标记为预发布的 [GitHub Release](https://github.com/FridrichMethod/deemo/releases)。发布这样的 Release 会运行 [Pages 工作流](.github/workflows/pages.yml)，部署该标签所指的提交，但必须先在同一次运行中让完整的[测试工作流](.github/workflows/tests.yml)在该提交上通过；任何检查失败都不会部署，现有线上版本不变。在这样的标签上手动运行 Pages 工作流（`gh workflow run pages.yml --ref v1.1.2`，或在 Actions 页面选择该标签作为 ref 后点击"Run workflow"）同样会部署该标签所指的提交。每次运行，无论由发布版还是手动触发，都会先向 GitHub API 发出一次只读请求，确认该标签有一个已发布且不是预发布的 Release。重新运行（re-run）沿用被重新运行的那次运行的事件，因此发布版的运行在该 Release 改为草稿或预发布、或被删除之后再重新运行，也会被这项检查拒绝。不包括 Release guard 任务的重新运行（例如只重新运行部署任务，或在测试、构建或部署任务失败后重新运行失败的任务）不会部署任何内容，因为部署任务要求有 Release guard 任务对当前这次尝试（attempt）的记录；应重新运行整次运行。这样的重新运行会失败：其 Re-run check 任务以错误结束，说明这次尝试没有运行 Release guard 任务、没有部署任何内容，必须重新运行全部任务。在运行任何测试之前，工作流会报错拒绝其他一切运行：标记为预发布（prerelease）的发布版、不是 `vX.Y.Z` 的标签（如 `v1.1`、`1.1.0`、`v1.1.0-rc1` 或 `v01.1.0`）、在分支上（包括 `main`）的手动运行、在没有这样的 Release 的标签上的运行（没有 Release、只有草稿或只有预发布），以及提交不在 `main` 上的标签。构建站点的任务在暂存任何文件之前还会把这些检查再做一遍，部署任务也只部署这些检查批准的提交。推送和 PR 永远不会部署，只运行测试工作流：它在每次推送和每个 PR（无论目标分支）上运行，安装 `requirements.txt` 和 `requirements-lint.txt` 后，在 coverage.py 下运行所有 `tests/test_*.py`（它们对 `scripts/` 的覆盖率低于 `pyproject.toml` 中的 `fail_under` 即失败）、`scripts/build_catalog.py --verify --check`（`index.html` 或目录过期即失败）、`scripts/build_legacy_inventory.py` 和 `scripts/fetch_archives.py --verify`；另一个任务用运行器自带的 Chrome 对本地服务运行 `tests/browser_smoke.py`，还有一个 lint 任务对所有文件运行全部 pre-commit 钩子（ruff、mypy、actionlint、ESLint 和空白检查，见下文"开发"）。`.gitattributes` 让继承文件和 `assets/` 下的所有文件不做换行符转换，因此在 Windows 检出（`core.autocrlf=true`）中这些逐字节检查同样通过。

发布时，把 `main` 上的一个提交打上 `vX.Y.Z` 标签（一个 `v` 加三个不带前导零的数字，没有其他内容，如 `v1.2.0` 或 `v1.10.0`，不能是 `v1.02.0`），再为该标签发布一个未标记为预发布的 GitHub Release。仓库规则集（ruleset）"Release tags v*"（Settings → Rules → Rulesets）只允许仓库管理员创建、移动或删除 `v*` 标签，`github-pages` 环境的部署规则只接受 `v1.1.1` 及之后的标签（Settings → Environments）。一次运行使用的是标签所指提交中的 Pages 工作流，因此这些规则使管理员以外的人无法用自己的工作流经 `github-pages` 环境部署。在早于 `v1.1.1` 的标签上的运行会使用该标签自己的工作流，它不会查询 Release，环境会拒绝它的部署。在 `v1.1.1` 上的运行同样使用该标签自己的工作流，其部署任务尚不要求 Release guard 任务对当前这次尝试的记录，因此这样的运行应整次重新运行。当前发布版为 [v1.1.2](https://github.com/FridrichMethod/deemo/releases/tag/v1.1.2)（2026-10-12）。

`scripts/prepare_pages.py` 只复制 Git 跟踪的 `assets/`、`data/`、`src/`、`docs/`、`licenses/` 和明确列出的根目录网页、配置、署名文件；不会发布 `.git/`、工作流、抓取脚本、测试或未跟踪文件。上传目标仅为独立生成的站点目录，不是仓库根目录。白名单只限定网站发布范围，不是敏感内容检测器；仓库本身公开，提交任何文件前仍需检查内容。

页面保留 `noindex,nofollow`，但这不是访问控制。发布包以 950 MB 为硬上限，给 GitHub Pages 的 1 GB 上限留出余量；新增素材超限时部署会停止，现有线上版本不变。`tests/test_pages.py` 还会在每次推送和每个 PR 上用这一上限衡量 Git 跟踪的站点文件，因此会超限的改动在进入 `main` 之前就无法通过检查。保留全部原图字节，不在发布时重新压缩素材。

## 来源更新检查

[检查工作流](.github/workflows/check-sources.yml) 每周一 03:00 UTC 自动运行，也可在 Actions 页手动触发，但只能从 `main` 运行（从其他分支运行会被拒绝）。它以 `--metadata-only` 重新枚举 Fandom 与 BWIKI 的曲绘候选，不下载任何图片，再用 `scripts/check_sources.py` 与 `main` 上的 `data/sources/wiki-discovery.json` 比较。候选集合没有变化时只在运行摘要里记录；若之前运行留下的快照 PR 仍处于打开状态，因其快照已过时，会附评论关闭。出现新增、重新上传（SHA-1 或尺寸变化）或移除的文件时，工作流把新的发现快照和歌曲索引推到 `auto/wiki-source-check` 分支，并创建或更新一个英文标题形如"Wiki source update: N added · M re-uploaded · K removed (YYYY-MM-DD)"的 PR。正文先是英文报告，列出每个文件的来源页、尺寸、曲名和本地是否已有下载；随后在可折叠的"简体中文"区块中附完整的中文报告。文件名、曲名和曲包名由 Wiki 编辑者填写，报告一律以行内代码显示，因此无法向 PR 或运行摘要加入链接、@提及、issue 引用、图片或 HTML。

抓取 Wiki 和安装依赖在只读令牌、不保存凭据的任务中进行。另一个不运行任何第三方代码的任务只接收这两个快照文件，据此重新生成报告、提交并创建 PR；写权限令牌只用于这次推送和 PR 命令。它只更新或关闭本仓库中从 `auto/wiki-source-check` 发起的 PR，无论由机器人还是维护者创建（来自 fork、分支同名的 PR 会被忽略）；若有其他人向该分支提交过，工作流拒绝覆盖它：先合并或转移这些提交，再删除分支。PR 的标题和正文归机器人所有，每次更新都会重写。不合并而关闭 PR 并不能阻止下一次仍发现差异的运行创建新 PR；如需暂停检查，请停用该工作流。GitHub 不会为用 `GITHUB_TOKEN` 创建的 PR 运行工作流，因此测试工作流不会在快照 PR 上运行；该 PR 只改动构建不读取的两个发现文件；合并后测试工作流会在 `main` 上运行全部检查，下一个发布版部署前还会再运行一遍。

合并该 PR 只更新快照，不改变图库。随后在本地运行 `scripts/fetch_wikis.py --resume` 下载新增和重新上传的文件，用 `scripts/build_thumbnails.py --prune` 刷新网格预览图（见下文"网格预览图"），再用 `scripts/build_catalog.py --verify` 构建，运行全部 `tests/test_*.py`，检查 `failures`、`upstream_status` 与 checksum 字段，然后提交图片、预览图、清单与重新生成的目录（命令见 PR 正文和下文"目录与复现"）。`--resume` 不会删除任何文件：已移除和被取代的记录仍保留在清单中，以 `upstream_status` 标记。首次启用前需要在仓库 Settings → Actions → General → Workflow permissions 勾选 "Allow GitHub Actions to create and approve pull requests"，否则工作流能推分支但无法创建 PR。公开仓库 60 天没有提交时 GitHub 会暂停 `schedule` 触发，需在 Actions 页重新启用。

画师来源（`fetch_artists.py`）的 Pixiv 作品 ID 写在脚本里，公开档案来源基本是静态内容，二者都不在自动检查范围内；要补充新作品仍需手动编辑脚本并重新抓取。

## 已整理的来源

| 来源 | 内容与说明 |
| --- | --- |
| [画师本人](docs/sources-artists.zh-CN.md) | 86 张公开上传：ころころさん、SnowEgg、Ryori、K@I、Blaze Wu、Siyouko。包括曲绘、曲包封面、长拼图和单独标记的过程参考。最高单张曲绘为 3030×3030。 |
| [Wiki](docs/sources-wikis.zh-CN.md) | 809 张：Fandom 561、BWIKI 248。包括原版歌曲/曲包图片、不同版本及纹理 atlas，保留实际下载文件与原站 metadata 校验结果。另保存日本 Wiki 的画师目录。 |
| [公开档案](docs/sources-archives.zh-CN.md) | 189 张历史 Tumblr 封面、19 张 OST 包装/内页扫描、4 张官网插画、2 份官方参考 PDF。低分辨率转载与扫描有单独分类。 |
| 原仓库 | `assets/legacy/trans/` 的 320 张素材用于展示；`assets/legacy/tiny/` 的 320 个量化副本作为配对版本，在原图详情中提供下载入口。 |

2026-09-05 共归档 1,057 个公开来源文件；2026-10-08 经来源检查补入 52 张 BWIKI 曲包封面缩略图，公开来源文件合计 1,109 个。另保留原仓库 320 张图片及其 320 个量化副本。量化副本记录在 `variants` 中，不重复计入图库张数。精确去重数量与获取状态以 [data/catalog.json](data/catalog.json) 为准。图片数包括同曲不同版本、曲包封面及参考资料，**不等于独立歌曲数，也不代表全曲母图已齐全**。字节完全相同的文件合并展示，同时保留所有来源；没有按尺寸或文件大小盲目覆盖其他版本。

所有下载均保留响应的原始 bytes，没有 AI 放大、裁切、去水印、去背景或格式转换。`original` 表示站点提供的原始下载规格，不自动等同于画师工作母档。Fandom 中部分下载的 checksum 与 Wiki 上传 metadata 不同，清单会明确记录。

旧素材曲名使用公开映射的精确内部 key 补全；没有精确 key 时，使用唯一一个仅大小写不同的 key，并标记为 `mapped_case_insensitive_internal_key`。映射中完全没有的纹理，若有文件 key 相同（忽略大小写、空格和标点，如 `samsara105_fc` 与 Fandom 的 `Samsara105 fc.png`）的 Wiki 单曲图上传且其映射的歌曲一致，则采用这些歌曲，并标记为 `mapped_wiki_file_key`（6 张纹理）。画师帖子中无法直接确认的单图曲名保留原帖与页序，并标记 `unmapped`；原帖分组不自动当作附图的曲包归属。作曲家和画师使用不同字段。同一曲包在所有来源中只显示一种拼写：Wiki 记录共用的拼写（见 [docs/sources-wikis.zh-CN.md](docs/sources-wikis.zh-CN.md)）；没有 Wiki 记录提及的曲包，取其各拼写中按码位排序的第一个。拼写不同的记录（如歌曲映射中的 `Etude collection`）把自己的拼写保留在 `collection_aliases` 中，档案搜索会匹配它；原帖分组按原帖的写法引用。

付费画集与游戏只记录购买来源，未下载。失效分享、访问失败、音频波形/谱图、视频和无法确认归属的混合 fanart 站点记录在来源清单中，不作为成功下载的曲绘。

## 目录与复现

```text
assets/
  public/{artists,wikis,archives}/  新增公开素材与参考资料
  legacy/trans/                    原仓库未量化的透明 PNG
  legacy/tiny/                     配对的历史量化副本
  thumbs/                          派生的 WebP 网格预览图（不是存档文件）
  site/{icons,fonts,images,audio}/  图标、字体、背景/截图、音频
src/                               页面 JS/CSS；vendor/ 为原第三方 bundle
src/i18n.js / src/i18n/            语言切换脚本与中英文界面文案
templates/slideshow.html           幻灯片模板（生成根 index.html）
scripts/                           抓取、构建与校验；legacy/ 为历史实验脚本
data/sources/                      各来源 manifest、候选与曲名映射
data/catalog.{json,js}              完整来源记录及离线浏览目录
data/legacy-inventory.json          668 个旧文件的迁移路径、Git blob 和 SHA-256
licenses/                          第三方许可文本及素材归属索引
*.zh-CN.md                         README、docs/ 等文档的简体中文版；同名 .md 为默认英文版
LICENSE / NOTICE                   标准软件许可证与署名入口
archive.html / index.html          两个静态网页入口
site.webmanifest / browserconfig.xml  浏览器配置入口
```

需要 Python 3.10 或更高版本（锁定版本的 Pillow 与 requests 要求如此）。安装依赖并构建：

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
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
```

如需重新发现 Wiki 条目，去掉 `--resume`。网络来源可能限流或失效；检查各 manifest 中的 `failures`、`status`、`access_status` 和 Git diff 后再提交更新。每个来源脚本的具体限制见上表对应文档。构建统一使用 `scripts/build_catalog.py`；旧根目录 `html.py` 入口已移除。`scripts/legacy/` 中的上游实验脚本仅供历史追溯，不参与当前流程。

离线校验与浏览器验收：

```sh
# 与 CI 相同，运行全部离线测试文件（CI 还会测量覆盖率并运行 lint，见下文"开发"）
for test in tests/test_*.py; do .venv/bin/python -I "$test" || echo "FAILED: $test"; done
.venv/bin/python scripts/build_catalog.py --verify --check
.venv/bin/python scripts/build_legacy_inventory.py
.venv/bin/python -I scripts/fetch_archives.py --verify

# 可选：上文的本机 HTTP 服务运行时，使用已安装的 Chrome 做浏览器验收
# （约一分半钟；出现任何控制台错误或失败的请求也会判为失败）
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -I tests/browser_smoke.py --base http://127.0.0.1:8765 --browser /usr/bin/google-chrome
```

## 开发

代码质量工具与构建依赖一样锁定版本：ruff、mypy、coverage.py 和 pre-commit 在 `requirements-lint.txt` 中，ESLint 在 `package.json` 和 `package-lock.json` 中，每个 pre-commit 钩子在 `.pre-commit-config.yaml` 中固定到完整的提交 SHA。它们的设置在 `pyproject.toml`（ruff、mypy、coverage.py）和 `eslint.config.mjs` 中；`tests/test_tooling.py` 检查这些锁定版本彼此一致，且每个工具都跳过本节末尾列出的字节锁定文件和生成文件。把它们安装到上文的虚拟环境中；ESLint 需要 Node.js 20.19 或更高的 20.x、22.13 或更高的 22.x，或 24 及更高版本（`package.json` 中的 `engines`）：

```sh
.venv/bin/python -m pip install -r requirements-lint.txt
npm ci --ignore-scripts
```

`pre-commit run --all-files` 对所有文件运行全部钩子，与测试工作流的 lint 任务相同：空白与文件检查、`ruff check`、`ruff format`、mypy、对工作流运行的 actionlint，以及 ESLint。需要改写文件的钩子（行尾空白、缺少的末尾换行、格式化）会使运行失败，提交前请先检查它做的改动。`pre-commit install` 会在你自己的克隆中于每次 `git commit` 时运行这些钩子，可按需选用。每个工具也可以单独运行：

```sh
.venv/bin/pre-commit run --all-files
.venv/bin/ruff check
.venv/bin/ruff format
.venv/bin/mypy
npx --no-install eslint --max-warnings=0 src
```

ruff 采用 `pyproject.toml` 中第一阶段的规则（语法与运行时错误、pyflakes、pycodestyle 警告、导入顺序、bugbear、pyupgrade 和 pylint 错误），行长 120，按 Python 3.10 语法检查；`ruff format --check` 只报告 `ruff format` 会做的改动。mypy 分阶段引入：第一阶段以非严格模式检查 `pyproject.toml` 的 `[tool.mypy]` 列出的五个构建与发布脚本（`scripts/` 下的 `build_catalog.py`、`build_legacy_inventory.py`、`build_thumbnails.py`、`check_sources.py` 和 `prepare_pages.py`）；之后的阶段依次加入抓取脚本（`scripts/fetch_*.py`）、测试，再启用更严格的选项。不带参数运行 mypy，它就恰好检查这份列表。ESLint 只做检查，切勿使用 `--fix`：`src/` 按提交时的原样发布，且多项测试会比较其中部分内容的确切文本，因此发现的问题要在经过审查的改动中手工修正；`src/vendor/` 不做检查。

测试工作流在 coverage.py 下运行单元测试，测试以子进程启动的每个脚本也一并测量；它们对 `scripts/` 的覆盖率低于 `pyproject.toml` 中的 `fail_under` 时即失败。该阈值是实测基线向下取整：覆盖率提高后上调它，切勿为了通过而调低。在仓库根目录本地测量覆盖率：

```sh
for test in tests/test_*.py; do .venv/bin/python -I -m coverage run "$test"; done
.venv/bin/python -I -m coverage combine
.venv/bin/python -I -m coverage report
```

只改动代码格式的提交记录在 `.git-blame-ignore-revs` 中。在你的克隆中运行一次 `git config blame.ignoreRevsFile .git-blame-ignore-revs`，`git blame` 就会把每一行归于写下它的改动，而不是格式化提交。

以下路径不会被任何工具读取或改写。`.pre-commit-config.yaml` 的全局 `exclude` 让所有钩子跳过它们，`pyproject.toml` 中的 `extend-exclude` 让 ruff 即使在显式传入路径时也不处理它们，`eslint.config.mjs` 忽略 `src/vendor/`，`.editorconfig` 则为它们关闭编辑器的规范化设置。

- `data/legacy-inventory.json` 锁定其字节（SHA-256 与 Git blob）的继承文件：`LICENSE`、`licenses/DeemoSongs-MIT.txt`、`src/vendor/legacy-ui.js`、`scripts/legacy/*.py`、`assets/legacy/` 和 `assets/site/`。出于同样的原因，`.gitattributes` 让它们不做换行符转换。
- `assets/public/` 下下载的原图，来源清单记录了它们的 SHA-256。
- 生成的文件：`index.html`、`data/catalog.json`、`data/catalog.js`、`data/thumbs.json`、`data/legacy-inventory.json` 和 `assets/thumbs/` 中的预览图，以及抓取脚本写入的 `data/sources/` 清单。请用对应的脚本重新生成它们，而不是手工编辑。
- 许可文本（`LICENSE`、`licenses/*.txt`）按原文保留，第三方 bundle `src/vendor/legacy-ui.js` 保持上游发布时的原样。

## 网格预览图

图库网格显示较小的派生预览图，而不是完整原图。在默认视图下实测首次加载：1440×900 窗口的首屏获取 25 张预览图（0.69 MB），375 px 宽的手机获取 8 张（0.27 MB），而同样卡片的原图分别为 39 MB 和 20 MB；更大的桌面窗口（1920×1080 和 2560×1440）获取 36 张预览图（0.98 MB）。`scripts/build_thumbnails.py` 为长边超过 480 px 的每张图库图片生成一张 WebP 预览图（长边 480 px、质量 75、不含 EXIF 和 XMP；保留内嵌的 RGB 色彩配置文件，灰度配置文件会转换为 sRGB，16 位灰度图缩放为 8 位），写入 `assets/thumbs/`，以原图 SHA-256 的前 16 位十六进制命名，并记录在 `data/thumbs.json` 中；随后 `scripts/build_catalog.py` 把预览图作为 `thumb` 字段写入对应的目录条目。预览图只是显示用的副本，不是存档文件：原图保持字节不变，查看器、下载链接和原图链接始终使用原图。较小的图片和 PDF 没有预览图；预览图加载失败时，卡片会改用原图。预览图（约 33 MB）随网站一起发布，计入 950 MB 的 Pages 上限。

增删原图后，先重建预览图，再构建目录，然后检查：

```sh
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I tests/test_thumbnails.py
# 可选，较慢：从原图重新编码每张预览图
.venv/bin/python -I scripts/build_thumbnails.py --verify
```

使用 `requirements.txt` 中固定的 Pillow 版本时，重建会写出逐字节相同的文件；`scripts/build_thumbnails.py --verify` 会从原图重新编码每张预览图并加以确认，而 `scripts/build_catalog.py --verify` 只检查记录中的每张预览图存在且大小与 SHA-256 符合记录。`scripts/build_thumbnails.py --check`（`tests/test_thumbnails.py` 也会运行）确认每张较大的图库图片都有预览图，且每张预览图存在、可解码、尺寸和哈希符合记录，也没有孤立文件。`scripts/build_thumbnails.py --prune` 删除原图已不在图库中的预览图。脚本不会混用不同的编码设置或 Pillow 版本；`scripts/build_thumbnails.py --rebuild` 会重新编码全部预览图。遇到无法如实预览的色彩模式或配置文件（如 CMYK）时，脚本会报错停止，而不是写出颜色错误的预览图。

## 署名

原界面由 Mashiro 设计；本收藏及新增工具由 FridrichMethod 维护。曲绘及其他游戏素材的权利仍属于 Rayark 与对应创作者；仓库代码许可证不为这些媒体赋予新许可。参见 [NOTICE](NOTICE)、[LICENSE](LICENSE)、[第三方许可与素材归属索引](licenses/README.zh-CN.md) 和每张图片的来源记录。

[许可索引](licenses/README.zh-CN.md)同样涵盖第三方代码与素材。原界面打包的 JavaScript（`src/vendor/legacy-ui.js`）包含 html2canvas、punycode.js 和 Pace，均采用 MIT 许可，许可文本见 [html2canvas-MIT.txt](licenses/html2canvas-MIT.txt)、[punycode-MIT.txt](licenses/punycode-MIT.txt) 和 [pace-MIT.txt](licenses/pace-MIT.txt)。索引还列出了 `assets/site/` 下每个继承的图标、字体、图片和音频文件的已知创作者与许可，并写明哪些仍不清楚。上游继承的两款商业字体 Copperplate Gothic Light 和 RocknRoll Typo bold 的许可禁止再分发，因此不再随仓库发布；幻灯片仅在访客设备已安装这些字体时使用它们。捐赠软件字体 Fantique Four 仍保留。
