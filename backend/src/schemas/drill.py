from pydantic import BaseModel, Field


class DrillInput(BaseModel):
    name: str = Field(default='听漏仪停用演练', min_length=2, max_length=160)
    job_id: int
    lo: float = Field(default=12, ge=0, le=200)
    hi: float = Field(default=85, ge=0, le=200)


class DrillReviseInput(BaseModel):
    name: str = Field(default='', max_length=160)
    lo: float = Field(ge=0, le=200)
    hi: float = Field(ge=0, le=200)
