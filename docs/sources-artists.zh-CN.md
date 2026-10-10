# 画师公开来源

[English](sources-artists.md) · **简体中文**

本批次保存 86 张图片，来自 18 个画师公开作品页面，另记录 1 个履历参考页面。采集时间、原始下载 URL、页面 URL、宽高、实际文件格式、SHA-256 和作者信息均在 [`data/sources/artists.json`](../data/sources/artists.json)。所有图片保留下载到的原始 bytes，没有 resize、裁切、调色、去水印或重新编码。

| 画师 / 来源 | 文件数 | 实际下载尺寸与内容 |
| --- | ---: | --- |
| [ころころさん / 鳩紫ぽっぽ](https://kolokolsan.jimdofree.com/仕事の制作物/) | 5 | 3 张 3030×3030；Matricaria 3030×2200；in a cradle 2048×1662 |
| [SnowEgg / 雪歌草](https://www.pixiv.net/en/users/1755315/illustrations/Deemo) | 39 | 32 张曲绘约 1236×1240–1241；7 张 928×928 曲包封面 |
| [Ryori](https://www.pixiv.net/en/users/1400721/illustrations/Deemo) | 20 | 10 张曲绘约 1324–1332px 宽；2 张曲包封面；8 张设计/过程参考 |
| [K@I](https://www.pixiv.net/en/users/93444/illustrations/Deemo) | 8 | 5 张 1500px 宽曲绘展示；3 张长拼图，最大 1417×6288 |
| [Blaze Wu](https://wublaze.tumblr.com/tagged/deemo) | 8 | 5 张曲绘和 1 张曲包封面为 1280×960；2 张 512×1920 长拼图 |
| [Siyouko / 硝子](https://www.pixiv.net/en/artworks/47910628) | 6 | 公开 DEEMO 插画合集，359–886px；并非高清曲绘包，未声称全部属于游戏委托曲绘 |

按用途分类：57 张单曲曲绘、10 张曲包封面、5 张长拼图、8 张设计/过程参考、6 张其他 DEEMO 插画。分类依据原帖上下文及视觉检查；原帖附带的装饰边框、版权字样、草稿和水印全部保留。

## 原始上传与平台预览

Pixiv 图片通过无需登录的公开 `/ajax/illust/{id}` 和 `/pages` 元数据获取 `urls.original`，下载时使用普通页面 Referer。没有使用账号、cookie、登录凭证或付费内容。

Jimdo 页面 lightbox 将部分 PNG 标成 2048px；使用该站公开的 `/transf/none/` 图片路径实际取得 3030px 原始文件。清单同时保存网页公布的 lightbox URL 和 metadata 尺寸，因此 `source_dimensions` 可能小于解码得到的 `width`/`height`。

Tumblr 使用作者公开的 legacy API 所公布的最大图片 URL。这里的 `1280` 是平台规格名称，并不保证每张图片实际宽度为 1280，也不保证拿到画师工作母档；两张早期长拼图实际只有 512px 宽。

Siyouko 的[履历页面](https://princeofglass.blogspot.com/p/blog-page_1146.html)确认其 DEEMO 曲绘、过场动画与人物设计工作，但页面没有可以下载的 DEEMO 图片，因此记为 `reference_only`。已检查上述 Pixiv 作者的 DEEMO 标签列表；无关游戏联动画、周年纪念混合作品以及不在委托曲绘范围的其他帖子没有混入本批次。

## 文件与曲名

图片位于 `assets/public/artists/{artist}/`。Pixiv/Tumblr 文件名保留 post ID 与从零开始的页序号；Jimdo 使用作者明确标出的曲名。之后新增的 Jimdo 图片以曲名 slug 加 Jimdo 图片 ID 命名（曲名不含拉丁字母或数字时为 `jimdo-<图片 ID>`），因此文件名不会为空或重复；已有文件名不变。

不能从原帖直接确认曲名时，`song_titles` 保持空数组且 `mapping_status` 为 `unmapped`。`collection` 表示原帖的曲包分组，不表示每张附图都属于该曲包；尤其 Ryori 在同一帖子中附带多张曲包封面与过程图。长拼图保持一张文件，没有推测裁切范围。

## 重现

环境需要 Python 3.10 或更高版本、`requests`、`beautifulsoup4` 和 `Pillow`。从仓库根目录运行：

```sh
python scripts/fetch_artists.py --workers 4
```

重新运行会检查公开页面元数据，并在本地文件 SHA-256 正确且下载 URL 不变时复用文件。显式增加 `--refresh` 才会重新下载已缓存图片。脚本验证每张图片能够完整解码，以实际格式确定扩展名，按 SHA-256 去重（资源 ID 最小的记录保留文件），所有失败写入 manifest 并返回非零退出状态。图片只从对应平台的图片主机（`i.pximg.net`、`image.jimcdn.com`、`*.media.tumblr.com`）下载，每次重定向都按同一列表检查。

重新运行不会丢弃已校验的记录，也不会删除文件。某张图片的上游字节变化时，已归档文件保留，新版本以 SHA-256 后缀另存；新版本沿用原资源 ID，旧记录保留为 `<ID>@<SHA-256 前 12 位十六进制>`，标记 `upstream_status: superseded` 并以 `superseded_by` 指向新版本。本次运行未能重现的记录也会保留，标记为 `upstream_status: fetch_failed`（该记录或其来源本次失败）或 `removed`（来源读取无误但已不再列出）。

本次下载和独立校验均为 86/86 成功，0 失败，原始图片总计 107,043,058 bytes。图片版权仍属于 Rayark 及相应创作者；本仓库的软件许可证不授予这些图片新的许可。
