"""XML 小工具。

电子书的 XML 命名空间是个雷区：NCX 有 `www.daisy.org` 和 `daisy.org` 两种写法，
OPF 有 2007 和更早的版本，还有一堆根本不写命名空间的生成器。与其维护一张命名空间
对照表，不如解析完直接把命名空间全剥掉，之后一律用裸标签名和裸属性名匹配。
"""

from __future__ import annotations

from lxml import etree


def strip_namespaces(root):
    """就地去掉整棵树上元素和属性的命名空间前缀。

    属性也要处理：OPF 的 `opf:role` / `opf:event` / `opf:scheme` 和 EPUB3 nav 的
    `epub:type` 都是带命名空间的属性，剥掉之后才能用 `el.get("role")` 直接取。
    """
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for name in list(el.attrib):
            if "}" in name:
                local = name.split("}", 1)[1]
                if local not in el.attrib:
                    el.attrib[local] = el.attrib[name]
                del el.attrib[name]
    etree.cleanup_namespaces(root)
    return root


def parse_xml(data: bytes, *, strip: bool = True):
    """宽容地解析 XML（坏结构不抛异常，尽量恢复）。"""
    parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
    root = etree.fromstring(data, parser=parser)
    if root is None:
        return None
    return strip_namespaces(root) if strip else root


def find(el, *names):
    """按顺序找第一个匹配的子孙元素，返回元素或 None。

    用 `is not None` 判空——lxml 里没有子节点的元素本身是假值，直接 `or` 串联
    会在"找到了但它是空元素"时静默走到下一个分支。
    """
    if el is None:
        return None
    for name in names:
        found = el.find(name)
        if found is not None:
            return found
    return None


def findall(el, *names) -> list:
    if el is None:
        return []
    for name in names:
        found = el.findall(name)
        if found:
            return found
    return []
