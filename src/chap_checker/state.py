"""Global CLI state passed via ``typer.Context.obj``."""

from __future__ import annotations

from pydantic import BaseModel


class GlobalState(BaseModel):
    """Carries flags that apply to every subcommand."""

    debug: bool = False
    json_output: bool = False
