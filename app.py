"""Interface desktop Windows do WeddingCulling."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

import wedding_culling
from extraction import CATEGORY_DIRS, extract, required_bytes, selected_photos

APP_NAME = "WeddingCulling"


def data_root() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    root = base / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


LOG_PATH = data_root() / "WeddingCulling.log"
logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


def count_photos(folder: Path) -> int:
    return len(wedding_culling.image_paths(folder))


class AnalysisWorker(QThread):
    progress = Signal(int, int, str)
    stage = Signal(str)
    completed = Signal(int, str)

    def __init__(self, source: Path, output: Path, cancel_file: Path):
        super().__init__()
        self.source, self.output, self.cancel_file = source, output, cancel_file

    def request_cancel(self):
        self.cancel_file.write_text("cancel", encoding="utf-8")

    def run(self):
        original_print = wedding_culling.print if hasattr(wedding_culling, "print") else None
        builtin_print = print

        def intercepted(*args, **kwargs):
            text = " ".join(str(x) for x in args)
            match = re.search(r"\[(\d+)/(\d+)\]", text)
            if match:
                self.progress.emit(int(match.group(1)), int(match.group(2)), "Analisando foco, rostos e exposição")
            elif text.startswith("STAGE:"):
                self.stage.emit(text.split(":", 1)[1])
            builtin_print(*args, **kwargs)

        try:
            wedding_culling.print = intercepted
            code = wedding_culling.run(Namespace(
                source=str(self.source), output=str(self.output), limit=0,
                cancel_file=str(self.cancel_file),
            ))
            self.completed.emit(code, "")
        except Exception:
            details = traceback.format_exc()
            logging.exception("Falha na análise")
            self.completed.emit(1, details)
        finally:
            if original_print is None:
                try:
                    delattr(wedding_culling, "print")
                except AttributeError:
                    pass
            else:
                wedding_culling.print = original_print


class ExtractionWorker(QThread):
    progress = Signal(int, int, str, str)
    completed = Signal(object)

    def __init__(self, photos, categories, destination, separate):
        super().__init__()
        self.photos, self.categories = photos, categories
        self.destination, self.separate = destination, separate
        self.stop_requested = False

    def cancel(self):
        self.stop_requested = True

    def run(self):
        result = extract(
            self.photos, self.categories, self.destination, self.separate,
            progress=lambda a, b, c, d: self.progress.emit(a, b, c, d),
            cancelled=lambda: self.stop_requested,
        )
        self.completed.emit(result)


class ExtractionDialog(QDialog):
    def __init__(self, parent, photos, source: Path):
        super().__init__(parent)
        self.photos, self.source, self.worker = photos, source, None
        self.settings = QSettings("WeddingCulling", "WeddingCulling")
        self.setWindowTitle("Extrair fotografias")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        title = QLabel("EXTRAIR FOTOGRAFIAS")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("Selecione quais grupos deseja copiar:"))
        self.checks = {}
        labels = {
            "best": "⭐ Melhores das rajadas", "isolated": "📷 Fotos isoladas",
            "alternative": "✓ Boas alternativas", "review": "⚠ Revisar",
        }
        for key, text in labels.items():
            check = QCheckBox(text)
            check.stateChanged.connect(self.update_space)
            self.checks[key] = check
            layout.addWidget(check)
        self.separate = QCheckBox("Criar uma pasta para cada categoria")
        self.separate.setChecked(True)
        layout.addWidget(self.separate)
        row = QHBoxLayout()
        suggested = str(source.parent / "WeddingCulling_Selecionadas")
        self.destination = QLineEdit(self.settings.value("last_destination", suggested))
        browse = QPushButton("PROCURAR")
        browse.clicked.connect(self.browse)
        row.addWidget(self.destination)
        row.addWidget(browse)
        layout.addLayout(row)
        self.space = QLabel()
        layout.addWidget(self.space)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.current = QLabel()
        layout.addWidget(self.current)
        buttons = QHBoxLayout()
        self.cancel_btn = QPushButton("CANCELAR")
        self.extract_btn = QPushButton("EXTRAIR")
        self.extract_btn.setObjectName("primary")
        self.cancel_btn.clicked.connect(self.cancel_action)
        self.extract_btn.clicked.connect(self.start)
        buttons.addStretch()
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.extract_btn)
        layout.addLayout(buttons)
        self.update_space()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Destino da extração", self.destination.text())
        if folder:
            self.destination.setText(folder)
            self.update_space()

    def selection(self):
        return {key for key, check in self.checks.items() if check.isChecked()}

    def update_space(self):
        try:
            items = selected_photos(self.photos, self.selection())
            needed = required_bytes(items)
            destination = Path(self.destination.text() or self.source.parent)
            probe = destination if destination.exists() else destination.parent
            free = shutil.disk_usage(probe).free
            self.space.setText(
                f"Arquivos selecionados: {len(items)}   •   Espaço necessário: {needed / 2**30:.2f} GB"
                f"   •   Disponível: {free / 2**30:.1f} GB"
            )
        except OSError:
            self.space.setText("Não foi possível calcular o espaço disponível.")

    def start(self):
        categories = self.selection()
        if not categories:
            QMessageBox.warning(self, APP_NAME, "Selecione ao menos uma categoria.")
            return
        destination = Path(self.destination.text()).resolve()
        self.settings.setValue("last_destination", str(destination))
        if wedding_culling.inside(destination, self.source):
            QMessageBox.critical(self, APP_NAME, "O destino deve ficar fora da pasta dos arquivos originais.")
            return
        items = selected_photos(self.photos, categories)
        needed = required_bytes(items)
        probe = destination if destination.exists() else destination.parent
        if not probe.exists():
            QMessageBox.warning(self, APP_NAME, "A pasta pai do destino não existe.")
            return
        if shutil.disk_usage(probe).free < needed:
            QMessageBox.critical(self, APP_NAME, "O disco de destino não possui espaço suficiente.")
            return
        self.progress.setMaximum(max(1, len(items)))
        self.progress.show()
        self.extract_btn.setEnabled(False)
        self.worker = ExtractionWorker(self.photos, categories, destination, self.separate.isChecked())
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_complete)
        self.worker.start()

    def cancel_action(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.current.setText("Cancelamento solicitado; terminando o arquivo atual…")
        else:
            self.reject()

    def on_progress(self, current, total, filename, category):
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.current.setText(f"{current} / {total} fotografias — {filename}")

    def on_complete(self, result):
        status = "EXTRAÇÃO INTERROMPIDA" if result.cancelled else "EXTRAÇÃO CONCLUÍDA"
        text = (
            f"{status}\n\nTotal copiado: {result.copied}\nJá existentes: {result.existing}"
            f"\nConflitos: {result.conflicts}\nErros: {len(result.errors)}"
        )
        QMessageBox.information(self, APP_NAME, text)
        self.extract_btn.setEnabled(True)
        self.cancel_btn.setText("FECHAR")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.destination.text()).resolve())))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("WeddingCulling", "WeddingCulling")
        self.source = None
        self.output = None
        self.photos = []
        self.worker = None
        self.resume_source = None
        self.resume_output = None
        self.started_at = 0.0
        self.setWindowTitle(APP_NAME)
        self.resize(self.settings.value("window_size", self.size()))
        self.setMinimumSize(820, 600)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.build_select()
        self.build_progress()
        self.build_result()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        last = self.settings.value("last_source", "")
        if last:
            self.path.setText(last)
            self.refresh_count()

    def build_select(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(70, 60, 70, 60)
        box.addStretch()
        title = QLabel("WEDDING CULLING")
        title.setObjectName("hero")
        box.addWidget(title)
        subtitle = QLabel("Seleção técnica local, segura e conservadora para fotografias RAW e JPEG.")
        subtitle.setObjectName("muted")
        box.addWidget(subtitle)
        box.addSpacing(34)
        box.addWidget(QLabel("Selecione a pasta com as fotografias RAW e JPEG:"))
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        browse = QPushButton("PROCURAR")
        browse.clicked.connect(self.browse_source)
        row.addWidget(self.path)
        row.addWidget(browse)
        box.addLayout(row)
        self.photo_count = QLabel("Nenhuma pasta selecionada.")
        self.photo_count.setObjectName("muted")
        box.addWidget(self.photo_count)
        box.addSpacing(24)
        self.analyze_btn = QPushButton("ANALISAR FOTOGRAFIAS")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(self.start_analysis)
        box.addWidget(self.analyze_btn)
        box.addStretch()
        self.stack.addWidget(page)

    def build_progress(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(70, 80, 70, 80)
        title = QLabel("ANALISANDO FOTOGRAFIAS…")
        title.setObjectName("hero")
        box.addWidget(title)
        self.stage = QLabel("Lendo fotografias e metadados")
        box.addWidget(self.stage)
        self.analysis_progress = QProgressBar()
        box.addWidget(self.analysis_progress)
        self.progress_count = QLabel("Preparando…")
        box.addWidget(self.progress_count)
        self.elapsed = QLabel("Tempo decorrido: 00:00")
        self.elapsed.setObjectName("muted")
        box.addWidget(self.elapsed)
        box.addStretch()
        cancel = QPushButton("CANCELAR")
        cancel.clicked.connect(self.cancel_analysis)
        box.addWidget(cancel)
        self.stack.addWidget(page)

    def build_result(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(70, 55, 70, 55)
        title = QLabel("ANÁLISE CONCLUÍDA")
        title.setObjectName("hero")
        box.addWidget(title)
        self.result_summary = QLabel()
        self.result_summary.setObjectName("summary")
        box.addWidget(self.result_summary)
        box.addStretch()
        open_report = QPushButton("ABRIR RELATÓRIO")
        open_report.setObjectName("primary")
        open_report.clicked.connect(self.open_report)
        extract_btn = QPushButton("EXTRAIR FOTOGRAFIAS")
        extract_btn.clicked.connect(self.open_extraction)
        new_btn = QPushButton("NOVO TRABALHO")
        new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        box.addWidget(open_report)
        box.addWidget(extract_btn)
        box.addWidget(new_btn)
        self.stack.addWidget(page)

    def browse_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Pasta com fotografias RAW e JPEG",
                                                  self.settings.value("last_source", ""))
        if folder:
            self.path.setText(folder)
            self.settings.setValue("last_source", folder)
            self.refresh_count()

    def refresh_count(self):
        folder = Path(self.path.text())
        count = count_photos(folder) if folder.is_dir() else 0
        self.photo_count.setText(f"{count:,} fotografias RAW e JPEG encontradas".replace(",", ".")
                               if count else "Nenhuma fotografia RAW ou JPEG encontrada nesta pasta.")
        self.analyze_btn.setEnabled(count > 0)

    def start_analysis(self):
        self.source = Path(self.path.text()).resolve()
        if self.resume_source == self.source and self.resume_output and self.resume_output.exists():
            self.output = self.resume_output
        else:
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", self.source.name).strip("_")[:36] or "TRABALHO"
            job_id = f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe_name}_{uuid.uuid4().hex[:6]}"
            self.output = data_root() / "WeddingCulling_Data" / job_id
            self.output.mkdir(parents=True, exist_ok=False)
        cancel_file = self.output / ".cancel"
        if cancel_file.exists():
            cancel_file.unlink()
        self.worker = AnalysisWorker(self.source, self.output, cancel_file)
        self.worker.progress.connect(self.on_analysis_progress)
        self.worker.stage.connect(self.stage.setText)
        self.worker.completed.connect(self.analysis_complete)
        self.started_at = time.monotonic()
        self.timer.start(1000)
        self.stack.setCurrentIndex(1)
        self.worker.start()

    def on_analysis_progress(self, current, total, stage):
        self.analysis_progress.setMaximum(total)
        self.analysis_progress.setValue(current)
        self.progress_count.setText(f"{current:,} / {total:,}".replace(",", "."))
        self.stage.setText(stage)

    def tick(self):
        seconds = int(time.monotonic() - self.started_at)
        self.elapsed.setText(f"Tempo decorrido: {seconds // 60:02d}:{seconds % 60:02d}")

    def cancel_analysis(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_cancel()
            self.stage.setText("Cancelamento solicitado; finalizando a fotografia atual…")

    def analysis_complete(self, code, details):
        self.timer.stop()
        if code == 130:
            self.resume_source, self.resume_output = self.source, self.output
            QMessageBox.information(self, APP_NAME, "Análise cancelada. O cache válido foi preservado.")
            self.stack.setCurrentIndex(0)
            return
        if code != 0:
            QMessageBox.critical(self, APP_NAME, "A análise não pôde ser concluída. Consulte o arquivo de log.")
            logging.error(details)
            self.stack.setCurrentIndex(0)
            return
        try:
            self.resume_source = self.resume_output = None
            payload = json.loads((self.output / "relatorio.json").read_text(encoding="utf-8"))
            self.photos = payload["photos"]
            s = payload["summary"]
            self.result_summary.setText(
                f"{s['ANALISADOS']:,} fotografias analisadas\n\n"
                f"⭐ Melhores: {s['⭐ MELHORES']}\n"
                f"📷 Fotos isoladas: {s['FOTOS ISOLADAS']}\n"
                f"✓ Boas alternativas: {s['✓ BOAS / ALTERNATIVAS']}\n"
                f"⚠ Revisar: {s['⚠ REVISAR']}".replace(",", ".")
            )
            self.stack.setCurrentIndex(2)
        except Exception:
            logging.exception("Falha ao carregar relatório")
            QMessageBox.critical(self, APP_NAME, "O relatório deste trabalho não foi encontrado ou está inválido.")

    def open_report(self):
        report = self.output / "relatorio.html"
        if report.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))
        else:
            QMessageBox.warning(self, APP_NAME, "O relatório deste trabalho não foi encontrado.")

    def open_extraction(self):
        ExtractionDialog(self, self.photos, self.source).exec()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, APP_NAME, "Cancele a análise antes de fechar o aplicativo.")
            event.ignore()
            return
        self.settings.setValue("window_size", self.size())
        event.accept()


STYLE = """
QWidget { background:#0b1020; color:#edf2ff; font-family:"Segoe UI"; font-size:15px; }
QLabel#hero { font-size:34px; font-weight:700; color:#ffffff; }
QLabel#title { font-size:24px; font-weight:700; }
QLabel#muted { color:#9ba8c7; }
QLabel#summary { font-size:19px; line-height:1.5; }
QLineEdit { background:#151c30; border:1px solid #34405f; border-radius:9px; padding:12px; }
QPushButton { background:#202a45; border:1px solid #35415e; border-radius:9px; padding:12px 18px; font-weight:600; }
QPushButton:hover { background:#2b385a; }
QPushButton:disabled { color:#6f7890; background:#151a29; }
QPushButton#primary { background:#5269b0; border-color:#7087ce; }
QPushButton#primary:hover { background:#6279c1; }
QProgressBar { background:#151c30; border:0; border-radius:8px; height:22px; text-align:center; }
QProgressBar::chunk { background:#657bd0; border-radius:8px; }
QCheckBox { spacing:10px; padding:5px; }
"""


def main():
    if "--self-test" in sys.argv:
        raw_path = None
        if "--raw" in sys.argv:
            raw_path = Path(sys.argv[sys.argv.index("--raw") + 1])
        assert cv_self_test(raw_path)
        return 0
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


def cv_self_test(raw_path: Path | None = None) -> bool:
    """Carrega as dependências nativas que mais costumam falhar no empacotamento."""
    import cv2
    import pyexiv2
    import rawpy
    cascade_path = Path(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not (cv2.__version__ and pyexiv2.__version__ and rawpy.__version__ and cascade_path.exists()):
        return False
    # Exercise JPEG decoding, metadata, scoring, reports and lossless copying
    # inside the frozen distribution, without touching user photographs.
    import tempfile
    import numpy as np
    with tempfile.TemporaryDirectory(prefix="WeddingCulling-self-test-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        photo = source / "teste.JPEG"
        pixels = np.random.default_rng(42).integers(30, 220, (120, 180, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", pixels)
        assert ok
        original = encoded.tobytes()
        photo.write_bytes(original)
        assert count_photos(source) == 1
        output = root / "report"
        assert wedding_culling.run(Namespace(source=source, output=output, limit=0, cancel_file="")) == 0
        payload = json.loads((output / "relatorio.json").read_text(encoding="utf-8"))
        assert payload["summary"]["ANALISADOS"] == 1 and not payload["failures"]
        assert payload["photos"][0]["preview_method"] == "jpeg_decode"
        copies = root / "copies"
        assert extract(payload["photos"], {"isolated"}, copies).copied == 1
        assert (copies / CATEGORY_DIRS["isolated"] / photo.name).read_bytes() == original
        assert photo.read_bytes() == original
    if raw_path:
        metadata = pyexiv2.Image(str(raw_path))
        try:
            assert metadata.read_exif().get("Exif.Photo.DateTimeOriginal")
        finally:
            metadata.close()
        with rawpy.imread(str(raw_path)) as raw:
            thumb = raw.extract_thumb()
            assert thumb.data is not None
        assert not cv2.CascadeClassifier(str(cascade_path)).empty()
    return True


if __name__ == "__main__":
    raise SystemExit(main())
