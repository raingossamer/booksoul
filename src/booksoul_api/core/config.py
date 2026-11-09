"""服务端自己的配置，从环境变量读，带一份只在本地成立的默认值。

**这里说的密钥是服务端自己的**（JWT 签名用）。用户的模型 API key 根本不在这条路径上：
它只存在用户浏览器的 localStorage，每次提问经 `X-Brain-Key` 头带来，用完即丢——
不进配置文件、不进数据库、不进日志。见 `backend.md`「认证授权」的硬规则。

引擎那边 `config.py` 的老规矩是「配置里写环境变量的名字，不写值」。多用户场景下
环境变量这条路走不通，等价替代就是「服务端压根不持有」。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全部可经 `BOOKSOUL_` 前缀的环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_prefix="BOOKSOUL_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://booksoul:booksoul@127.0.0.1:5432/booksoul"
    # 本地开发的占位值。HS256 要求密钥不短于 32 字节，短了 PyJWT 会告警。
    # 任何外网可达的部署都必须用 BOOKSOUL_SECRET_KEY 覆盖掉它。
    secret_key: str = "dev-only-secret-change-me-before-any-real-deployment"
    access_token_minutes: int = 30  # Spec N4
    refresh_token_days: int = 14  # Spec N4
    library_root: str = "library"
    # Spec N2。前端的上传前校验与后端的流式截断用同一个值，
    # S3 起由 GET /api/formats 一并下发给前端，不让前端自己写死一份。
    max_upload_mb: int = 100
    # 原始上传文件留在这里。重新解析要从它开始，不留就只剩「删掉重传」一条路。
    upload_root: str = "uploads"
    # 解析进度与心跳多久落一次库。与 SSE 的轮询间隔（下面的 job_poll_seconds）
    # 是两个独立的节拍：前者决定数据多新，后者决定推得多勤。
    parse_heartbeat_seconds: float = 0.5
    # 心跳停滞多久算孤儿。单实例下启动清扫不看它（进程刚起来，谁都没在跑），
    # 多实例部署才用得上 —— Grill R7 的尺度裁决推迟到那时。
    parse_orphan_timeout_seconds: int = 120
    # SSE 多久回数据库看一眼。与 parse_heartbeat_seconds 是两个节拍：
    # 前者决定推得多勤，后者决定数据多新。推得比写得勤没有意义。
    job_poll_seconds: float = 0.8
    # 一条解析进度推送最多活多久。第三个节拍，管的是「推到什么时候为止」：一个卡住的
    # 任务不该让这条流一直轮询下去。到点还没有结局就静默收尾，前端接着轮询 GET /api/jobs/{id}。
    job_stream_max_seconds: float = 600

    @property
    def sync_database_url(self) -> str:
        """给 Alembic 用的同步连接串。

        迁移走同步驱动（psycopg2）、应用走异步驱动（asyncpg），是刻意的：
        alembic 的 env.py 写成异步版要多一层 run_sync 包装，而迁移本来就是
        一次性的串行操作，异步在这里一点好处都没有，只有坑。
        """
        return self.database_url.replace("+asyncpg", "+psycopg2")


@lru_cache
def get_settings() -> Settings:
    """进程内单例。配置在运行期不变，没必要每次重读环境。"""
    return Settings()
