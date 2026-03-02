from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

LayoutName = Literal["title", "bullets", "two_columns", "sources"]


class SourceItem(BaseModel):
    name: str = ""
    detail: str = ""


class ColumnSpec(BaseModel):
    heading: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)


class SlideSpec(BaseModel):
    layout: LayoutName
    title: Optional[str] = None

    subtitle: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)

    left: Optional[ColumnSpec] = None
    right: Optional[ColumnSpec] = None

    sources: List[SourceItem] = Field(default_factory=list)
    notes: Optional[str] = None


class PresentationSpec(BaseModel):
    title: str
    subtitle: Optional[str] = None
    slides: List[SlideSpec]
    filename: Optional[str] = None
