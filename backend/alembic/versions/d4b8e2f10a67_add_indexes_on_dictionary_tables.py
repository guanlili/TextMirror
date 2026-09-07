"""add indexes on dictionary tables

Revision ID: d4b8e2f10a67
Revises: f7a3c1e8d560
Create Date: 2026-09-07

每次审校都要加载用户词库：按 dictionaries.user_id 过滤、按 dictionary_entries.dictionary_id
join、按 whitelist_words.user_id 查放行词。三列均无索引，词条量增大后拖慢每一次校对请求。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4b8e2f10a67'
down_revision: Union[str, None] = 'f7a3c1e8d560'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ('ix_dictionaries_user_id', 'dictionaries', ['user_id']),
    ('ix_dictionary_entries_dictionary_id', 'dictionary_entries', ['dictionary_id']),
    ('ix_whitelist_words_user_id', 'whitelist_words', ['user_id']),
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, columns in INDEXES:
        if name not in [i['name'] for i in inspector.get_indexes(table)]:
            op.create_index(name, table, columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, _ in INDEXES:
        if name in [i['name'] for i in inspector.get_indexes(table)]:
            op.drop_index(name, table_name=table)
