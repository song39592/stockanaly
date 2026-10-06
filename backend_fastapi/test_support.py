"""测试签名必须使用临时配置，不能访问或改写用户的密钥。"""
import os
from unittest.mock import patch
import crypto


def isolate_crypto(case, base):
    for context in (
        patch.object(crypto, "ENV_PATH", base / ".env"),
        patch.object(crypto, "_key", None),
        patch.dict(os.environ, {"DATA_SECRET": "unit-test-secret", "DATA_SECRET_SEALED": ""}),
    ):
        context.start()
        case.addCleanup(context.stop)


def require_data_isolation(store, base):
    """断言 store 实际使用的写入路径落在临时目录内（bug-04 的教训）。

    第 27 项首跑的兼容转发是「复制名字」而非 sys.modules 别名，setUp 在副本上
    重绑 db.DATA_DIR/BARS_DIR，实现模块仍读真实数据盘 —— 测试夹具直接写进了
    用户的生产库（000002 的脏柱就是这么来的，第 33 项之后才被用户撞见）。
    这里用**实现模块自己持有的 db 引用**（store.db）验证，转发方式再怎么变，
    只要隔离失效就当场报错，绝不静默污染。
    """
    from pathlib import Path
    real = Path(store.db.DB_PATH)
    if not str(real).startswith(str(base)):
        raise AssertionError(
            "数据隔离失效：%s 不在临时目录 %s 下 —— 停止测试以免污染真实数据盘"
            % (real, base))
