"""Shared validated input base, independent of any calculation registry."""
from pydantic import BaseModel, ConfigDict

class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
