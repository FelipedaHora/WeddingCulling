#!/usr/bin/env python3
"""Analisador técnico conservador de RAWs e JPEGs; nunca escreve na árvore de origem."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyexiv2
import rawpy

RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".dng"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
IMAGE_EXTENSIONS = RAW_EXTENSIONS | JPEG_EXTENSIONS


def image_paths(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file()
                  and p.suffix.lower() in IMAGE_EXTENSIONS and not p.name.startswith("._"))


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CSV_FIELDS = [
    "filename", "original_path", "capture_time", "burst_id", "sequence_type", "burst_position", "technical_score",
    "burst_rank", "classification", "sharpness_score", "face_sharpness_score",
    "eyes_status", "eyes_confidence", "motion_blur_score", "exposure_status",
    "issue_focus", "issue_eyes", "issue_blur", "issue_exposure", "issues",
    "selection_reason",
]


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class SafeWorkspace:
    source: Path
    output: Path

    def validate(self) -> None:
        if not self.source.is_dir():
            raise ValueError(f"Pasta de fotografias inexistente: {self.source}")
        if inside(self.output, self.source) or inside(self.source, self.output):
            raise ValueError("A saída não pode estar dentro ou conter a árvore das fotografias originais.")

    def writable(self, path: Path) -> Path:
        resolved = path.resolve()
        if inside(resolved, self.source):
            raise PermissionError(f"ESCRITA BLOQUEADA NA ORIGEM: {resolved}")
        if not inside(resolved, self.output):
            raise PermissionError(f"Escrita fora da pasta de relatório: {resolved}")
        return resolved

    def mkdir(self, path: Path) -> None:
        self.writable(path).mkdir(parents=True, exist_ok=True)

    def write_text(self, path: Path, text: str) -> None:
        self.writable(path).write_text(text, encoding="utf-8")


def tag(tags: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in tags:
            return str(tags[name])
    return ""


def rational(value: str) -> float | None:
    try:
        if "/" in value:
            a, b = value.split("/", 1)
            return float(a) / float(b)
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def read_metadata(path: Path) -> dict[str, Any]:
    image = (pyexiv2.ImageData(path.read_bytes()) if path.suffix.lower() in JPEG_EXTENSIONS
             else pyexiv2.Image(str(path)))
    try:
        tags = image.read_exif()
    finally:
        image.close()
    dt = tag(tags, "Exif.Photo.DateTimeOriginal", "Exif.Photo.DateTimeDigitized", "Exif.Image.DateTime")
    sub = tag(tags, "Exif.Photo.SubSecTimeOriginal", "Exif.Photo.SubSecTimeDigitized", "Exif.Photo.SubSecTime")
    capture = None
    if dt:
        try:
            capture = datetime.strptime(dt[:19], "%Y:%m:%d %H:%M:%S")
            if sub and sub.isdigit():
                capture = capture.replace(microsecond=int((sub + "000000")[:6]))
        except ValueError:
            pass
    if capture is None:
        capture = datetime.fromtimestamp(path.stat().st_mtime)
    return {
        "filename": path.name, "path": str(path), "original_path": str(path),
        "capture_time": capture.isoformat(timespec="milliseconds"),
        "camera": " ".join(filter(None, [tag(tags, "Exif.Image.Make"), tag(tags, "Exif.Image.Model")])),
        "lens": tag(tags, "Exif.Photo.LensModel", "Exif.Sony2.LensSpec"),
        "focal_length": tag(tags, "Exif.Photo.FocalLength"), "aperture": tag(tags, "Exif.Photo.FNumber"),
        "shutter": tag(tags, "Exif.Photo.ExposureTime"), "iso": tag(tags, "Exif.Photo.ISOSpeedRatings"),
        "width": tag(tags, "Exif.Photo.PixelXDimension", "Exif.Image.ImageWidth"),
        "height": tag(tags, "Exif.Photo.PixelYDimension", "Exif.Image.ImageLength"),
        "orientation": tag(tags, "Exif.Image.Orientation"),
    }


def extract_preview(raw_path: Path, preview_path: Path) -> tuple[np.ndarray, str]:
    if raw_path.suffix.lower() in JPEG_EXTENSIONS:
        # Decode bytes to support Unicode Windows paths; OpenCV applies EXIF orientation.
        image = cv2.imdecode(np.frombuffer(raw_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        return save_preview(image, preview_path), "jpeg_decode"
    method = "embedded_preview"
    with rawpy.imread(str(raw_path)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                image = cv2.imdecode(np.frombuffer(thumb.data, dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                image = cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            method = "reduced_raw_development"
            rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return save_preview(image, preview_path), method


def save_preview(image: np.ndarray | None, preview_path: Path) -> np.ndarray:
    """Mesma resolução e qualidade de preview para RAW e JPEG."""
    if image is None or image.size == 0:
        raise RuntimeError("preview vazio")
    h, w = image.shape[:2]
    scale = min(1.0, 1600 / max(h, w))
    if scale < 1:
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not success:
        raise RuntimeError("não foi possível gravar preview")
    preview_path.write_bytes(encoded.tobytes())
    return image


def dhash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    return f"{sum(int(v) << i for i, v in enumerate(bits.flatten())):016x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def analyze_image(image: np.ndarray, face_cascade: cv2.CascadeClassifier,
                  eye_cascade: cv2.CascadeClassifier) -> dict[str, Any]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    lap_var = float(lap.var())
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    gradient = float(np.mean(cv2.magnitude(gx, gy)))
    sharp = float(np.clip(30 * math.log10(1 + lap_var) - 20, 0, 100))
    faces = face_cascade.detectMultiScale(gray, 1.12, 5, minSize=(40, 40))
    face_scores, eye_counts, border_faces = [], [], 0
    for x, y, fw, fh in faces:
        roi = gray[y:y + fh, x:x + fw]
        face_scores.append(float(np.clip(30 * math.log10(1 + cv2.Laplacian(roi, cv2.CV_64F).var()) - 20, 0, 100)))
        eyes = eye_cascade.detectMultiScale(roi[: int(fh * .68)], 1.1, 5, minSize=(12, 8))
        eye_counts.append(len(eyes))
        if x < 4 or y < 4 or x + fw > w - 4 or y + fh > h - 4:
            border_faces += 1
    face_sharp = statistics.mean(face_scores) if face_scores else None
    analyzable = [i for i, (_, _, fw, _) in enumerate(faces) if fw >= 70]
    if not analyzable:
        eyes_status, eyes_conf = "INCERTO", 0.15
        eyes_note = "Nenhum rosto com pelo menos 70 px para avaliar os olhos."
    else:
        good = sum(eye_counts[i] >= 2 for i in analyzable)
        none = sum(eye_counts[i] == 0 for i in analyzable)
        if none and not good:
            eyes_status, eyes_conf = "INCERTO", 0.42
            eyes_note = "Olhos não detectados; pode ser piscada, pose, obstrução ou limitação do detector."
        else:
            eyes_status, eyes_conf = "ABERTOS_PROVÁVEIS", min(.9, .58 + .12 * good)
            eyes_note = "Dois olhos detectados em rosto(s) analisável(is)." if good else "Detecção ocular parcial."
    dark = float(np.mean(gray <= 5))
    bright = float(np.mean(gray >= 250))
    mean_luma = float(gray.mean())
    if bright > .18 or dark > .28 or mean_luma < 28 or mean_luma > 232:
        exposure = "PROBLEMA_SEVERO"
    elif bright > .06 or dark > .14 or mean_luma < 48 or mean_luma > 210:
        exposure = "POSSIVELMENTE_RECUPERAVEL_NO_RAW"
    else:
        exposure = "OK"
    fft = np.fft.fftshift(np.fft.fft2(gray[::2, ::2]))
    magnitude = np.log1p(np.abs(fft))
    cy, cx = np.array(magnitude.shape) // 2
    center = magnitude[max(0, cy-4):cy+5, max(0, cx-4):cx+5]
    high_ratio = float((magnitude.sum() - center.sum()) / max(1, magnitude.sum()))
    blur_score = float(np.clip((sharp * .72 + min(100, gradient * 3.2) * .28), 0, 100))
    issue_focus = bool((face_sharp is not None and face_sharp < 30) or (len(faces) == 0 and sharp < 20))
    issue_blur = blur_score < 28
    issue_eyes = eyes_status == "INCERTO" and eyes_conf >= .4
    issue_exposure = exposure == "PROBLEMA_SEVERO"
    issues = []
    if issue_focus: issues.append("possível foco insuficiente no assunto")
    if issue_eyes: issues.append("olhos exigem revisão manual")
    if issue_blur: issues.append("possível blur/movimento")
    if issue_exposure: issues.append("exposição severa no preview")
    if border_faces: issues.append("rosto próximo/cortado pela borda")
    subject = face_sharp if face_sharp is not None else sharp
    exposure_score = 45 if exposure == "PROBLEMA_SEVERO" else 75 if exposure != "OK" else 94
    eyes_score = 88 if eyes_status == "ABERTOS_PROVÁVEIS" else 68
    score = .27 * sharp + .30 * subject + .17 * blur_score + .12 * exposure_score + .10 * eyes_score + .04 * (100 - 20 * bool(border_faces))
    return {
        "technical_score": round(float(np.clip(score, 0, 100)), 1),
        "sharpness_score": round(sharp, 1), "face_sharpness_score": None if face_sharp is None else round(face_sharp, 1),
        "faces_count": len(faces), "eyes_status": eyes_status, "eyes_confidence": round(eyes_conf, 2),
        "eyes_explanation": eyes_note, "motion_blur_score": round(blur_score, 1),
        "exposure_status": exposure, "clipped_shadows_fraction": round(dark, 4),
        "clipped_highlights_fraction": round(bright, 4), "mean_luma": round(mean_luma, 1),
        "issue_focus": issue_focus, "issue_eyes": issue_eyes, "issue_blur": issue_blur,
        "issue_exposure": issue_exposure, "issues": issues, "border_faces": border_faces,
        "perceptual_hash": dhash(gray), "histogram": cv2.calcHist([image], [0, 1], None, [8, 8], [0, 256, 0, 256]).flatten().tolist(),
        "diagnostics": {"laplacian_variance": round(lap_var, 2), "mean_gradient": round(gradient, 2), "fft_high_frequency_ratio": round(high_ratio, 4)},
    }


def group_bursts(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups, current = [], []
    for rec in records:
        if not current:
            current = [rec]
            continue
        prev = current[-1]
        dt = (datetime.fromisoformat(rec["capture_time"]) - datetime.fromisoformat(prev["capture_time"])).total_seconds()
        hash_distance = hamming(rec["perceptual_hash"], prev["perceptual_hash"])
        hist_a, hist_b = np.float32(rec["histogram"]), np.float32(prev["histogram"])
        corr = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
        same = dt <= 1.6 and hash_distance <= 25 and corr >= .38
        if same:
            current.append(rec)
        else:
            groups.append(current)
            current = [rec]
    if current:
        groups.append(current)
    return groups


def classify(groups: list[list[dict[str, Any]]]) -> None:
    burst_num = 0
    for group in groups:
        is_burst = len(group) >= 2
        if is_burst:
            burst_num += 1
        ordered = sorted(group, key=lambda r: r["technical_score"], reverse=True)
        for pos, rec in enumerate(group, 1):
            rec["burst_id"] = f"BURST_{burst_num:04d}" if is_burst else ""
            rec["sequence_type"] = "RAJADA" if is_burst else "FOTO_ISOLADA"
            rec["burst_position"] = pos if is_burst else 1
            rec["burst_size"] = len(group)
            rec["burst_rank"] = ordered.index(rec) + 1
            serious = rec["issue_focus"] or rec["issue_blur"] or rec["issue_exposure"]
            if is_burst and rec is ordered[0]:
                rec["classification"] = "MELHOR"
            elif (is_burst and rec is ordered[1] and rec["technical_score"] >= 45) or (not is_burst and rec["technical_score"] >= 50 and not serious):
                rec["classification"] = "ALTERNATIVA"
            elif serious and rec["technical_score"] < 48:
                rec["classification"] = "PROBLEMA_TECNICO"
            elif serious or rec["issue_eyes"] or rec["technical_score"] < 40:
                rec["classification"] = "REVISAR"
            else:
                rec["classification"] = "ALTERNATIVA"
            if rec["classification"] == "MELHOR":
                rec["selection_reason"] = "Melhor combinação técnica relativa da sequência (foco, rosto, blur e exposição)."
            elif rec["classification"] == "ALTERNATIVA":
                rec["selection_reason"] = "Boa opção técnica; preservada como alternativa, sem inferir descarte."
            else:
                rec["selection_reason"] = "; ".join(rec["issues"]) or "Score técnico pede inspeção humana."


def html_report(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    data = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    cards = "".join(f"<div><b>{v}</b><span>{k}</span></div>" for k, v in summary.items() if k != "score_method")
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Relatório de Culling</title><style>
:root{{--bg:#0b1020;--panel:#151c30;--text:#edf2ff;--muted:#9ba8c7;--best:#ffd166;--good:#69db9c;--warn:#ffb86b;--bad:#ff6b7a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px system-ui}}header,main{{max-width:1500px;margin:auto;padding:24px}}
h1,h2{{margin:.2em 0 .7em}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}}.stats div,.burst,.special{{background:var(--panel);border:1px solid #26304b;border-radius:14px;padding:14px}}.stats b{{font-size:24px;display:block}}.stats span{{color:var(--muted)}}
.filters{{position:sticky;top:0;z-index:4;background:#0b1020e8;padding:12px 0;display:flex;gap:8px;flex-wrap:wrap}}button{{background:#202a45;color:var(--text);border:0;padding:9px 12px;border-radius:999px;cursor:pointer}}button.active{{background:#5269b0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:12px}}.photo{{background:#10172a;border:2px solid transparent;border-radius:10px;overflow:hidden;cursor:pointer}}.photo.MELHOR{{border-color:var(--best)}}.photo img{{width:100%;height:150px;object-fit:cover}}.meta{{padding:9px}}.meta small{{display:block;color:var(--muted)}}.burst{{margin:14px 0}}.burst h3{{margin:0 0 10px}}.special{{margin:20px 0}}dialog{{width:min(920px,94vw);background:#151c30;color:var(--text);border:1px solid #394663;border-radius:14px}}dialog img{{max-width:100%;max-height:64vh;display:block;margin:auto}}dialog pre{{white-space:pre-wrap}}.badge{{float:right}}@media(max-width:600px){{header,main{{padding:12px}}}}
</style></head><body><header><h1>Relatório técnico de culling</h1><p>Decisões conservadoras; “problema” significa revisar, nunca excluir.</p><div class="stats">{cards}</div></header><main>
<div class="filters"><button data-f="ALL" class="active">TODAS</button><button data-f="MELHOR">⭐ MELHORES</button><button data-f="isolated">📷 ISOLADAS</button><button data-f="ALTERNATIVA">✓ ALTERNATIVAS</button><button data-f="REVISAR">⚠ REVISAR</button><button data-f="PROBLEMA_TECNICO">✕ PROBLEMAS</button><button data-f="eyes">👁 OLHOS</button><button data-f="focus">🎯 FOCO</button><button data-f="blur">💨 BLUR</button><button data-f="exposure">☀ EXPOSIÇÃO</button><button data-f="bursts">📸 RAJADAS</button></div>
<section class="special"><h2>⭐ Melhores de cada rajada</h2><div class="grid" id="best"></div></section>
<section class="special"><h2>⚠ Fotos que precisam de revisão</h2><div class="grid" id="review"></div></section>
<h2>Rajadas e fotos isoladas</h2><div id="groups"></div></main><dialog id="modal"><button onclick="modal.close()">Fechar</button><div id="detail"></div></dialog>
<script>const photos={data};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function card(p){{return `<article class="photo ${{p.classification}}" onclick="show('${{esc(p.filename)}}')"><img loading="lazy" src="${{esc(p.preview)}}"><div class="meta"><b>${{esc(p.filename)}} <span class="badge">${{p.technical_score}}</span></b><small>${{p.classification}} · ${{p.burst_id||'ISOLADA'}}</small><small>${{esc(p.issues.join('; ')||'sem alerta técnico forte')}}</small></div></article>`}}
function matches(p,f){{return f==='ALL'||p.classification===f||f==='isolated'&&!p.burst_id||f==='eyes'&&p.issue_eyes||f==='focus'&&p.issue_focus||f==='blur'&&p.issue_blur||f==='exposure'&&p.issue_exposure||f==='bursts'&&p.burst_id}}
function render(f='ALL'){{let map={{}};photos.filter(p=>matches(p,f)).forEach(p=>{{let k=p.burst_id||'ISOLADA';(map[k]??=[]).push(p)}});groups.innerHTML=Object.entries(map).map(([k,a])=>`<section class="burst"><h3>${{k}} · ${{a.length}} foto(s) · ${{esc(a[0].capture_time)}} → ${{esc(a.at(-1).capture_time)}}</h3><div class="grid">${{a.map(card).join('')}}</div></section>`).join('')}}
function show(name){{let p=photos.find(x=>x.filename===name);detail.innerHTML=`<h2>${{esc(p.filename)}} · ${{p.classification}}</h2><img src="${{esc(p.preview)}}"><pre>Original path: ${{esc(p.original_path||p.path)}}\\nTipo: ${{p.sequence_type||(!p.burst_id?'FOTO_ISOLADA':'RAJADA')}}\\nPosição: ${{p.burst_position}}/${{p.burst_size}} · rank ${{p.burst_rank}}\\nScore: ${{p.technical_score}}\\nNitidez: ${{p.sharpness_score}} — rosto: ${{p.face_sharpness_score??'INCERTO'}}\\nRostos: ${{p.faces_count}}\\nOlhos: ${{p.eyes_status}} (confiança ${{p.eyes_confidence}}) — ${{esc(p.eyes_explanation)}}\\nBlur: ${{p.motion_blur_score}}\\nExposição: ${{p.exposure_status}}\\nProblemas: ${{esc(p.issues.join('; ')||'nenhum alerta forte')}}\\nMotivo: ${{esc(p.selection_reason)}}</pre>`;modal.showModal()}}
document.querySelectorAll('button[data-f]').forEach(b=>b.onclick=()=>{{document.querySelectorAll('button[data-f]').forEach(x=>x.classList.remove('active'));b.classList.add('active');render(b.dataset.f)}});best.innerHTML=photos.filter(p=>p.classification==='MELHOR').map(card).join('');review.innerHTML=photos.filter(p=>p.classification==='REVISAR'||p.classification==='PROBLEMA_TECNICO').map(card).join('');render();</script></body></html>"""


def run(args: argparse.Namespace) -> int:
    source, output = Path(args.source).resolve(), Path(args.output).resolve()
    safe = SafeWorkspace(source, output)
    safe.validate()
    safe.mkdir(output)
    previews = output / "previews"
    safe.mkdir(previews)
    raw_paths = image_paths(source)
    if args.limit:
        raw_paths = raw_paths[:args.limit]
    inventory, failures = [], []
    for path in raw_paths:
        try:
            inventory.append(read_metadata(path))
        except Exception as exc:
            failures.append({"filename": path.name, "error": f"{type(exc).__name__}: {exc}"})
    inventory.sort(key=lambda r: (r["capture_time"], r["filename"]))
    face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    eyes = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")
    cache_path = output / "cache_analise.json"
    cached: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        try:
            cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if cache_payload.get("schema_version") == 2:
                cached = {r["path"]: r for r in cache_payload.get("photos", [])}
        except (OSError, ValueError, KeyError):
            cached = {}
    records = []
    cancelled = False
    for index, meta in enumerate(inventory, 1):
        if args.cancel_file and Path(args.cancel_file).exists():
            cancelled = True
            print("CANCELLED", flush=True)
            break
        raw_path = Path(meta["path"])
        key = hashlib.sha1(str(raw_path).encode()).hexdigest()[:10]
        preview_path = previews / f"{raw_path.stem}_{key}.jpg"
        try:
            old = cached.get(str(raw_path))
            if old and preview_path.exists():
                rec = old
            else:
                image, method = extract_preview(raw_path, safe.writable(preview_path))
                rec = {**meta, **analyze_image(image, face, eyes), "preview_method": method,
                       "preview": f"previews/{preview_path.name}"}
                if method == "jpeg_decode" and rec["exposure_status"] == "POSSIVELMENTE_RECUPERAVEL_NO_RAW":
                    # Only the description changes; all scores and thresholds remain identical.
                    rec["exposure_status"] = "EXPOSICAO_EXIGE_REVISAO"
            records.append(rec)
        except Exception as exc:
            failures.append({"filename": raw_path.name, "error": f"{type(exc).__name__}: {exc}"})
        if index % 25 == 0 or index == len(inventory):
            safe.write_text(cache_path, json.dumps({"schema_version": 2, "photos": records}, ensure_ascii=False))
            print(f"[{index}/{len(inventory)}] analisados={len(records)} falhas={len(failures)}", flush=True)
    if cancelled:
        return 130
    groups = group_bursts(records)
    print("STAGE:Identificando e comparando rajadas", flush=True)
    classify(groups)
    counts = {name: sum(r["classification"] == name for r in records) for name in ("MELHOR", "ALTERNATIVA", "REVISAR", "PROBLEMA_TECNICO")}
    summary = {
        "TOTAL DE FOTOGRAFIAS": len(raw_paths), "ANALISADOS": len(records),
        "RAJADAS": sum(len(g) >= 2 for g in groups), "FOTOS ISOLADAS": sum(len(g) == 1 for g in groups),
        "⭐ MELHORES": counts["MELHOR"], "✓ BOAS / ALTERNATIVAS": counts["ALTERNATIVA"],
        "⚠ REVISAR": counts["REVISAR"], "✕ POSSÍVEIS PROBLEMAS": counts["PROBLEMA_TECNICO"],
        "score_method": "27% nitidez geral, 30% assunto/rosto, 17% blur, 12% exposição, 10% olhos, 4% enquadramento; ranking relativo por rajada.",
    }
    payload = {"schema_version": 1, "source_read_only": str(source), "summary": summary,
               "failures": failures, "photos": records}
    safe.write_text(output / "relatorio.json", json.dumps(payload, ensure_ascii=False, indent=2))
    with safe.writable(output / "relatorio.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rec in records:
            row = {k: rec.get(k, "") for k in CSV_FIELDS}
            row["issues"] = "; ".join(rec["issues"])
            writer.writerow(row)
    safe.write_text(output / "relatorio.html", html_report(records, summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print(f"Falhas registradas: {len(failures)}", file=sys.stderr)
    return 0 if records else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", default="./relatorio_culling")
    parser.add_argument("--limit", type=int, default=0, help="Amostra inicial; 0 processa tudo.")
    parser.add_argument("--cancel-file", default="", help="Arquivo-sinal para cancelamento cooperativo.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
