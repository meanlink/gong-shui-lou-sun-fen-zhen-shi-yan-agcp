from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Drill(Base):
    """听漏仪停用演练：冻结原试验验证样本与规则树，按共用声学区间穷举结论。"""

    __tablename__ = 'drills'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    lo: Mapped[float] = mapped_column(Double)
    hi: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_digest: Mapped[str] = mapped_column(String(64), default='')
    source_result_checksum: Mapped[str] = mapped_column(String(64), default='')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(150), default='')
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    approved_result: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[int | None] = mapped_column(ForeignKey('drills.id'), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey('drills.id'), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
