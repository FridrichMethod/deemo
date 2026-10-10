# 公开档案与官方参考资料

[English](sources-archives.md) · **简体中文**

本目录对应 `data/sources/archives.json`；抓取脚本为 `scripts/fetch_archives.py`。文件保留服务端响应的原始 bytes，没有缩放、转码、锐化或 AI 放大。扩展名按实际解码格式保存，每张图片记录页面、下载 URL、最终 URL、尺寸、SHA-256、字节数和抓取时间。

2026-09-05 实际取得 **214 个文件，51.2 MB**，其中 212 张图片、2 个 PDF。此处覆盖 DEEMO 1 及相关 Last Recital 旧曲，不收集 DEEMO II。

| 来源 | 已下载 | 实测内容与适用范围 |
|---|---:|---|
| [Cover Art Archive](https://musicbrainz.org/release/fb7dec3c-01cd-4659-a398-e4494b824db4/cover-art) | 19 | 原始上传的 OST 包装/内页扫描。封面 2000²，内页主要约 2180×1100；保留双页、裁切和扫描痕迹，归类为 reference。不是独立曲绘母图。 |
| [Tunes of Rayark](https://rayarkmusic.tumblr.com/deemo) | 189 | 32 曲包及 Alice/Celia 标签全部翻页到终点，共 189 个曲目帖。164 张 500²，其余更小；属于 community_repost。原贴可能含字体、游戏背景或二次修图，不能当作高清替换优先源。collection 30、31 当前为 0 帖。 |
| [DEEMO 官网](https://deemo.com/) | 4 | 官网人物/场景 key art，约 798–985px 宽、901–1431px 高，归类为 illustration。官网没有全曲绘下载库。 |
| [展览英文导览](https://rayark.promo/deemo_exhibition_en.pdf) | 1 | 官方 PDF，reference，不混入曲绘轮播。 |
| [Rayark Game Brand Assets](https://rayark.promo/rayark_site/RAYARK_GameBrandAssets.pdf) | 1 | 官方品牌资料 PDF，reference。 |

[Tunes of Rayark FAQ](https://rayarkmusic.tumblr.com/faq) 明示该博客并非官方，并说明作者曾制作 cleaned artwork。FAQ 指向的 [Kitsunefreak `my edit` 标签](https://kitsunefreak.tumblr.com/tagged/my%20edit)目前公开 API 返回 0 帖；没有把空结果写成成功取得修图。这里只抓取歌曲帖中的静态封面图片，没有下载任何音乐。

其他来源的处理记录均可在 JSON 中按 `status` / `access_status` 查阅：

- KADOKAWA/BookWalker 官方画集、Steam Reborn 标记 `purchase_required`，未购买、未下载游戏或付费书籍。
- Reddit 3.4/4.x/5.x 仅为历史分享线索：前次搜索证据指向 Wikia 转存或放大图及已下线的分享。讨论页面仍可打开不代表共享文件存活。
- [历史 Dropbox](https://www.dropbox.com/sh/qdzxn3menwuk5he/AAAL2v6303lMlAf1ma54tJjga?dl=0) 实测 HTTP 200，但页面内容是 `Dropbox - Error`，未取得共享文件。
- 原 repo 的[提取过程博客](https://2heng.xin/2018/04/05/python-pil/)与旧百度分享保留为 provenance；百度页面可打开，但没有验证匿名直接文件下载，也没有把旧解包资源误称为新增高清源。
- [Internet Archive 202606 项目](https://archive.org/details/deemo_ost-_202606)列出 443 个 FLAC、443 个 MP3、443 个 PNG、443 个 spectrogram。普通 PNG 抽样实际是 800×200 的音频波形；音频描述也把嵌入封面归源至 DEEMO Wiki。因此不下载音频或波形/频谱图。
- Zerochan/Safebooru 是混合 fanart、官方图、裁切和再上传的索引，没有核实独立新增的官方曲绘，未把整站图片当母图批量导入。
- Tumgik 第三方镜像与 SteamDB 当前 HTTP 403；前者所指的 Blaze Wu 作品由画师来源 importer 抓取原始 Tumblr 页面。
- 日文 DEEMO Wiki 画师目录归 Wiki importer 管理（`wikis:wikiwiki-illustrators`），此处条目标记为 `delegated`。
- Bilibili 视频检索保留为线索，视频帧经过视频编码，不作为原始散图来源。

这些来源的可访问性和著作权是两件事。这里保留原作者与 Rayark 归属，仅记录公开来源，不把维护者身份或仓库代码许可证套用于曲绘。

复现抓取与离线核验：

```sh
python -I scripts/fetch_archives.py
python -I scripts/fetch_archives.py --verify
```

需要 Python 3.10 或更高版本；依赖为 `requests`、`beautifulsoup4`、`Pillow`；`-I` 使用 Python 隔离模式。抓取器按 3–5 个线程并行请求，遇到失败会以真实来源 ID 与 URL 写入 `failures`；有失败的来源标记为 `partial` 或 `failed`，命令以非零状态退出。Tumblr 每个标签按 API `posts-total` 分页，不设置静默截断上限。下载和页面读取只发往预期主机（Cover Art Archive/archive.org、Tumblr、deemo.com、rayark.promo），每次重定向都按同一列表检查；对其余目录页面的可达性检查只记录 HTTP 状态和最终 URL，不保存任何内容。文件按内容校验后写入；远端内容如变化，旧文件保留并以 hash 后缀保存新版本。manifest 在运行结束时一次性原子写入。重新运行不会丢弃已校验的记录，也不会删除文件：变化文件的新版本沿用资源 ID，旧记录保留为 `<ID>:<SHA-256 前 12 位十六进制>` 并标记 `upstream_status: superseded`；未能重现的记录保留为 `fetch_failed` 或 `removed`。上游之后改回较早版本时，该版本重新成为当前记录，不会重复列出。唯一的例外是检出中已缺失的归档文件：重新运行会把当前上游字节保存到该路径，旧记录的字节已不存在，因此不再保留。若这些字节已以 hash 后缀保存在旁边，则沿用该副本，旧记录保留；此时 `--verify` 会报告缺失的文件，直到它被恢复（例如从 git 恢复）。
