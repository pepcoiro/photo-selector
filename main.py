"""
Photo Selector v2.2
Applicazione desktop locale per selezionare fotografie (JPEG, PNG, TIFF e RAW),
segnalare immagini sfocate o con problemi di esposizione, individuare scatti simili
e organizzare la copia o lo spostamento sicuro dei file selezionati (inclusi sidecar .xmp).
"""
from __future__ import annotations

import sys
import shutil
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageFile
from PySide6.QtCore import Qt, QSize, QRunnable, QThreadPool, Signal, QObject
from PySide6.QtGui import QPixmap, QImage, QAction, QKeySequence, QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QFrame, QMessageBox,
    QProgressBar, QCheckBox, QSpinBox, QComboBox, QStatusBar, QProgressDialog
)

ImageFile.LOAD_TRUNCATED_IMAGES = True

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf", ".pef"
}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf", ".pef"}

def compute_edge_acutance(gray_img: np.ndarray) -> float:
    """
    Misura la ripidezza dei bordi salienti (Edge Acutance) anziché la varianza grezza dell'intera area.
    Questo evita i due grandi difetti del Laplaciano classico:
    1. Sfondi vuoti o semplici (studio, cielo, bokeh) non diluiscono più il punteggio del soggetto.
    2. Sfondi caotici (foglie, ghiaia, prato) non mascherano più un soggetto sfocato.
    """
    clean = cv2.medianBlur(gray_img, 3)
    gx = cv2.Sobel(clean, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(clean, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx**2 + gy**2)

    # Escludiamo i pixel piatti (rumore e sfondi privi di dettaglio)
    significant = mag[mag > 15]
    if len(significant) < 25:
        return 0.0

    # Misuriamo la ripidezza dei dettagli reali (90° percentile dei pixel di bordo)
    return float(np.percentile(significant, 90))


@dataclass
class PhotoInfo:
    path: Path
    thumbnail: QPixmap | None = None
    blur_score: float | None = None
    face_detected: bool = False
    exposure_note: str = ""
    analysis_error: str = ""
    selected: bool = False
    duplicate_group: str = ""
    perceptual_hash: int | None = None
    duplicate_best: bool = False


def load_preview(path: Path, max_size=(360, 250)) -> Image.Image:
    """
    Carica un'anteprima dell'immagine; gestisce RAW (con rawpy) e TIFF a 16 bit
    evitando saturazioni anomale del canale colore.
    """
    if path.suffix.lower() in RAW_EXTENSIONS:
        try:
            import rawpy
            with rawpy.imread(str(path)) as raw:
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        import io
                        im = Image.open(io.BytesIO(thumb.data))
                        im.load()
                    else:
                        im = Image.fromarray(thumb.data)
                except Exception:
                    rgb = raw.postprocess(use_camera_wb=True, half_size=True, no_auto_bright=True)
                    im = Image.fromarray(rgb)
        except ImportError as exc:
            raise RuntimeError("Per aprire i RAW installa rawpy (pip install rawpy).") from exc
    else:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)

        # Gestione corretta dei TIFF a 16 bit (I;16, I, F) per evitare immagini bruciate/bianche
        if im.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
            arr = np.asarray(im)
            if arr.dtype == np.uint16:
                arr = (arr / 256).astype(np.uint8)
            elif arr.dtype in (np.float32, np.float64):
                mx = float(arr.max())
                if mx > 0:
                    arr = np.clip((arr / mx) * 255 if mx > 1.0 else arr * 255, 0, 255).astype(np.uint8)
                else:
                    arr = np.zeros(arr.shape, dtype=np.uint8)
            im = Image.fromarray(arr)

        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

    im.thumbnail(max_size, Image.Resampling.LANCZOS)
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im


def pil_to_qimage(im: Image.Image) -> QImage:
    """Converte un'immagine PIL in un QImage thread-safe (usabile nei worker di background)."""
    im = im.convert("RGB")
    arr = np.asarray(im, dtype=np.uint8)
    h, w, _ = arr.shape
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def perceptual_hash(im: Image.Image) -> int:
    """Hash percettivo (pHash) per identificare foto identiche o quasi identiche."""
    gray = im.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    dct = cv2.dct(np.asarray(gray, dtype=np.float32))
    low = dct[:8, :8].flatten()[1:]
    median = float(np.median(low))
    value = 0
    for bit in (low > median).astype(np.uint8):
        value = (value << 1) | int(bit)
    return value


def hash_distance(a: int, b: int) -> int:
    """Distanza di Hamming tra due hash percettivi (richiede Python 3.10+)."""
    return (a ^ b).bit_count()


def analyze_image(path: Path) -> tuple[float | None, bool, str, str, int | None]:
    """
    Analizza la nitidezza e l'esposizione dell'immagine su anteprima 800x600.
    Usa la metrica di Edge Acutance sui dettagli salienti (Sobel sui bordi significativi):
    - Riconosce come nitidi i soggetti su sfondi puliti/studio/bokeh (escludendo le aree piatte).
    - Riconosce come sfocate le immagini con sfondi caotici (foglie, ghiaia) dove il soggetto è fuori fuoco.
    """
    try:
        im = load_preview(path, (800, 600))
        arr = np.asarray(im, dtype=np.uint8)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        p_hash = perceptual_hash(im)

        h_img, w_img = gray.shape
        center_roi = gray[h_img // 4 : 3 * h_img // 4, w_img // 4 : 3 * w_img // 4]

        center_score = compute_edge_acutance(center_roi) if center_roi.size else 0.0
        full_score = compute_edge_acutance(gray)

        # Se il soggetto al centro ha bordi netti e a fuoco (>= 180), la foto è considerata nitida
        # anche se lo sfondo è completamente sfocato o liscio (bokeh / studio).
        if center_score >= 180:
            blur_score = center_score
        else:
            # Se il centro è sfocato, diamo priorità al centro (70%) per evitare che uno sfondo
            # caotico (es. foglie cadute negli angoli) mascheri un soggetto fuori fuoco.
            blur_score = (center_score * 0.7) + (full_score * 0.3)

        very_dark = float(np.mean(gray < 12))
        very_bright = float(np.mean(gray > 245))
        notes = []
        if very_dark > 0.45:
            notes.append("molto scura?")
        if very_bright > 0.30:
            notes.append("alte luci molto forti?")
        return blur_score, False, ", ".join(notes), "", p_hash
    except Exception as exc:
        return None, False, "", str(exc), None


class WorkerSignals(QObject):
    result = Signal(object)
    progress = Signal(int, int)
    finished = Signal()


class ThumbnailSignals(QObject):
    loaded = Signal(int, str, object, str)  # token, path_str, QImage or None, error_str
    progress = Signal(int, int, int)       # token, current, total
    finished = Signal(int)                 # token


class ThumbnailLoaderWorker(QRunnable):
    """Carica le miniature in background emettendo QImage (thread-safe)."""
    def __init__(self, token: int, paths: list[Path]):
        super().__init__()
        self.token = token
        self.paths = paths
        self.signals = ThumbnailSignals()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.paths)
        for i, path in enumerate(self.paths, start=1):
            if self._is_cancelled:
                break
            try:
                preview = load_preview(path, max_size=(360, 250))
                qimg = pil_to_qimage(preview)
                self.signals.loaded.emit(self.token, str(path), qimg, "")
            except Exception as exc:
                self.signals.loaded.emit(self.token, str(path), None, str(exc))
            self.signals.progress.emit(self.token, i, total)
        self.signals.finished.emit(self.token)


class ParallelAnalyzeWorker(QRunnable):
    """Analisi multi-thread parallela con cancellazione pulita."""
    def __init__(self, token: int, paths: list[Path]):
        super().__init__()
        self.token = token
        self.paths = paths
        self.signals = WorkerSignals()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.paths)
        done = 0
        max_workers = min(os.cpu_count() or 4, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(analyze_image, path): path
                for path in self.paths
            }
            for future in as_completed(future_to_path):
                if self._is_cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                path = future_to_path[future]
                done += 1
                try:
                    score, face_detected, exposure, error, p_hash = future.result()
                except Exception as exc:
                    score, face_detected, exposure, error, p_hash = None, False, "", str(exc), None
                self.signals.result.emit((self.token, str(path), score, face_detected, exposure, error, p_hash))
                self.signals.progress.emit(done, total)
        self.signals.finished.emit()


class PhotoCard(QFrame):
    toggled = Signal(str, bool)
    opened = Signal(str)
    focused = Signal(object)

    def __init__(self, info: PhotoInfo, blur_threshold: float):
        super().__init__()
        self.info = info
        self.blur_threshold = blur_threshold
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(215)
        self.setMaximumWidth(270)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_selected_style(info.selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(195, 145)
        self.image_label.setMaximumHeight(170)
        self.set_thumbnail(info.thumbnail)
        self.image_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.image_label.mousePressEvent = self._image_clicked
        self.image_label.mouseDoubleClickEvent = self._image_double_clicked
        layout.addWidget(self.image_label)

        self.name_label = QLabel(info.path.name)
        self.name_label.setWordWrap(True)
        self.name_label.setToolTip(str(info.path))
        self.name_label.setStyleSheet("font-weight: 600;")
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_label.mousePressEvent = self._label_clicked
        layout.addWidget(self.name_label)

        self.status_label = QLabel(self.status_text())
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #E5E5E5; font-size: 11px;")
        self.status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_label.mousePressEvent = self._label_clicked
        layout.addWidget(self.status_label)

        self.checkbox = QCheckBox("Seleziona")
        self.checkbox.setChecked(info.selected)
        self.checkbox.stateChanged.connect(self._checkbox_changed)
        layout.addWidget(self.checkbox)

    def set_thumbnail(self, pixmap: QPixmap | None):
        """Imposta o aggiorna l'anteprima dell'immagine."""
        if pixmap:
            self.image_label.setPixmap(pixmap.scaled(
                QSize(245, 165), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.image_label.setText("Caricamento...")

    def _toggle_selection(self):
        self.checkbox.setChecked(not self.checkbox.isChecked())

    def mousePressEvent(self, event):
        self.focused.emit(self)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space:
            self._toggle_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _image_clicked(self, event):
        self.focused.emit(self)
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_selection()
        event.accept()

    def _image_double_clicked(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit(str(self.info.path))
        event.accept()

    def _label_clicked(self, event):
        self.focused.emit(self)
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_selection()
        event.accept()

    def _checkbox_changed(self, state):
        selected = self.checkbox.isChecked()
        self.set_selected_style(selected)
        self.toggled.emit(str(self.info.path), selected)

    def set_selected(self, selected: bool):
        self.info.selected = selected
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(selected)
        self.checkbox.blockSignals(False)
        self.set_selected_style(selected)

    def set_selected_style(self, selected: bool):
        if selected:
            self.setStyleSheet("""
                QFrame { background: #14213D; border: 2px solid #FCA311; border-radius: 7px; }
                QLabel { color: #ffffff; border: none; background: transparent; }
                QCheckBox { color: #FCA311; border: none; font-weight: 700; spacing: 7px; }
                QCheckBox::indicator { width: 19px; height: 19px; }
            """)
        else:
            self.setStyleSheet("""
                QFrame { background: #14213D; border: 1px solid #14213D; border-radius: 7px; }
                QLabel { color: #ffffff; border: none; background: transparent; }
                QCheckBox { color: #E5E5E5; border: none; font-weight: 600; spacing: 7px; }
                QCheckBox::indicator { width: 19px; height: 19px; }
            """)

    def status_text(self) -> str:
        parts = []
        if self.info.blur_score is None:
            parts.append("Da analizzare")
        elif self.info.blur_score < self.blur_threshold:
            tag = "Possibile sfocatura (su volto)" if self.info.face_detected else "Possibile sfocatura"
            parts.append(f"{tag} (indice {self.info.blur_score:.0f})")
        else:
            tag = "Nitidezza (volto)" if self.info.face_detected else "Nitidezza"
            parts.append(f"{tag}: {self.info.blur_score:.0f}")
        if self.info.exposure_note:
            parts.append(self.info.exposure_note)
        if self.info.analysis_error:
            parts.append("Errore analisi")
        if self.info.duplicate_group:
            if self.info.duplicate_best:
                parts.append("SIMILE · candidata migliore")
            else:
                parts.append(f"SIMILE a {self.info.duplicate_group}")
        return " · ".join(parts)

    def refresh_status(self, threshold: float):
        self.blur_threshold = threshold
        self.status_label.setText(self.status_text())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo Selector")
        self.resize(1250, 850)

        self.current_folder: Path | None = None
        self.photos: dict[str, PhotoInfo] = {}
        self.cards: dict[str, PhotoCard] = {}
        self.thread_pool = QThreadPool.globalInstance()
        self.analyze_worker: ParallelAnalyzeWorker | None = None
        self.thumb_worker: ThumbnailLoaderWorker | None = None
        self._current_columns = 0
        self._load_token = 0
        self._last_focused_card: PhotoCard | None = None

        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        title = QLabel("Photo Selector")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #FFFFFF;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        self.open_button = QPushButton("📂  APRI CARTELLA FOTO")
        self.open_button.setMinimumHeight(42)
        self.open_button.setMinimumWidth(230)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setStyleSheet("""
            QPushButton {
                background: #FCA311; color: #000000; font-weight: 800;
                font-size: 14px; padding: 8px 16px; border-radius: 6px;
                border: 1px solid #FCA311;
            }
            QPushButton:hover { background: #ffb733; }
            QPushButton:pressed { background: #e08f0a; }
        """)
        self.open_button.clicked.connect(self.choose_folder)
        top_bar.addWidget(self.open_button)
        main_layout.addLayout(top_bar)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Segnala sotto:"))
        self.threshold = QSpinBox()
        self.threshold.setRange(1, 1000)
        self.threshold.setValue(180)
        self.threshold.setSuffix(" indice sfocatura")
        self.threshold.setToolTip(
            "Soglia indicativa: più è alta, più immagini vengono segnalate come potenzialmente sfocate."
        )
        self.threshold.valueChanged.connect(self.refresh_cards)
        controls.addWidget(self.threshold)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Tutte le foto", "Solo da controllare / simili", "Solo selezionate"])
        self.filter_combo.currentIndexChanged.connect(self.render_grid)
        controls.addWidget(self.filter_combo)

        self.analyze_button = QPushButton("Analizza foto")
        self.analyze_button.setStyleSheet("""
            QPushButton { background: #FCA311; color: #000000; font-weight: 700; border-radius: 5px; padding: 8px 12px; }
            QPushButton:hover { background: #ffb733; }
        """)
        self.analyze_button.clicked.connect(self.start_analysis)
        controls.addWidget(self.analyze_button)

        self.cancel_button = QPushButton("Ferma analisi")
        self.cancel_button.setStyleSheet("""
            QPushButton { background: #D90429; color: white; font-weight: 700; border-radius: 5px; padding: 8px 12px; }
            QPushButton:hover { background: #EF233C; }
        """)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        controls.addWidget(self.cancel_button)

        self.select_good_button = QPushButton("Seleziona tutte le buone")
        self.select_good_button.setToolTip("Seleziona le foto analizzate prive di anomalie.")
        self.select_good_button.clicked.connect(self.select_good_photos)
        controls.addWidget(self.select_good_button)

        self.select_visible_button = QPushButton("Seleziona visibili")
        self.select_visible_button.clicked.connect(self.select_visible)
        controls.addWidget(self.select_visible_button)

        self.invert_selection_button = QPushButton("Inverti selezione")
        self.invert_selection_button.clicked.connect(self.invert_selection)
        controls.addWidget(self.invert_selection_button)

        self.clear_button = QPushButton("Azzera selezione")
        self.clear_button.clicked.connect(self.clear_selection)
        controls.addWidget(self.clear_button)

        controls.addStretch()
        main_layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        main_layout.addWidget(self.progress)

        self.summary = QLabel("1. Apri una cartella   →   2. Seleziona le foto da tenere   →   3. Copia selezionate nella cartella finale")
        self.summary.setStyleSheet("color: #ffffff; background: #14213D; padding: 10px; border: 1px solid #14213D; border-radius: 5px; font-weight: 600;")
        main_layout.addWidget(self.summary)

        self.welcome = QLabel("Per iniziare, clicca su  📂  APRI CARTELLA FOTO")
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome.setStyleSheet("""
            font-size: 20px; font-weight: 700; color: #E5E5E5;
            background: #14213D; border: 2px dashed #FCA311; border-radius: 12px;
            padding: 42px;
        """)
        main_layout.addWidget(self.welcome)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(10)
        self.scroll.setWidget(self.grid_host)
        self.scroll.setVisible(False)
        main_layout.addWidget(self.scroll, 1)

        bottom_bar = QHBoxLayout()
        hint = QLabel("Suggerimenti: Clicca su una foto per selezionarla. Premi SPAZIO per alternare la selezione. Doppio clic per aprirla.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #E5E5E5; font-size: 12px;")
        bottom_bar.addWidget(hint, 1)

        self.copy_button = QPushButton("COPIA SELEZIONATE NELLA CARTELLA…")
        self.copy_button.setStyleSheet("""
            QPushButton { background: #14213D; color: white; font-weight: 700; border: 1px solid #14213D; border-radius: 5px; padding: 10px 16px; }
            QPushButton:hover { background: #FCA311; color: black; border-color: #FCA311; }
        """)
        self.copy_button.setToolTip("Crea una copia delle foto scelte (inclusi eventuali .xmp) lasciando intatti gli originali.")
        self.copy_button.clicked.connect(lambda: self.transfer_selected(move=False))
        bottom_bar.addWidget(self.copy_button)

        self.move_button = QPushButton("SPOSTA SELEZIONATE…")
        self.move_button.setStyleSheet("""
            QPushButton { background: #14213D; color: #E5E5E5; font-weight: 600; border: 1px solid #333333; border-radius: 5px; padding: 10px 16px; }
            QPushButton:hover { background: #D90429; color: white; border-color: #D90429; }
        """)
        self.move_button.setToolTip("Attenzione: sposta i file (inclusi eventuali .xmp) rimuovendoli dalla cartella di partenza.")
        self.move_button.clicked.connect(lambda: self.transfer_selected(move=True))
        bottom_bar.addWidget(self.move_button)

        main_layout.addLayout(bottom_bar)
        self.setStatusBar(QStatusBar())

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #000000; color: #E5E5E5; }
            QLabel { color: #E5E5E5; }
            QPushButton {
                background: #14213D; color: white;
                border: 1px solid #14213D; border-radius: 5px;
                padding: 8px 12px; font-weight: 600;
            }
            QPushButton:hover { background: #FCA311; color: #000000; border-color: #FCA311; }
            QPushButton:pressed { background: #14213D; border-color: #FCA311; }
            QPushButton:disabled { background: #222222; color: #777777; border-color: #222222; }
            QComboBox, QSpinBox {
                background: #14213D; color: white;
                border: 1px solid #14213D; border-radius: 5px; padding: 6px;
            }
            QComboBox QAbstractItemView { background: #14213D; color: white; selection-background-color: #FCA311; }
            QComboBox:focus, QSpinBox:focus { border: 2px solid #FCA311; }
            QScrollArea { background: #000000; border: none; }
            QProgressBar { background: #14213D; color: white; border: 1px solid #14213D; border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background: #FCA311; }
            QCheckBox { color: #E5E5E5; spacing: 7px; }
            QStatusBar { background: #000000; color: #FCA311; }
        """)

        self._make_shortcuts()

    def _make_shortcuts(self):
        open_action = QAction(self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.choose_folder)
        self.addAction(open_action)

        select_action = QAction(self)
        select_action.setShortcut(QKeySequence("Ctrl+A"))
        select_action.triggered.connect(self.select_visible)
        self.addAction(select_action)

        invert_action = QAction(self)
        invert_action.setShortcut(QKeySequence("Ctrl+I"))
        invert_action.triggered.connect(self.invert_selection)
        self.addAction(invert_action)

        deselect_action = QAction(self)
        deselect_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        deselect_action.triggered.connect(self.clear_selection)
        self.addAction(deselect_action)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space:
            if self._last_focused_card and self._last_focused_card.isVisible():
                self._last_focused_card._toggle_selection()
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_card_focused(self, card: PhotoCard):
        self._last_focused_card = card

    def is_photo_flagged(self, info: PhotoInfo) -> bool:
        """Verifica coerente se una foto è considerata 'da controllare'."""
        if info.blur_score is None:
            return True
        return (
            info.blur_score < self.threshold.value() or
            bool(info.exposure_note) or
            bool(info.analysis_error) or
            bool(info.duplicate_group)
        )

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Scegli la cartella con le fotografie")
        if not folder:
            return
        self.load_folder(Path(folder))

    def load_folder(self, folder: Path):
        """Carica l'elenco dei file e avvia il caricamento asincrono, annullando task precedenti."""
        self._load_token += 1
        current_token = self._load_token

        # Annulla eventuale analisi in corso
        if self.analyze_worker:
            self.analyze_worker.cancel()
            self.analyze_worker = None
            self.analyze_button.setEnabled(True)
            self.cancel_button.setVisible(False)

        # Annulla eventuale caricamento miniature in corso
        if self.thumb_worker:
            self.thumb_worker.cancel()
            self.thumb_worker = None

        paths = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS],
                       key=lambda p: p.name.lower())
        self.current_folder = folder
        self.welcome.setVisible(False)
        self.scroll.setVisible(True)
        self.photos.clear()
        self._last_focused_card = None

        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.cards.clear()

        for path in paths:
            info = PhotoInfo(path=path)
            self.photos[str(path)] = info
            card = PhotoCard(info, self.threshold.value())
            card.toggled.connect(self.on_toggled)
            card.opened.connect(self.open_file)
            card.focused.connect(self._on_card_focused)
            self.cards[str(path)] = card

        self._current_columns = 0
        self.render_grid()
        self.summary.setText(f"{len(paths)} fotografie trovate in: {folder.name}")
        self.statusBar().showMessage("Caricamento miniature in corso...")

        if not paths:
            QMessageBox.information(self, "Nessuna foto", "Non ho trovato immagini supportate nella cartella scelta.")
            return

        self.progress.setVisible(True)
        self.progress.setRange(0, len(paths))
        self.progress.setValue(0)
        self.thumb_worker = ThumbnailLoaderWorker(current_token, paths)
        self.thumb_worker.signals.loaded.connect(self._on_thumbnail_loaded)
        self.thumb_worker.signals.progress.connect(self._on_thumbnail_progress)
        self.thumb_worker.signals.finished.connect(self._on_thumbnails_finished)
        self.thread_pool.start(self.thumb_worker)

    def _on_thumbnail_loaded(self, token: int, path_str: str, qimg: QImage | None, error: str):
        if token != self._load_token:
            return
        pixmap = QPixmap.fromImage(qimg) if qimg else None
        if path_str in self.photos:
            self.photos[path_str].thumbnail = pixmap
            if error:
                self.photos[path_str].analysis_error = error
        if path_str in self.cards:
            self.cards[path_str].set_thumbnail(pixmap)
            if error:
                self.cards[path_str].refresh_status(self.threshold.value())

    def _on_thumbnail_progress(self, token: int, current: int, total: int):
        if token != self._load_token:
            return
        self.progress.setValue(current)
        self.statusBar().showMessage(f"Caricamento miniature: {current}/{total}")

    def _on_thumbnails_finished(self, token: int):
        if token != self._load_token:
            return
        self.thumb_worker = None
        self.progress.setVisible(False)
        self.statusBar().showMessage("Miniature pronte. Premi «Analizza foto» per i controlli automatici.")

    def render_grid(self):
        """Dispone le card visibili nella griglia in modo efficiente."""
        mode = self.filter_combo.currentIndex()
        viewport_w = self.scroll.viewport().width() if self.scroll.viewport().width() > 100 else self.width()
        columns = max(1, viewport_w // 270)
        self._current_columns = columns

        idx = 0
        for key, card in self.cards.items():
            info = self.photos.get(key)
            if not info:
                card.setVisible(False)
                continue

            visible = True
            if mode == 1:
                visible = self.is_photo_flagged(info)
            elif mode == 2:
                visible = info.selected

            card.setVisible(visible)
            if visible:
                self.grid.addWidget(card, idx // columns, idx % columns)
                idx += 1

        self.update_summary()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "photos") and self.photos:
            viewport_w = self.scroll.viewport().width() if self.scroll.viewport().width() > 100 else self.width()
            new_columns = max(1, viewport_w // 270)
            if new_columns != self._current_columns:
                self.render_grid()

    def on_toggled(self, key: str, checked: bool):
        if key in self.photos:
            self.photos[key].selected = checked
        if self.filter_combo.currentIndex() == 2:
            self.render_grid()
        else:
            self.update_summary()

    def update_summary(self):
        total = len(self.photos)
        selected = sum(1 for p in self.photos.values() if p.selected)
        flagged = sum(1 for p in self.photos.values() if self.is_photo_flagged(p))
        self.summary.setText(f"{total} foto · {selected} selezionate · {flagged} da controllare")

    def refresh_cards(self):
        thresh = self.threshold.value()
        for card in self.cards.values():
            card.refresh_status(thresh)
        self.update_summary()
        if self.filter_combo.currentIndex() == 1:
            self.render_grid()

    def start_analysis(self):
        if not self.photos:
            QMessageBox.information(self, "Nessuna cartella", "Apri prima una cartella con fotografie.")
            return
        if self.analyze_worker is not None:
            QMessageBox.information(self, "Analisi in corso", "L'analisi è già in corso.")
            return

        self.analyze_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.photos))
        self.progress.setValue(0)

        paths = [info.path for info in self.photos.values()]
        self.analyze_worker = ParallelAnalyzeWorker(self._load_token, paths)
        self.analyze_worker.signals.result.connect(self.analysis_result)
        self.analyze_worker.signals.progress.connect(self.analysis_progress)
        self.analyze_worker.signals.finished.connect(self.analysis_finished)
        self.thread_pool.start(self.analyze_worker)

    def cancel_analysis(self):
        if self.analyze_worker:
            self.statusBar().showMessage("Annullamento analisi in corso...")
            self.analyze_worker.cancel()
            self.cancel_button.setEnabled(False)

    def analysis_result(self, result):
        token, path, score, face_detected, exposure, error, p_hash = result
        if token != self._load_token:
            return
        if path in self.photos:
            self.photos[path].blur_score = score
            self.photos[path].face_detected = face_detected
            self.photos[path].exposure_note = exposure
            self.photos[path].analysis_error = error
            self.photos[path].perceptual_hash = p_hash
            if path in self.cards:
                self.cards[path].refresh_status(self.threshold.value())

    def analysis_progress(self, current: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.statusBar().showMessage(f"Analisi parallela: {current}/{total} foto completate")

    def detect_similar_photos(self):
        """Identifica scatti quasi identici raggruppandoli e premiando il più nitido."""
        items = [(info.path.name, info.perceptual_hash, info)
                 for info in self.photos.values() if info.perceptual_hash is not None]
        for _, _, info in items:
            info.duplicate_group = ""
            info.duplicate_best = False

        groups = []
        used = set()
        for i, (name_a, hash_a, info_a) in enumerate(items):
            if i in used:
                continue
            group = [(i, name_a, info_a)]
            for j in range(i + 1, len(items)):
                if j in used:
                    continue
                name_b, hash_b, info_b = items[j]
                if hash_distance(hash_a, hash_b) <= 6:
                    group.append((j, name_b, info_b))
                    used.add(j)
            if len(group) > 1:
                used.add(i)
                groups.append(group)

        for group in groups:
            best = max(group, key=lambda entry: (
                entry[2].blur_score if entry[2].blur_score is not None else -1,
                entry[1].lower()
            ))
            best_name, best_info = best[1], best[2]
            best_info.duplicate_best = True
            for _, name, info in group:
                info.duplicate_group = "gruppo simile" if name == best_name else best_name

    def analysis_finished(self):
        self.analyze_worker = None
        self.analyze_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(False)
        self.detect_similar_photos()
        self.refresh_cards()
        self.statusBar().showMessage("Analisi completata. Le segnalazioni sono indicative: verifica sempre prima di scartare.")

    def select_good_photos(self):
        if not self.photos:
            QMessageBox.information(self, "Nessuna cartella", "Apri prima una cartella con fotografie.")
            return
        unanalyzed = any(info.blur_score is None and not info.analysis_error for info in self.photos.values())
        if unanalyzed:
            QMessageBox.information(
                self, "Prima analizza le foto",
                "Premi prima «Analizza foto». Il programma potrà così selezionare le immagini senza anomalie."
            )
            return

        currently_selected = sum(1 for p in self.photos.values() if p.selected)
        replace_existing = True
        if currently_selected > 0:
            box = QMessageBox(self)
            box.setWindowTitle("Selezione foto buone")
            box.setText(f"Ci sono già {currently_selected} foto selezionate manualmente.")
            box.setInformativeText("Come vuoi procedere?")
            btn_replace = box.addButton("Sostituisci selezione", QMessageBox.ButtonRole.AcceptRole)
            btn_add = box.addButton("Aggiungi alle selezionate", QMessageBox.ButtonRole.ActionRole)
            btn_cancel = box.addButton("Annulla", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == btn_cancel:
                return
            replace_existing = (clicked == btn_replace)

        good_count = 0
        thresh = self.threshold.value()
        for key, info in self.photos.items():
            is_good = (
                info.blur_score is not None
                and info.blur_score >= thresh
                and not info.exposure_note
                and not info.analysis_error
                and (not info.duplicate_group or info.duplicate_best)
            )
            if replace_existing:
                info.selected = is_good
            else:
                info.selected = info.selected or is_good

            if key in self.cards:
                self.cards[key].set_selected(info.selected)
            if is_good:
                good_count += 1

        self.update_summary()
        if self.filter_combo.currentIndex() == 2:
            self.render_grid()

        QMessageBox.information(
            self, "Selezione completata",
            f"Individuate {good_count} foto senza segnalazioni; per ogni gruppo simile è rimasta candidata quella con nitidezza maggiore."
        )

    def select_visible(self):
        for key, card in self.cards.items():
            if card.isVisible():
                card.set_selected(True)
                self.photos[key].selected = True
        self.update_summary()

    def invert_selection(self):
        """Inverte la selezione attuale di tutte le foto visibili."""
        for key, card in self.cards.items():
            if card.isVisible():
                new_state = not self.photos[key].selected
                card.set_selected(new_state)
                self.photos[key].selected = new_state
        self.update_summary()

    def clear_selection(self):
        for key, card in self.cards.items():
            card.set_selected(False)
            self.photos[key].selected = False
        self.update_summary()
        if self.filter_combo.currentIndex() == 2:
            self.render_grid()

    def open_file(self, path_str: str):
        """Apre l'immagine con il visualizzatore predefinito del sistema operativo."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(path_str)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path_str])
            else:
                subprocess.Popen(["xdg-open", path_str])
        except Exception as exc:
            QMessageBox.warning(self, "Impossibile aprire", str(exc))

    def _find_sidecars(self, source: Path) -> list[Path]:
        """Trova eventuali file sidecar (.xmp) associati alla foto."""
        candidates = [
            source.with_suffix(".xmp"),
            source.with_suffix(".XMP"),
            source.with_name(source.name + ".xmp"),
            source.with_name(source.name + ".XMP"),
        ]
        found = []
        for c in candidates:
            if c.exists() and c.is_file() and c not in found:
                found.append(c)
        return found

    def transfer_selected(self, move: bool):
        selected = [p.path for p in self.photos.values() if p.selected]
        if not selected:
            QMessageBox.information(self, "Nessuna selezione", "Seleziona prima almeno una fotografia.")
            return

        destination = QFileDialog.getExistingDirectory(self, "Scegli la cartella di destinazione")
        if not destination:
            return
        dest = Path(destination)
        if self.current_folder and dest.resolve() == self.current_folder.resolve():
            QMessageBox.warning(self, "Destinazione non valida", "Scegli una cartella diversa da quella di origine.")
            return

        operation = "spostare" if move else "copiare"
        answer = QMessageBox.question(
            self, "Conferma operazione",
            f"Vuoi {operation} {len(selected)} foto nella cartella:\n\n{dest}\n\n"
            + ("I file originali (ed eventuali file sidecar .xmp associati) verranno rimossi dall'origine." if move
               else "I file originali rimarranno intatti."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        progress_dialog = QProgressDialog(f"{operation.capitalize()} in corso...", "Annulla", 0, len(selected), self)
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(150)

        done = 0
        errors = []
        was_aborted = False

        for i, source in enumerate(selected):
            if progress_dialog.wasCanceled():
                was_aborted = True
                break
            progress_dialog.setValue(i)
            progress_dialog.setLabelText(f"File {i + 1}/{len(selected)}: {source.name}")
            QApplication.processEvents()

            # Gestione del file principale
            target = dest / source.name
            if target.exists():
                stem, suffix = source.stem, source.suffix
                n = 1
                while (dest / f"{stem}_{n}{suffix}").exists():
                    n += 1
                target = dest / f"{stem}_{n}{suffix}"

            try:
                if move:
                    shutil.move(str(source), str(target))
                    self.photos.pop(str(source), None)
                    if str(source) in self.cards:
                        self.cards[str(source)].deleteLater()
                        self.cards.pop(str(source), None)
                else:
                    shutil.copy2(str(source), str(target))
                done += 1

                # Sposta/copia anche eventuali file sidecar (.xmp)
                for sidecar in self._find_sidecars(source):
                    sidecar_target = dest / sidecar.name
                    if move:
                        shutil.move(str(sidecar), str(sidecar_target))
                    else:
                        shutil.copy2(str(sidecar), str(sidecar_target))

            except Exception as exc:
                errors.append(f"{source.name}: {exc}")

        progress_dialog.setValue(len(selected))

        if move and done > 0:
            self.detect_similar_photos()

        self.render_grid()

        if was_aborted:
            msg = f"Operazione interrotta dall'utente. Trasferiti {done} su {len(selected)} file."
        else:
            msg = f"Operazione completata con successo: {done} file trasferiti."

        if errors:
            msg += "\n\nProblemi riscontrati:\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Risultato", msg)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Selector")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
