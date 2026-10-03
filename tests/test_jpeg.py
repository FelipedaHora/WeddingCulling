from argparse import Namespace
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pyexiv2
import pytest

import wedding_culling as culling
from extraction import extract


def jpeg(path, image=None, tags=None):
    if image is None:
        image = np.random.default_rng(42).integers(30, 220, (120, 180, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    path.write_bytes(encoded.tobytes())
    if tags:
        with metadata(path) as meta:
            meta.modify_exif(tags)
    return path


@contextmanager
def metadata(path):
    meta = pyexiv2.ImageData(path.read_bytes())
    try:
        yield meta
        path.write_bytes(meta.get_bytes())
    finally:
        meta.close()


@pytest.mark.parametrize('extension', ['.jpg', '.jpeg', '.JPG', '.JPEG'])
def test_jpeg_pipeline_and_original_copy(tmp_path, extension):
    source = tmp_path / 'fotografias'
    source.mkdir()
    path = jpeg(source / ('foto' + extension))
    original = path.read_bytes()
    output = tmp_path / 'report'
    assert culling.run(Namespace(source=source, output=output, limit=0, cancel_file='')) == 0
    import json
    report = json.loads((output / 'relatorio.json').read_text(encoding='utf-8'))
    assert report['summary']['TOTAL DE FOTOGRAFIAS'] == 1
    assert not report['failures']
    rec = report['photos'][0]
    assert rec['preview_method'] == 'jpeg_decode'
    assert (output / rec['preview']).exists()
    assert rec['capture_time'] == datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='milliseconds')
    result = extract(report['photos'], {'isolated'}, tmp_path / 'copies')
    assert result.copied == 1
    assert (tmp_path / 'copies' / '02_Isoladas' / path.name).read_bytes() == original
    assert path.read_bytes() == original


def test_discovery_mixed_recursive(tmp_path):
    nested = tmp_path / 'nested'
    nested.mkdir()
    names = ['a.ARW', 'a.JPG', 'b.jpeg', 'c.NEF', '._hidden.JPG', 'ignored.png']
    for name in names:
        (nested / name).touch()
    assert {p.name for p in culling.image_paths(tmp_path)} == set(names[:4])


@pytest.mark.parametrize('orientation', range(1, 9))
def test_jpeg_exif_orientation_unicode_and_timestamp(tmp_path, orientation):
    pixels = np.zeros((60, 100, 3), dtype=np.uint8)
    pixels[:30, :50] = (20, 40, 230)
    pixels[30:, 50:] = (220, 80, 20)
    path = jpeg(tmp_path / 'coração.JPEG', pixels, {
        'Exif.Image.Orientation': str(orientation),
        'Exif.Photo.DateTimeOriginal': '2026:09:06 12:34:56',
        'Exif.Photo.SubSecTimeOriginal': '123',
    })
    original = path.read_bytes()
    base = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    expected = {
        1: lambda a: a, 2: lambda a: cv2.flip(a, 1), 3: lambda a: cv2.rotate(a, cv2.ROTATE_180),
        4: lambda a: cv2.flip(a, 0), 5: cv2.transpose,
        6: lambda a: cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE),
        7: lambda a: cv2.flip(cv2.transpose(a), -1),
        8: lambda a: cv2.rotate(a, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }[orientation](base)
    actual, method = culling.extract_preview(path, tmp_path / 'prévia.jpg')
    np.testing.assert_array_equal(actual, expected)
    assert method == 'jpeg_decode'
    assert culling.read_metadata(path)['capture_time'] == '2026-09-06T12:34:56.123'
    assert path.read_bytes() == original


def test_same_pixels_have_identical_raw_and_jpeg_analysis(tmp_path, monkeypatch):
    path = jpeg(tmp_path / 'photo.jpg', np.full((1800, 2000, 3), 40, dtype=np.uint8))
    original = path.read_bytes()
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_thumb(self):
            return Namespace(format=culling.rawpy.ThumbFormat.JPEG, data=original)
    monkeypatch.setattr(culling.rawpy, 'imread', lambda _: Raw())
    raw_image, raw_method = culling.extract_preview(tmp_path / 'photo.ARW', tmp_path / 'raw-preview.jpg')
    jpg_image, jpg_method = culling.extract_preview(path, tmp_path / 'jpg-preview.jpg')
    assert raw_method == 'embedded_preview' and jpg_method == 'jpeg_decode'
    assert max(jpg_image.shape[:2]) == 1600
    np.testing.assert_array_equal(raw_image, jpg_image)
    face = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eyes = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye_tree_eyeglasses.xml')
    scores = [culling.analyze_image(image, face, eyes) for image in (raw_image, jpg_image)]
    assert scores[0] == scores[1]
    for rec in scores:
        culling.classify([[rec]])
    assert scores[0]['classification'] == scores[1]['classification']


def test_corrupt_jpeg_does_not_abort_valid_images(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    jpeg(source / 'valid.jpg')
    (source / 'broken.jpeg').write_bytes(b'not a jpeg')
    output = tmp_path / 'out'
    assert culling.run(Namespace(source=source, output=output, limit=0, cancel_file='')) == 0
    import json
    report = json.loads((output / 'relatorio.json').read_text(encoding='utf-8'))
    assert report['summary']['TOTAL DE FOTOGRAFIAS'] == 2
    assert report['summary']['ANALISADOS'] == 1
    assert [p['filename'] for p in report['failures']] == ['broken.jpeg']
