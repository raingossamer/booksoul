"""大脑配置：接哪个 API、用哪个 key、跑哪个模型。

设计上只有一条硬规则：**api key 不进配置文件**。配置里写的是环境变量名
（api_key_env），真正的 key 从环境读。这样 booksoul.toml 可以安心提交到仓库、
可以贴给别人看。

配置查找顺序（后面的覆盖前面的）：
  1. 内置预设 PRESETS
  2. ~/.booksoul/config.toml
  3. 项目根的 booksoul.toml
  4. 环境变量 BOOKSOUL_BRAIN / BOOKSOUL_MODEL / BOOKSOUL_BASE_URL / BOOKSOUL_API_KEY
  5. 命令行参数
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

# 内置预设。模型名会随供应商更新而变化，这里只是开箱可用的默认值，
# 随时可以用 --model 或配置文件覆盖。
PRESETS: dict[str, dict[str, str]] = {
    "claude": {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "note": "Anthropic 官方 SDK，1M 上下文，支持 prompt 缓存",
    },
    "deepseek": {
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "note": "深度求索",
    },
    "kimi": {
        "provider": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-128k",
        "api_key_env": "MOONSHOT_API_KEY",
        "note": "月之暗面，长上下文适合整本书",
    },
    "glm": {
        "provider": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "api_key_env": "ZHIPU_API_KEY",
        "note": "智谱 GLM",
    },
    "qwen": {
        "provider": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "note": "阿里通义千问（DashScope 兼容模式）",
    },
    "siliconflow": {
        "provider": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "api_key_env": "SILICONFLOW_API_KEY",
        "note": "硅基流动，一个 key 通多家开源模型",
    },
    "openai": {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "note": "OpenAI 官方",
    },
    "ollama": {
        "provider": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:14b",
        "api_key_env": "",
        "note": "本地 Ollama，不需要 key",
    },
    "lmstudio": {
        "provider": "openai",
        "base_url": "http://localhost:1234/v1",
        "model": "local-model",
        "api_key_env": "",
        "note": "本地 LM Studio，不需要 key",
    },
}

DEFAULT_BRAIN = "claude"

CONFIG_NAME = "booksoul.toml"


@dataclass
class BrainConfig:
    name: str = DEFAULT_BRAIN
    provider: str = "anthropic"  # anthropic | openai
    model: str = "claude-opus-5"
    base_url: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    # 解析后的真实 key。不落盘，也**不进 repr**——`repr()` 会在四个地方被自动调用：
    # 日志里的 %r、异常回溯里的局部变量、pytest 断言失败时打印的对象、调试器的变量面板。
    # 每一处都是「没人打算打印 key」却把 key 打印了出来。
    api_key: str = field(default="", repr=False)
    max_tokens: int = 8000
    temperature: float | None = None
    note: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.api_key_env)

    def describe(self) -> str:
        where = self.base_url or "官方端点"
        return f"{self.name}（{self.provider} · {self.model} · {where}）"


def _project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _config_paths() -> list[str]:
    return [
        os.path.join(os.path.expanduser("~"), ".booksoul", "config.toml"),
        os.path.join(_project_root(), CONFIG_NAME),
        os.path.join(os.getcwd(), CONFIG_NAME),
    ]


def _load_files() -> tuple[dict, str]:
    """合并所有存在的配置文件，返回 (数据, 默认大脑名)。"""
    merged: dict[str, dict] = {}
    default = ""
    for path in _config_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise RuntimeError(f"配置文件读取失败 {path}：{exc}") from exc
        default = data.get("default", default)
        for name, cfg in (data.get("brains") or {}).items():
            merged.setdefault(name, {}).update(cfg)
    return merged, default


def available_brains() -> dict[str, dict]:
    """预设 + 配置文件里定义的全部大脑。"""
    out = {k: dict(v) for k, v in PRESETS.items()}
    files, _ = _load_files()
    for name, cfg in files.items():
        out.setdefault(name, {}).update(cfg)
    return out


def resolve(
    name: str = "",
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    max_tokens: int = 0,
) -> BrainConfig:
    """定下这次用哪个大脑。命令行参数优先级最高。"""
    files, file_default = _load_files()
    name = name or os.environ.get("BOOKSOUL_BRAIN", "") or file_default or DEFAULT_BRAIN

    raw = dict(PRESETS.get(name, {}))
    raw.update(files.get(name, {}))
    if not raw:
        known = "、".join(sorted(set(PRESETS) | set(files)))
        raise ValueError(f"没有名为 {name!r} 的大脑。已知：{known}")

    cfg = BrainConfig(
        name=name,
        provider=raw.get("provider", "openai"),
        model=raw.get("model", ""),
        base_url=raw.get("base_url", ""),
        api_key_env=raw.get("api_key_env", ""),
        max_tokens=int(raw.get("max_tokens", 8000)),
        temperature=raw.get("temperature"),
        note=raw.get("note", ""),
    )

    # 环境变量覆盖
    cfg.model = model or os.environ.get("BOOKSOUL_MODEL", "") or cfg.model
    cfg.base_url = base_url or os.environ.get("BOOKSOUL_BASE_URL", "") or cfg.base_url
    if max_tokens:
        cfg.max_tokens = max_tokens

    # key：命令行 > BOOKSOUL_API_KEY > 该大脑自己的环境变量
    cfg.api_key = (
        api_key
        or os.environ.get("BOOKSOUL_API_KEY", "")
        or (os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else "")
    )

    if not cfg.model:
        raise ValueError(
            f"大脑 {name!r} 没有指定 model，用 --model 指定或写进 {CONFIG_NAME}"
        )
    return cfg


def write_example_config(path: str = "") -> str:
    """生成一份带注释的示例配置。"""
    path = path or os.path.join(_project_root(), CONFIG_NAME)
    lines = [
        "# booksoul 大脑配置",
        "#",
        "# api key 不要写在这里——写环境变量名（api_key_env），key 从环境读。",
        "# 这样这个文件可以安心提交到仓库。",
        "",
        f'default = "{DEFAULT_BRAIN}"',
        "",
    ]
    for name, cfg in PRESETS.items():
        lines.append(f"# {cfg.get('note', '')}")
        lines.append(f"[brains.{name}]")
        for key in ("provider", "base_url", "model", "api_key_env"):
            if key in cfg:
                lines.append(f'{key} = "{cfg[key]}"')
        lines.append("")
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
