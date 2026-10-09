# 上游历史处理脚本

[English](README.md) · **简体中文**

这里原样保留原仓库 `pil/` 中的三个实验脚本，用于解释继承素材的处理过程：

- `pil.py`：把纯白像素改为透明，并生成与背景合成的图片。
- `pix.py`：单张图片的纯白转透明实验。
- `rgb.py`：使用色彩通道 mask 的实验。

这些文件依赖原作者当时的相对路径（例如 `saurce/`、`bg.png`）和实验输入，其中部分输入未随仓库提供。它们作为历史来源保留，不参与当前抓取或构建，也未在此次整理中运行。

当前图库使用 `python scripts/build_catalog.py --verify` 构建，不对素材重新编码或去背景。旧脚本的原始路径及内容哈希见 [data/legacy-inventory.json](../../data/legacy-inventory.json)。
