from pathlib import Path

import pytest

from extraction import category_for, extract, required_bytes, selected_photos


def photo(path: Path, burst: str, classification: str):
    return {"filename": path.name, "original_path": str(path), "burst_id": burst, "classification": classification}


def test_categories_are_deterministic(tmp_path):
    p = tmp_path / "a.arw"
    p.write_bytes(b"raw")
    assert category_for(photo(p, "", "REVISAR")) == "isolated"
    assert category_for(photo(p, "BURST_0001", "MELHOR")) == "best"
    assert category_for(photo(p, "BURST_0001", "ALTERNATIVA")) == "alternative"
    assert category_for(photo(p, "BURST_0001", "REVISAR")) == "review"
    assert category_for(photo(p, "BURST_0001", "PROBLEMA_TECNICO")) is None


@pytest.mark.parametrize("category,burst,classification,folder", [
    ("best", "B1", "MELHOR", "01_Melhores"),
    ("isolated", "", "PROBLEMA_TECNICO", "02_Isoladas"),
    ("alternative", "B1", "ALTERNATIVA", "03_Boas_Alternativas"),
    ("review", "B1", "REVISAR", "04_Revisar"),
])
def test_extract_each_category(tmp_path, category, burst, classification, folder):
    source = tmp_path / "source"
    source.mkdir()
    raw = source / f"{category}.ARW"
    raw.write_bytes(b"original raw bytes")
    destination = tmp_path / "selected"
    result = extract([photo(raw, burst, classification)], {category}, destination)
    assert result.copied == 1
    assert (destination / folder / raw.name).read_bytes() == raw.read_bytes()
    assert raw.exists()


def test_multiple_categories_and_no_duplicates(tmp_path):
    raws = []
    for i in range(4):
        raw = tmp_path / f"{i}.ARW"
        raw.write_bytes(bytes([i]) * 10)
        raws.append(raw)
    photos = [
        photo(raws[0], "B1", "MELHOR"), photo(raws[1], "", "REVISAR"),
        photo(raws[2], "B1", "ALTERNATIVA"), photo(raws[3], "B1", "REVISAR"),
        photo(raws[0], "B1", "MELHOR"),
    ]
    items = selected_photos(photos, {"best", "isolated", "alternative", "review"})
    assert len(items) == 4
    assert required_bytes(items) == 40
    result = extract(photos, {"best", "isolated", "alternative", "review"}, tmp_path / "out")
    assert result.copied == 4


def test_existing_and_conflict_never_overwrite(tmp_path):
    source = tmp_path / "source.ARW"
    source.write_bytes(b"source")
    out = tmp_path / "out"
    target_dir = out / "01_Melhores"
    target_dir.mkdir(parents=True)
    target = target_dir / source.name
    target.write_bytes(b"source")
    first = extract([photo(source, "B1", "MELHOR")], {"best"}, out)
    assert first.existing == 1
    target.write_bytes(b"different")
    second = extract([photo(source, "B1", "MELHOR")], {"best"}, out)
    assert second.conflicts == 1
    assert target.read_bytes() == b"different"


def test_cancel_stops_before_next_copy(tmp_path):
    raws = []
    for i in range(3):
        raw = tmp_path / f"{i}.ARW"
        raw.write_bytes(b"x")
        raws.append(photo(raw, "B1", "MELHOR"))
    calls = 0
    def stop():
        nonlocal calls
        calls += 1
        return calls > 1
    result = extract(raws, {"best"}, tmp_path / "out", cancelled=stop)
    assert result.copied == 1
    assert result.cancelled
