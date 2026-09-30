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
