# 许可证与素材归属

[English](README.md) · **简体中文**

| 范围 | 许可证 / 署名入口 |
| --- | --- |
| 项目代码及原始图库代码 | 根目录 [LICENSE](../LICENSE) 保留原 Apache-2.0 文本；维护者和原设计者见 [NOTICE](../NOTICE)。 |
| 游戏内部 key / 曲名映射 | [DeemoSongs-MIT.txt](DeemoSongs-MIT.txt)，来自 syuchan1005/DeemoSongs；文件内容原样保留。 |
| 原界面打包的第三方 JavaScript | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js) 即上游 `src/main.js`，逐字节保留。其中打包了 html2canvas 0.5.0-beta3（MIT，Copyright (c) 2012 Niklas von Hertzen：[html2canvas-MIT.txt](html2canvas-MIT.txt)）、html2canvas 内置的 punycode.js 1.2.4 模块（MIT，Copyright Mathias Bynens：[punycode-MIT.txt](punycode-MIT.txt)）和 Pace 1.0.0（MIT，Copyright (c) 2013 HubSpot, Inc.：[pace-MIT.txt](pace-MIT.txt)）。bundle 本身只有一行许可证名称为空的 html2canvas 头注释和一行 `/*! pace 1.0.0 */`，因此完整许可文本放在本目录，逐字取自对应版本的已发布软件包。 |
| 原仓库曲绘及量化副本 | [assets/legacy/](../assets/legacy/)，素材归属仍以 Rayark 与对应创作者为准；配对版本见 [data/catalog.json](../data/catalog.json)。 |
| 图标、背景、字体、音频及历史截图 | [assets/site/](../assets/site/)，按用途整理；每个文件的创作者与许可见下表，已移除的两款商业字体也列在其中。这些文件不因目录迁移获得新的 Apache 授权。 |
| 新增公开曲绘、扫描与参考资料 | [data/sources/](../data/sources/) 记录具体作者、原帖、文件地址和来源状态。 |

根目录 `LICENSE` 和 `NOTICE` 是整个仓库的标准软件许可与署名入口；此目录集中存放第三方许可文本及范围索引。

[data/legacy-inventory.json](../data/legacy-inventory.json) 记录旧文件路径、新路径、迁移前 Git blob、字节数和 SHA-256，覆盖全部旧曲绘、站点媒体、保留的旧工具及许可文本；已移除的两款字体不再列入。

## 继承的站点素材（assets/site/）

下列文件全部来自 [mashirozx/deemo](https://github.com/mashirozx/deemo) 的上游快照；“继承自”一栏即旧文件清单中的 `original_path`。上游没有记录任何一个文件的来源或许可，因此创作者是根据文件本身（字体内嵌名称、ID3 标签、SVG 元数据、图像内容）和仓库中的 wiki 曲目数据确定的；仍不清楚的创作者或许可，表中直接写明“未知”。Apache-2.0 LICENSE 不适用于其中任何文件。

| 文件 | 继承自 | 用途 | 创作者 / 权利人 | 许可与说明 |
| --- | --- | --- | --- | --- |
| `audio/Riin - Alice good night.mp3` | `src/Riin - Alice good night.mp3` | 幻灯片背景音乐 | DEEMO 曲目《Alice good night》（Epilogue 曲包），DEEMO wiki 署名为 Sleepy WiFi feat. Riin；© Rayark Inc. 及/或原作者（Sleepy WiFi、Riin），未核实。 | 未知许可。ID3 标签（艺术家“Riin”、专辑“最新热歌慢摇102”、网易云音乐“163 key”注释）表明它来自音乐平台下载，而非官方发行；尚未核实该录音是否就是游戏内音轨。 |
| `fonts/Fantique-Four.ttf` | `src/Fantique-Four.ttf` | 幻灯片标题字体（`Fantique`） | Steve Tune，Digital Empires（Copyright Digital Empires Inc 1997） | 共享软件 / 捐赠软件。字体内嵌许可写明“The font is complete as is and you may use it”，未提及再分发。 |
| `icons/android-chrome-192x192.png` | `android-chrome-192x192.png` | Web 应用清单 | DEEMO 应用图标插画（Deemo 与少女），© Rayark Inc. | 无许可。为游戏图标的缩放副本；图标集由谁、从哪张图片制作均无记录。 |
| `icons/apple-touch-icon.png` | `apple-touch-icon.png` | 幻灯片触屏图标与 `og:image` | DEEMO 应用图标插画，© Rayark Inc. | 同上。 |
| `icons/favicon-16x16.png` | `favicon-16x16.png` | 幻灯片网站图标 | DEEMO 应用图标插画，© Rayark Inc. | 同上。 |
| `icons/favicon-32x32.png` | `favicon-32x32.png` | 幻灯片网站图标 | DEEMO 应用图标插画，© Rayark Inc. | 同上。 |
| `icons/favicon.ico` | `favicon.ico` | 档案页网站图标 | DEEMO 应用图标插画，© Rayark Inc. | 同上。 |
| `icons/mstile-150x150.png` | `mstile-150x150.png` | Windows 磁贴（`browserconfig.xml`） | DEEMO 应用图标插画，© Rayark Inc. | 同上。 |
| `icons/safari-pinned-tab.svg` | `safari-pinned-tab.svg` | 幻灯片 Safari 固定标签图标 | 未知。只是一个圆角方形剪影；其元数据说明由 potrace 1.11 描摹生成。 | 未知。 |
| `icons/camera.svg` | `src/camera.svg` | 幻灯片截图按钮 | 未知。路径数据看起来与 Google Material Design 图标中的“photo camera”（Apache-2.0）相同，未经确认。 | 未知。 |
| `icons/github.svg` | `src/github.svg` | 幻灯片 GitHub 链接 | GitHub 标志，为 GitHub, Inc. 的标识和商标；绘制者未知。 | 未知；仅用于链接到 GitHub。 |
| `icons/love.svg` | `src/love.svg` | 未使用 | 未知（心形）。 | 未知。 |
| `icons/pause.svg` | `src/pause.svg` | 幻灯片音乐按钮 | 未知（电视形暂停图标）。 | 未知。 |
| `icons/play.svg` | `src/play.svg` | 幻灯片音乐按钮 | 未知（电视形播放图标）。 | 未知。 |
| `icons/rotate.svg` | `src/rotate.svg` | 未使用 | 未知（屏幕旋转）。 | 未知。 |
| `icons/sand-clock.svg` | `src/sand-clock.svg` | 幻灯片自动播放按钮 | 未知（沙漏）。 | 未知。 |
| `icons/save.svg` | `src/save.svg` | 幻灯片截图保存按钮 | 未知（软盘）。 | 未知。 |
| `icons/weibo.svg` | `src/weibo.svg` | 未使用 | 微博标志，为微博公司（Weibo Corporation）的商标；绘制者未知。 | 未知。 |
| `icons/wordpress.svg` | `src/wordpress.svg` | 未使用 | WordPress 标志，为 WordPress 基金会（WordPress Foundation）的商标；绘制者未知。 | 未知。 |
| `images/bg.png` | `src/bg.png` | 幻灯片页面背景 | 未知（灰色纸张纹理）。 | 未知。 |
| `images/upstream-preview-lower.png` | `src/Capture.png` | 未使用 | Mashiro 上游图库（deemo.shino.cc）的截图：页面设计属于 Mashiro，其中的曲绘 © Rayark Inc. 及各自画师。 | 作为历史记录保留；与 `upstream-preview-upper.png` 逐字节相同。 |
| `images/upstream-preview-upper.png` | `src/Capture.PNG` | 未使用 | 同一张截图。 | 同上。 |

这些 SVG 图标只留下了工具痕迹，没有作者信息。`github`、`love`、`rotate`、`sand-clock`、`save`、`weibo` 和 `wordpress` 使用 Adobe Illustrator 导出时的图层 id `Capa_1`，`save` 和 `sand-clock` 还带有在线图标编辑器的 `data-original` 属性；这类痕迹常见于从 Flaticon 下载的图标，但来源均未确认。`pause` 和 `play` 带有 iconfont.cn 下载文件常见的 `t` 与 `p-id` 属性，时间戳为 2018 年 4 月。如了解其中任何来源，欢迎提交 issue。

### 已移除的字体

| 文件 | 继承自 | 创作者 / 权利人 | 移除原因 |
| --- | --- | --- | --- |
| `fonts/COPRGTL.ttf`（已移除） | `src/COPRGTL.ttf` | Copperplate Gothic Light：data copyright URW Software & Type GmbH，additional data copyright The Font Bureau, Inc.，Copyright 1994 Microsoft Corporation | 商业字体，保留所有权利；没有任何授权允许再分发。 |
| `fonts/RocknRoll_Typo_bold.ttf`（已移除） | `src/RocknRoll_Typo_bold.ttf` | RocknRoll Typo bold：Copyright (c) 2010 by Otto Maurer，经 MyFonts 销售 | 字体内嵌许可禁止以任何方式向第三方提供该字体。 |

两个文件已于 2026 年移除，不再列入旧文件清单，也不再发布到网站。幻灯片保留其 CSS 字体族名称，仅在访客设备已安装这些字体时使用（CSS `local()`），其他访客看到的是备用字体。
