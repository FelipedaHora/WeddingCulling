"""Cópia segura e determinística das fotografias classificadas."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

CATEGORY_DIRS = {
    "best": "01_Melhores",
    "isolated": "02_Isoladas",
    "alternative": "03_Boas_Alternativas",
    "review": "04_Revisar",
}


def category_for(photo: dict[str, Any]) -> str | None:
    """Uma foto recebe no máximo uma categoria de extração."""
    if not photo.get("burst_id"):
        return "isolated"
    classification = photo.get("classification")
    if classification == "MELHOR":
        return "best"
    if classification == "ALTERNATIVA":
        return "alternative"
    if classification == "REVISAR":
        return "review"
    return None


def selected_photos(photos: Iterable[dict[str, Any]], categories: set[str]) -> list[tuple[str, dict[str, Any]]]:
    result, seen = [], set()
    for photo in photos:
        category = category_for(photo)
        source = str(photo.get("original_path") or photo.get("path") or "")
        key = os.path.normcase(os.path.abspath(source)) if source else ""
        if category in categories and key and key not in seen:
            result.append((category, photo))
            seen.add(key)
    return result


def required_bytes(items: Iterable[tuple[str, dict[str, Any]]]) -> int:
    return sum(Path(p.get("original_path") or p["path"]).stat().st_size for _, p in items)


def same_file_content(source: Path, destination: Path) -> bool:
    if source.stat().st_size != destination.stat().st_size:
        return False
    block = 1024 * 1024
    with source.open("rb") as a, destination.open("rb") as b:
        while True:
            left, right = a.read(block), b.read(block)
            if left != right:
                return False
            if not left:
                return True


@dataclass
class ExtractionResult:
    copied: int = 0
    existing: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    per_category: dict[str, int] = field(default_factory=lambda: {key: 0 for key in CATEGORY_DIRS})


def extract(
    photos: Iterable[dict[str, Any]],
    categories: set[str],
    destination: Path,
    separate_folders: bool = True,
    progress: Callable[[int, int, str, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExtractionResult:
    items = selected_photos(photos, categories)
    result = ExtractionResult()
    destination = destination.resolve()
    total = len(items)
    for index, (category, photo) in enumerate(items, 1):
        if cancelled and cancelled():
            result.cancelled = True
            break
        source = Path(photo.get("original_path") or photo["path"]).resolve()
        try:
            if not source.is_file():
                raise FileNotFoundError(source)
            folder = destination / CATEGORY_DIRS[category] if separate_folders else destination
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source.name
            if target.exists():
                if same_file_content(source, target):
                    result.existing += 1
                else:
                    result.conflicts += 1
                    result.errors.append(f"Conflito, não sobrescrito: {target}")
            else:
                shutil.copy2(source, target)
                result.copied += 1
                result.per_category[category] += 1
        except Exception as exc:
            result.errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
        if progress:
            progress(index, total, source.name, category)
    return result
