"""跨层共用的基础设施：配置、安全原语、错误类型。

这一层不含业务判断，也不 import 引擎——上面三层都可以依赖它。
"""

from __future__ import annotations
