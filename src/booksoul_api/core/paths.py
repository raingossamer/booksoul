"""书库里的路径拼接。

只有一条规则，但它值得有自己的文件：**先 resolve，再核对在不在书目录里面。**

反过来做（拼接前检查字符串里有没有 `..`）绕过方式太多，而符号链接在字符串上
根本看不出来。这条规则现在有两处在用（阅读页读正文、溯源比对引文），
抄第二遍就是抄一条安全规则——而抄错的那一份不会报错，只会在某一天读出书目录
外面的文件。
"""

from __future__ import annotations

from pathlib import Path


def under(root: Path | str, *parts: str) -> Path | None:
    """把相对路径接到 `root` 下面。跑出去了返回 None，由调用方决定怎么说。

    路径本身来自数据库里的索引，不是用户直接输入的——但索引是解析器写的，
    而解析器读的是用户传上来的文件。中间隔了几层，不等于不会出事。书里的图更近一层：
    名字直接来自请求路径。

    带空字符的也返回 None：这种路径在操作系统上表示不出来，谈不上「在里面」。
    不先拦下的话，Linux 上 `resolve()` 抛 `ValueError`（一个 500），Windows 上却
    照常返回——同一个请求两个平台两种结局。
    """
    if any("\x00" in part for part in parts):
        return None
    base = Path(root).resolve()
    target = (base / Path(*parts)).resolve()
    return target if target.is_relative_to(base) else None
