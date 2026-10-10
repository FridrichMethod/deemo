# 许可证与素材归属

[English](README.md) · **简体中文**

| 范围 | 许可证 / 署名入口 |
| --- | --- |
| 项目代码及原始图库代码 | 根目录 [LICENSE](../LICENSE) 保留原 Apache-2.0 文本；维护者和原设计者见 [NOTICE](../NOTICE)。 |
| 游戏内部 key / 曲名映射 | [DeemoSongs-MIT.txt](DeemoSongs-MIT.txt)，来自 syuchan1005/DeemoSongs；文件内容原样保留。 |
| 原界面打包的第三方 JavaScript | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js) 即上游 `src/main.js`，逐字节保留。其中打包了 html2canvas 0.5.0-beta3（MIT，Copyright (c) 2012 Niklas von Hertzen：[html2canvas-MIT.txt](html2canvas-MIT.txt)）、html2canvas 内置的 punycode.js 1.2.4 模块（MIT，Copyright Mathias Bynens：[punycode-MIT.txt](punycode-MIT.txt)）和 Pace 1.0.0（MIT，Copyright (c) 2013 HubSpot, Inc.：[pace-MIT.txt](pace-MIT.txt)）。bundle 本身只有一行许可证名称为空的 html2canvas 头注释和一行 `/*! pace 1.0.0 */`，因此完整许可文本放在本目录，逐字取自对应版本的已发布软件包。 |
| 原仓库曲绘及量化副本 | [assets/legacy/](../assets/legacy/)，素材归属仍以 Rayark 与对应创作者为准；配对版本见 [data/catalog.json](../data/catalog.json)。 |
| 图标、背景、字体、音频及历史截图 | [assets/site/](../assets/site/)，按用途整理；现有署名说明见 NOTICE，不因目录迁移获得新的 Apache 授权。 |
| 新增公开曲绘、扫描与参考资料 | [data/sources/](../data/sources/) 记录具体作者、原帖、文件地址和来源状态。 |

根目录 `LICENSE` 和 `NOTICE` 是整个仓库的标准软件许可与署名入口；此目录集中存放第三方许可文本及范围索引。

[data/legacy-inventory.json](../data/legacy-inventory.json) 记录旧文件路径、新路径、迁移前 Git blob、字节数和 SHA-256，覆盖全部旧曲绘、站点媒体、保留的旧工具及许可文本。
