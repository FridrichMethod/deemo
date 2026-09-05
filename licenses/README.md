# 许可证与素材归属

| 范围 | 许可证 / 署名入口 |
| --- | --- |
| 项目代码及原始图库代码 | 根目录 [LICENSE](../LICENSE) 保留原 Apache-2.0 文本；维护者和原设计者见 [NOTICE](../NOTICE)。 |
| 游戏内部 key / 曲名映射 | [DeemoSongs-MIT.txt](DeemoSongs-MIT.txt)，来自 syuchan1005/DeemoSongs；文件内容原样保留。 |
| 原界面打包的第三方 JavaScript | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js) 保留原 bundle 和嵌入署名；上游头部有未填写的许可证字段，本次迁移未推断补写。 |
| 原仓库曲绘及量化副本 | [assets/legacy/](../assets/legacy/)，素材归属仍以 Rayark 与对应创作者为准；配对版本见 [data/catalog.json](../data/catalog.json)。 |
| 图标、背景、字体、音频及历史截图 | [assets/site/](../assets/site/)，按用途整理；现有署名说明见 NOTICE，不因目录迁移获得新的 Apache 授权。 |
| 新增公开曲绘、扫描与参考资料 | [data/sources/](../data/sources/) 记录具体作者、原帖、文件地址和来源状态。 |

根目录 `LICENSE` 和 `NOTICE` 是整个仓库的标准软件许可与署名入口；此目录集中存放第三方许可文本及范围索引。

[data/legacy-inventory.json](../data/legacy-inventory.json) 记录旧文件路径、新路径、迁移前 Git blob、字节数和 SHA-256，覆盖全部旧曲绘、站点媒体、保留的旧工具及许可文本。
