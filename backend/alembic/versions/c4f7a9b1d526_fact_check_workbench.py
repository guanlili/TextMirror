"""Independent fact-check runs, confirmation and append-only reviews."""
import sqlalchemy as sa

from alembic import op

revision = "c4f7a9b1d526"
down_revision = "b3e6f8a0c415"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("fact-check", "事实核查", "menu", "/fact-check"),
    ("fact-check:run", "运行事实核查", "button", None),
    ("fact-check:review", "复核事实结论", "button", None),
    ("fact-check:export", "导出核查报告", "button", None),
]


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("fact_check_runs")}
    if "confirm_claims" not in columns:
        naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
        foreign_key = next((fk for fk in inspector.get_foreign_keys("fact_check_runs") if fk["constrained_columns"] == ["record_id"]), None)
        if foreign_key is None:
            raise RuntimeError("Cannot find foreign key on fact_check_runs.record_id")
        with op.batch_alter_table("fact_check_runs", naming_convention=naming) as batch:
            batch.drop_constraint(foreign_key["name"] or "fk_fact_check_runs_record_id_proofread_records", type_="foreignkey")
            batch.alter_column("record_id", existing_type=sa.Integer(), nullable=True)
            batch.create_foreign_key("fk_fact_check_record", "proofread_records", ["record_id"], ["id"], ondelete="SET NULL")
            batch.drop_constraint("ck_fact_check_status", type_="check")
            batch.alter_column("status", existing_type=sa.String(16), type_=sa.String(24))
            batch.create_check_constraint("ck_fact_check_status", "status IN ('PENDING','RUNNING','WAITING_CONFIRMATION','SUCCESS','FAILURE','CANCELLED')")
            batch.add_column(sa.Column("title", sa.String(200), nullable=False, server_default=""))
            batch.add_column(sa.Column("source_kind", sa.String(16), nullable=False, server_default="record"))
            batch.add_column(sa.Column("file_id", sa.String(100), nullable=True))
            batch.add_column(sa.Column("parent_run_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_fact_check_parent", "fact_check_runs", ["parent_run_id"], ["id"], ondelete="SET NULL")
            batch.add_column(sa.Column("confirm_claims", sa.Boolean(), nullable=False, server_default=sa.false()))
            batch.add_column(sa.Column("stage", sa.String(16), nullable=False, server_default="extract"))
            batch.add_column(sa.Column("depth", sa.String(16), nullable=False, server_default="standard"))
            batch.add_column(sa.Column("selected_claim_ids", sa.JSON(none_as_null=True), nullable=True))
            batch.add_column(sa.Column("supplemental_urls", sa.JSON(), nullable=False, server_default="[]"))
            batch.add_column(sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default="{}"))
            batch.add_column(sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
            batch.add_column(sa.Column("execute_request_id", sa.String(36), nullable=True))
            batch.add_column(sa.Column("execute_request_hash", sa.String(64), nullable=True))
            batch.create_check_constraint("ck_fact_check_stage", "stage IN ('extract','check','complete')")
            batch.create_check_constraint("ck_fact_check_depth", "depth IN ('standard','deep')")
        bind.execute(sa.text("UPDATE fact_check_runs SET queued_at = created_at, stage = CASE WHEN status IN ('SUCCESS','FAILURE','CANCELLED') THEN 'complete' ELSE 'extract' END"))
    if "model_config_id" not in {c["name"] for c in sa.inspect(bind).get_columns("fact_check_config")}:
        with op.batch_alter_table("fact_check_config") as batch:
            batch.add_column(sa.Column("model_config_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_fact_check_model", "llm_configs", ["model_config_id"], ["id"], ondelete="SET NULL")
    if not sa.inspect(bind).has_table("fact_check_reviews"):
        op.create_table(
            "fact_check_reviews",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("fact_check_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("claim_id", sa.String(64), nullable=False),
            sa.Column("decision", sa.String(16), nullable=False),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("request_id", sa.String(36), nullable=False),
            sa.UniqueConstraint("run_id", "request_id", name="uq_fact_check_review_request"),
            sa.CheckConstraint("decision IN ('agree','disagree','unresolved')", name="ck_fact_check_review_decision"),
        )
        op.create_index("ix_fact_check_reviews_run_id", "fact_check_reviews", ["run_id"])
    metadata = sa.MetaData()
    permissions = sa.Table("permissions", metadata, autoload_with=bind)
    role_permissions = sa.Table("role_permissions", metadata, autoload_with=bind)
    eligible_roles = list(bind.execute(sa.select(role_permissions.c.role_id).join(
        permissions, permissions.c.id == role_permissions.c.permission_id,
    ).where(permissions.c.code.in_(["proofread:text", "proofread:document"])).distinct()).scalars())
    parent = None
    for code, name, kind, path in PERMISSIONS:
        permission_id = bind.execute(sa.select(permissions.c.id).where(permissions.c.code == code)).scalar()
        if permission_id is None:
            permission_id = bind.execute(permissions.insert().values(
                code=code, name=name, type=kind, path=path, parent_id=parent, icon="Search", sort_order=3,
            ).returning(permissions.c.id)).scalar_one()
        if parent is None:
            parent = permission_id
        for role_id in eligible_roles:
            if not bind.execute(sa.select(role_permissions.c.id).where(
                role_permissions.c.role_id == role_id, role_permissions.c.permission_id == permission_id,
            )).first():
                bind.execute(role_permissions.insert().values(role_id=role_id, permission_id=permission_id))


def downgrade():
    raise RuntimeError("事实核查工作台已保存独立任务与复核记录；请使用兼容版本回滚应用，不执行破坏性降级。")
