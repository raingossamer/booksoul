"""命令行入口。

用标准库 argparse，不引第三方 CLI 框架——解析书本身已经要装几个可选依赖了，
命令行这层没必要再加。
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def _setup_console() -> None:
    """Windows 控制台默认是 GBK，中文书名和正文会直接炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _err(msg: str) -> int:
    sys.stdout.flush()  # 先把已输出的正文刷出去，否则错误会插到前面
    print(f"\n✗ {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- parse


def cmd_parse(args) -> int:
    from . import clean, footnotes, writer
    from .parsers import SUPPORTED, detect_format
    from .parsers import parse as parse_book

    paths: list[str] = []
    for item in args.files:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                full = os.path.join(item, name)
                if os.path.isfile(full):
                    paths.append(full)
        else:
            paths.append(item)

    if not paths:
        return _err("没有找到要解析的文件")

    ok, failed = 0, 0
    for path in paths:
        if not os.path.isfile(path):
            print(f"跳过（不是文件）：{path}")
            continue
        try:
            fmt = detect_format(path)
        except Exception as exc:
            print(f"✗ {os.path.basename(path)}：{exc}", file=sys.stderr)
            failed += 1
            continue

        print(f"\n📖 {os.path.basename(path)}　[{SUPPORTED.get(fmt, fmt)}]")
        try:
            book, parser = parse_book(path, keep_images=not args.no_images)
            clean.drop_duplicate_headings(book)
            n_notes = footnotes.normalize_book(book)
            result = writer.write_book(book, args.out, images_from=parser)
        except Exception as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            failed += 1
            continue

        print(
            f"  书名：{book.display_title()}"
            + (f"　作者：{book.meta.author}" if book.meta.author else "")
        )
        print(f"  产物：{result['目录']}")
        extra = []
        if n_notes:
            extra.append(f"脚注 {n_notes} 条")
        if result["图片数"]:
            extra.append(f"图片 {result['图片数']} 张")
        print(
            f"  统计：{result['章节数']} 节　{result['总字数']:,} 字"
            + ("　" + "　".join(extra) if extra else "")
        )
        for w in result["警告"]:
            print(f"  ⚠ {w}")
        ok += 1

    print(f"\n完成：成功 {ok} 本" + (f"，失败 {failed} 本" if failed else ""))
    return 0 if ok else 1


# ---------------------------------------------------------------- list


def cmd_list(args) -> int:
    from . import library

    books = library.list_books(args.library)
    if not books:
        print(f"书库是空的（{library.root_dir(args.library)}/）")
        print("先解析一本：booksoul parse 你的书.epub")
        return 0

    width = max(len(b["书名"]) for b in books)
    print(f"书库：{library.root_dir(args.library)}/　共 {len(books)} 本\n")
    for b in books:
        author = f"　{b['作者']}" if b["作者"] else ""
        print(f"  {b['书名']:<{width}}{author}　{b['章节数']} 节　{b['总字数']:,} 字")
    return 0


# ---------------------------------------------------------------- brains


def cmd_brains(args) -> int:
    from . import config

    brains = config.available_brains()
    print("可用的大脑（--brain 选一个）：\n")
    for name, cfg in brains.items():
        key_env = cfg.get("api_key_env", "")
        if not key_env:
            status = "本地，无需 key"
        elif os.environ.get(key_env):
            status = f"✓ {key_env} 已设置"
        else:
            status = f"✗ 需要 {key_env}"
        print(f"  {name:<14} {cfg.get('model', '?'):<28} {status}")
        if cfg.get("note"):
            print(f"  {'':<14} {cfg['note']}")
    print(f"\n配置文件：{config.CONFIG_NAME}（用 `booksoul init-config` 生成一份）")
    print("key 从环境变量读，不写进配置文件。")
    return 0


def cmd_init_config(args) -> int:
    from . import config

    path = config.write_example_config(args.path)
    print(f"已生成：{path}")
    print("按需修改 model / base_url，然后设置对应的环境变量即可。")
    return 0


# ---------------------------------------------------------------- ask / chat


def _make_session(args):
    from . import ask, config

    cfg = config.resolve(
        args.brain,
        model=args.model,
        base_url=args.base_url,
        max_tokens=args.max_tokens,
    )
    return ask.open_session(
        args.book,
        persona=args.persona,
        cfg=cfg,
        root=args.library,
        reflection=args.reflect,
        budget_chars=args.budget,
    )


def cmd_ask(args) -> int:
    from .brain import BrainError

    question = " ".join(args.question).strip()
    if not question:
        return _err('请给出问题，例如：booksoul ask "悉达多为什么离开佛陀"')

    try:
        session = _make_session(args)
    except (ValueError, FileNotFoundError, BrainError, RuntimeError) as exc:
        return _err(str(exc))

    print(
        f"《{session.book.display_title()}》　人格：{session.persona.name}　大脑：{session.brain_label}"
    )
    print("─" * 60)

    try:
        reply = session.ask(question, on_text=lambda t: print(t, end="", flush=True))
    except (BrainError, ValueError) as exc:
        return _err(str(exc))

    print("\n" + "─" * 60)
    print(f"{session.selection_note()}　{reply.usage.summary()}")
    return 0


def cmd_chat(args) -> int:
    from .brain import BrainError
    from .persona import PERSONAS

    try:
        session = _make_session(args)
    except (ValueError, FileNotFoundError, BrainError, RuntimeError) as exc:
        return _err(str(exc))

    print(f"《{session.book.display_title()}》")
    print(f"人格：{session.persona.name}　大脑：{session.brain_label}")
    print("命令：/persona <scholar|author|book> 换人格　/reset 清空对话　/quit 退出")
    print("─" * 60)

    while True:
        try:
            line = input("\n你 › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            print("再见。")
            return 0
        if line == "/reset":
            session.reset()
            print("（对话已清空）")
            continue
        if line.startswith("/persona"):
            _, _, key = line.partition(" ")
            key = key.strip()
            if key not in PERSONAS:
                print(f"（可选人格：{'、'.join(PERSONAS)}）")
                continue
            from . import persona as persona_mod

            session.persona = persona_mod.load(key)
            session.reset()
            print(f"（已换成「{session.persona.name}」，对话已清空）")
            continue

        print(f"\n{session.persona.name} › ", end="", flush=True)
        try:
            reply = session.ask(line, on_text=lambda t: print(t, end="", flush=True))
        except (BrainError, ValueError) as exc:
            print()
            _err(str(exc))
            continue
        print(f"\n\n  〔{session.selection_note()}　{reply.usage.summary()}〕")


# ---------------------------------------------------------------- 组装


def build_parser() -> argparse.ArgumentParser:
    from .parsers import SUPPORTED
    from .persona import PERSONAS

    p = argparse.ArgumentParser(
        prog="booksoul",
        description="把书解析成模型读得懂的 Markdown，再让模型扮演它。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  booksoul parse 悉达多.epub              解析一本书进书库\n"
            "  booksoul parse ./books/                 批量解析一个目录\n"
            "  booksoul list                           看看书库里有什么\n"
            '  booksoul ask "悉达多为什么离开佛陀"      问一个问题\n'
            '  booksoul ask -p author "我该怎么选"      让作者回答\n'
            "  booksoul chat -p book --brain deepseek  用 DeepSeek 和这本书聊\n"
        ),
    )
    p.add_argument(
        "-V", "--version", action="version", version=f"booksoul {__version__}"
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("parse", help=f"解析书籍（支持 {'/'.join(SUPPORTED)}）")
    sp.add_argument("files", nargs="+", help="书籍文件或目录")
    sp.add_argument(
        "-o", "--out", default="library", help="输出书库目录（默认 library）"
    )
    sp.add_argument("--no-images", action="store_true", help="不导出图片")
    sp.set_defaults(func=cmd_parse)

    sl = sub.add_parser("list", help="列出书库里的书")
    sl.add_argument("-L", "--library", default="", help="书库目录")
    sl.set_defaults(func=cmd_list)

    sb = sub.add_parser("brains", help="列出可用的模型后端")
    sb.set_defaults(func=cmd_brains)

    sc = sub.add_parser("init-config", help="生成示例配置文件")
    sc.add_argument("path", nargs="?", default="", help="写到哪里")
    sc.set_defaults(func=cmd_init_config)

    def add_chat_opts(sp_) -> None:
        sp_.add_argument(
            "-b", "--book", default="", help="书名（书库里只有一本时可省略）"
        )
        sp_.add_argument(
            "-p",
            "--persona",
            default="scholar",
            choices=list(PERSONAS),
            help="人格：scholar 研读者 / author 作者 / book 书本身",
        )
        sp_.add_argument("--brain", default="", help="模型后端（见 booksoul brains）")
        sp_.add_argument("--model", default="", help="覆盖模型名")
        sp_.add_argument("--base-url", default="", help="覆盖接口地址")
        sp_.add_argument("--max-tokens", type=int, default=0, help="回答长度上限")
        sp_.add_argument(
            "--budget",
            type=int,
            default=0,
            metavar="字数",
            help="最多载入多少字的正文（默认按模型上下文自动定）。"
            "调小会只载入与问题最相关的章节，可用来控制成本，"
            "或绕开供应商内容审核拦下的章节",
        )
        sp_.add_argument(
            "--reflect", action="store_true", help="按「读者带着自己的处境提问」来回应"
        )
        sp_.add_argument("-L", "--library", default="", help="书库目录")

    sa = sub.add_parser("ask", help="问一个问题")
    sa.add_argument("question", nargs="+", help="你的问题")
    add_chat_opts(sa)
    sa.set_defaults(func=cmd_ask)

    sch = sub.add_parser("chat", help="多轮对话")
    add_chat_opts(sch)
    sch.set_defaults(func=cmd_chat)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
