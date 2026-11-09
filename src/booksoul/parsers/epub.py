"""EPUB 解析。

不依赖 ebooklib，直接按 EPUB 规范走 zipfile + lxml，原因是要拿到两样 ebooklib
包得不够顺手的东西：

* **spine 顺序**才是真正的阅读顺序，manifest 里的顺序不是；
* **目录里的锚点**。很多中文 epub 把整本书塞进一两个 xhtml，章节靠 `#anchor`
  区分，不按锚点切就会得到一个几十万字的"单章"。

所有 XML 在读入时就剥掉命名空间（见 xmlutil），避免跟各家生成器的命名空间变体较劲。
"""

from __future__ import annotations

import os
import posixpath
import re
import zipfile

from ..html2md import HtmlToMarkdown, _clean_text, _escape
from ..model import Book, BookMeta, Chapter, ProgressFn
from ..xmlutil import find, findall, parse_xml


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


class EpubParser:
    format_name = "epub"

    def __init__(self, path: str, *, keep_images: bool = True) -> None:
        self.path = path
        self.keep_images = keep_images
        self.zf = zipfile.ZipFile(path)
        try:
            self._names = set(self.zf.namelist())
            self.opf_path = self._find_opf()
            self.opf_dir = posixpath.dirname(self.opf_path)
            self.opf = parse_xml(self.zf.read(self.opf_path))
            if self.opf is None:
                raise ValueError("EPUB 的 OPF 文件无法解析")
        except BaseException:
            self.close()  # 半路失败也要放手，见 `close()`
            raise
        self.manifest: dict[str, dict[str, str]] = {}
        self.images: dict[str, str] = {}  # zip 内路径 -> 输出用的相对路径
        self.book = Book()

    def close(self) -> None:
        """放开原件的 zip 句柄。写盘那步导完图片之后调；关过再调无害。

        不关就要等垃圾回收才放手，而解析器与它产出的对象之间有引用环，那要等一阵。
        Windows 上一个还开着的文件删不掉、也换不掉——Web 层重新解析、删书都要动那份原件。
        """
        self.zf.close()

    # ---------- 基础读取 ----------

    def _find_opf(self) -> str:
        if "META-INF/container.xml" in self._names:
            root = parse_xml(self.zf.read("META-INF/container.xml"))
            node = find(root, ".//rootfile")
            if node is not None and node.get("full-path"):
                return node.get("full-path")
        # container.xml 缺失或损坏：在包里找 .opf
        for name in self.zf.namelist():
            if name.lower().endswith(".opf"):
                return name
        raise ValueError("不是有效的 EPUB：找不到 OPF 文件")

    def _abs(self, href: str, base: str = "") -> str:
        """把 OPF/NCX 里的相对 href 解析成 zip 内的绝对路径。"""
        from urllib.parse import unquote

        href = unquote(href.split("#", 1)[0])
        base_dir = posixpath.dirname(base) if base else self.opf_dir
        joined = posixpath.join(base_dir, href) if base_dir else href
        return posixpath.normpath(joined).lstrip("./")

    # ---------- 元信息 ----------

    def _parse_meta(self) -> BookMeta:
        md = find(self.opf, "metadata", ".//metadata")
        meta = BookMeta(source_file=os.path.basename(self.path), source_format="epub")
        if md is None:
            return meta

        meta.title = _text(find(md, "title"))
        meta.language = _text(find(md, "language"))
        meta.publisher = _text(find(md, "publisher"))
        meta.description = _text(find(md, "description"))

        creators = [_text(e) for e in findall(md, "creator")]
        meta.author = "、".join(dict.fromkeys(c for c in creators if c))

        for el in findall(md, "contributor"):
            name = _text(el)
            if not name:
                continue
            if (el.get("role") or "") == "trl":
                meta.translator = name
            elif not meta.contributor:
                meta.contributor = name

        for el in findall(md, "date"):
            value = _text(el)
            if not value:
                continue
            event = el.get("event") or ""
            if event == "publication":
                meta.published = value
                break
            if not meta.published:
                meta.published = value

        for el in findall(md, "identifier"):
            scheme = (el.get("scheme") or "").upper()
            value = _text(el)
            if not value:
                continue
            if scheme == "ISBN" or (
                not meta.isbn and re.fullmatch(r"(?:97[89])?[\d\-]{9,16}[\dxX]", value)
            ):
                meta.isbn = value
        return meta

    # ---------- manifest / spine ----------

    def _parse_manifest(self) -> None:
        node = find(self.opf, "manifest", ".//manifest")
        if node is None:
            raise ValueError("EPUB 缺少 manifest")
        for item in node:
            iid, href = item.get("id"), item.get("href")
            if not iid or not href:
                continue
            self.manifest[iid] = {
                "href": self._abs(href),
                "type": (item.get("media-type") or "").lower(),
                "properties": item.get("properties") or "",
            }

    def _spine_ids(self) -> list[str]:
        node = find(self.opf, "spine", ".//spine")
        if node is None:
            return [i for i, m in self.manifest.items() if "html" in m["type"]]
        out = []
        for ref in node:
            idref = ref.get("idref")
            # linear="no" 多是封面/目录页，仍然保留——书的完整性优先，由目录决定标题
            if idref and idref in self.manifest:
                out.append(idref)
        return out

    def _nav_paths(self) -> set[str]:
        """找出属于"导航构件"而非正文的文档，它们不该进正文。

        目录页混进正文既是噪声（"目录 / Landmarks / Table of Contents"），
        又和我们自己生成的 INDEX.md 重复。三种声明方式都查，因为各版本 EPUB
        和各家转换工具的写法不一样：

        * EPUB3：manifest 里 properties="nav"
        * EPUB2：OPF 的 <guide><reference type="toc">
        * 混合：包声明是 2.0 但文档里写了 <nav epub:type="toc">（从 MOBI 转来的常见）
        """
        paths = {
            m["href"]
            for m in self.manifest.values()
            if "nav" in m["properties"].split()
        }

        guide = find(self.opf, "guide", ".//guide")
        if guide is not None:
            for ref in guide:
                if (ref.get("type") or "").lower() in ("toc", "ncx") and ref.get(
                    "href"
                ):
                    paths.add(self._abs(ref.get("href")))
        return paths

    def _has_toc_nav(self, doc_path: str) -> bool:
        """文档本身是不是一个 <nav epub:type="toc">。"""
        try:
            root = parse_xml(self.zf.read(doc_path))
        except (KeyError, OSError):
            return False
        if root is None:
            return False
        return any((el.get("type") or "").lower() == "toc" for el in root.iter("nav"))

    def _ncx_path(self) -> str:
        spine = find(self.opf, "spine", ".//spine")
        toc_id = spine.get("toc") if spine is not None else None
        if toc_id and toc_id in self.manifest:
            return self.manifest[toc_id]["href"]
        for m in self.manifest.values():
            if m["type"] == "application/x-dtbncx+xml":
                return m["href"]
        return ""

    # ---------- 目录 ----------

    def _parse_toc(self) -> list[tuple[str, str, str, int]]:
        """返回 [(文件路径, 锚点, 标题, 层级)]，按目录顺序。优先 EPUB3 nav。

        顺便记下走的是哪条路线：解析报告里「目录来源」那一行要说清楚，
        因为 nav 和 ncx 切出来的章节边界可能不一样，出了问题这是第一个要看的。
        """
        nav = self._parse_nav()
        if nav:
            self.book.note_stat("目录来源", "EPUB3 nav")
            return nav

        ncx = self._parse_ncx()
        self.book.note_stat(
            "目录来源", "EPUB2 toc.ncx" if ncx else "无目录，按 spine 顺序"
        )
        return ncx

    def _parse_ncx(self) -> list[tuple[str, str, str, int]]:
        path = self._ncx_path()
        if not path or path not in self._names:
            return []
        root = parse_xml(self.zf.read(path))
        nav_map = find(root, "navMap", ".//navMap")
        if nav_map is None:
            return []

        out: list[tuple[str, str, str, int]] = []

        def walk(node, level: int) -> None:
            for point in node:
                if point.tag != "navPoint":
                    continue
                title = _text(find(point, "navLabel/text", ".//navLabel/text"))
                content = find(point, "content")
                src = content.get("src", "") if content is not None else ""
                if title and src:
                    href, _, anchor = src.partition("#")
                    out.append((self._abs(href, path), anchor, title, level))
                walk(point, level + 1)

        walk(nav_map, 1)
        return out

    def _parse_nav(self) -> list[tuple[str, str, str, int]]:
        """EPUB3 的 nav.xhtml。"""
        path = next(
            (
                m["href"]
                for m in self.manifest.values()
                if "nav" in m["properties"].split()
            ),
            "",
        )
        if not path or path not in self._names:
            return []
        root = parse_xml(self.zf.read(path))
        if root is None:
            return []

        nav = next(
            (
                el
                for el in root.iter()
                if el.tag == "nav" and (el.get("type") or "") == "toc"
            ),
            None,
        )
        if nav is None:
            return []

        out: list[tuple[str, str, str, int]] = []

        def walk(ol, level: int) -> None:
            for li in ol:
                if li.tag != "li":
                    continue
                a = find(li, "a", ".//a")
                if a is not None:
                    href, title = a.get("href", ""), _text(a)
                    if title and href:
                        target, _, frag = href.partition("#")
                        out.append((self._abs(target, path), frag, title, level))
                for sub in li:
                    if sub.tag == "ol":
                        walk(sub, level + 1)

        for child in nav.iter("ol"):
            walk(child, 1)
            break
        return out

    # ---------- 图片 ----------

    def _image_resolver(self, doc_path: str):
        def resolve(src: str) -> str:
            if src.startswith(("http://", "https://", "data:")):
                return src
            target = self._abs(src, doc_path)
            if target not in self._names:
                return ""
            rel = f"images/{posixpath.basename(target)}"
            self.images[target] = rel
            return rel

        return resolve

    def extract_images(self, out_dir: str) -> int:
        """把正文里真正引用到的图片写到 out_dir/images/。返回写出的张数。"""
        if not self.images:
            return 0
        os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
        n = 0
        for zip_path, rel in self.images.items():
            try:
                data = self.zf.read(zip_path)
            except KeyError:
                continue
            with open(os.path.join(out_dir, *rel.split("/")), "wb") as f:
                f.write(data)
            n += 1
        return n

    # ---------- 正文 ----------

    def _converter(self, doc_path: str) -> HtmlToMarkdown:
        return HtmlToMarkdown(
            keep_images=self.keep_images,
            image_resolver=self._image_resolver(doc_path) if self.keep_images else None,
        )

    def _split_by_anchors(
        self, doc_path: str, anchors: list[str]
    ) -> list[tuple[str, str]]:
        """按锚点把一个 xhtml 切成若干段，返回 [(锚点, markdown)]。

        第一个锚点之前的内容归到 "" 下。找不到的锚点跳过，不丢内容。
        """
        conv = self._converter(doc_path)
        root = conv._parse(self.zf.read(doc_path))
        if root is None:
            return [("", "")]
        body = find(root, ".//body")
        if body is None:
            body = root

        wanted = [a for a in anchors if a]
        if not wanted:
            return [("", conv._join(conv._blocks(body)))]

        # 为每个顶层子节点算出它归属哪个锚点区间
        top = list(body)
        owner: list[str] = []
        current, found = "", set()
        for child in top:
            ids = {child.get("id")} | {e.get("id") for e in child.iter()}
            hit = next((a for a in wanted if a in ids and a not in found), None)
            if hit:
                current = hit
                found.add(hit)
            owner.append(current)

        if not found:
            return [("", conv._join(conv._blocks(body)))]

        segments: list[tuple[str, str]] = []
        buf: list = []
        cur_key = owner[0]
        # owner 是上面逐个 child append 出来的，与 top 必然等长；
        # strict=True 把这个不变量固化，将来谁改坏了会直接炸而不是静默截断。
        for child, key in zip(top, owner, strict=True):
            if key != cur_key:
                segments.append((cur_key, self._render(conv, buf)))
                buf, cur_key = [], key
            buf.append(child)
        segments.append((cur_key, self._render(conv, buf)))
        return segments

    @staticmethod
    def _render(conv: HtmlToMarkdown, nodes: list) -> str:
        blocks: list[str] = []
        for node in nodes:
            tag = node.tag.lower() if isinstance(node.tag, str) else ""
            if not tag:
                continue
            blocks.extend(conv._block(node, tag))
            if node.tail and node.tail.strip():
                blocks.append(_escape(_clean_text(node.tail)))
        return conv._join(blocks)

    # ---------- 组装 ----------

    def parse(self, *, on_progress: ProgressFn | None = None) -> Book:
        """解析整本。

        `on_progress(done, total)` 按 spine 文件逐个回报——那是这个格式里唯一
        既有意义又数得准的刻度。大部头解析要几十秒，没有刻度用户分不清
        「在干活」和「卡死了」，而这两件事的下一步动作完全不同。
        """
        self.book.meta = self._parse_meta()
        self._parse_manifest()
        spine = self._spine_ids()
        toc = self._parse_toc()

        by_file: dict[str, list[tuple[str, str, int]]] = {}
        for path, anchor, title, level in toc:
            by_file.setdefault(path, []).append((anchor, title, level))

        skip = self._nav_paths()

        chapters: list[Chapter] = []
        self.book.note_stat("spine 文件数", len(spine))
        total = len(spine)
        for done, idref in enumerate(spine, 1):
            # 放在循环体最前面：跳过的文件也要计数，否则 done 追不平 total，
            # 进度条会永远停在 93% 上。
            if on_progress is not None:
                on_progress(done, total)
            item = self.manifest[idref]
            path, mtype = item["href"], item["type"]
            if "html" not in mtype and not path.lower().endswith(
                (".xhtml", ".html", ".htm")
            ):
                continue
            # 没有目录条目、且自己就是一份 toc 导航的文档，跳过。
            # 加"没有目录条目"这个前提是保险：万一某本书的目录真把正文章节指到
            # 了这里，说明它被当成正文用，那就留着。
            if path in skip or (path not in by_file and self._has_toc_nav(path)):
                # 目录页混在 spine 里既是噪声，又和我们自己写的 INDEX.md 重复
                self.book.note_stat("剔除的目录页", 1)
                continue
            if path not in self._names:
                self.book.warn(f"spine 指向的文件不存在，已跳过：{path}")
                continue

            entries = by_file.get(path, [])
            try:
                segments = self._split_by_anchors(path, [a for a, _, _ in entries])
            except Exception as exc:  # 单个文件坏掉不该毁掉整本书
                self.book.warn(
                    f"{path} 解析失败（{type(exc).__name__}: {exc}），已跳过"
                )
                continue

            anchor_meta = {a: (t, lv) for a, t, lv in entries}
            for anchor, md in segments:
                if not md.strip():
                    continue
                title, level = anchor_meta.get(anchor, ("", 0))
                if not title:
                    # 这一段没有目录条目：并入上一章，而不是造一个假标题
                    if chapters:
                        chapters[-1].content = (
                            chapters[-1].content + "\n\n" + md
                        ).strip()
                        continue
                    title, level = self.book.meta.title or "正文", 1
                chapters.append(
                    Chapter(
                        title=title,
                        content=md,
                        level=level,
                        order=len(chapters),
                        source_id=f"{path}#{anchor}" if anchor else path,
                    )
                )

        if not chapters:
            raise ValueError("EPUB 里没有解析出任何正文")

        # 层级归一：目录可能从 2 级起跳，或只用了 1/3 两级
        levels = sorted({c.level for c in chapters})
        remap = {lv: i + 1 for i, lv in enumerate(levels)}
        for c in chapters:
            c.level = remap[c.level]

        self.book.chapters = chapters
        if not toc:
            self.book.warn(
                "EPUB 没有可用目录（NCX/nav），已按 spine 文件顺序合并为单章"
            )
        return self.book


def parse(
    path: str, *, keep_images: bool = True, on_progress: ProgressFn | None = None
) -> tuple[Book, EpubParser]:
    """解析一本 EPUB。交回的解析器还握着 zip 句柄（写盘时要用它导图片），用完调 `close()`。"""
    p = EpubParser(path, keep_images=keep_images)
    try:
        return p.parse(on_progress=on_progress), p
    except BaseException:
        p.close()
        raise
