"""数据库类型适配

PostgreSQL 使用 JSONB（二进制存储，路径提取比 JSON 快一个数量级）；
SQLite（单测环境）回退为通用 JSON。
"""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


def json_type(none_as_null: bool = False):
    return JSON(none_as_null=none_as_null).with_variant(
        JSONB(none_as_null=none_as_null), "postgresql"
    )
