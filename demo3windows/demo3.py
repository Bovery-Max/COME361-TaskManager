
"""
Task Manager Lite - v1.3 (GUI katmanı)
COME 361 - İşletim Sistemleri

- Veri toplama QThread'de çalışır: GUI thread'i psutil beklemez, arayüz donmaz.
- Tüm prosesler listelenir; sütuna tıklayarak sıralama, isim/PID araması.
- Durum (process state), CPU %, Bellek, Parent PID; canlı CPU/RAM grafikleri.
- Askıya Al / Devam Ettir (SIGSTOP / SIGCONT) ve doğrulamalı sonlandırma.
"""
import os
import sys
import shlex
import time
from collections import deque
from datetime import datetime

import psutil
from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QBrush, QPainter, QPainterPath, QPalette
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QPushButton, QLineEdit, QLabel, QMessageBox, QHeaderView,
    QGroupBox, QInputDialog, QAbstractItemView, QComboBox,
)

# Ekip arkadaşımızın backend modülü (proje kökünde)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from process_ops import list_processes, terminate_process, start_process

REFRESH_MS = 5000
REFRESH_OPTIONS = [("1 sn", 1000), ("2 sn", 2000), ("5 sn", 5000), ("10 sn", 10000)]
MB = 1024 * 1024
CPU_COUNT = psutil.cpu_count() or 1
IS_WINDOWS = os.name == "nt"
PROTECTED_PIDS = {0, 1, 4, os.getpid()}   # Idle, init, Windows System, kendimiz
SORT_ROLE = Qt.ItemDataRole.UserRole

# psutil durum kodu -> (Türkçe etiket, renk)
STATUS_INFO = {
    "running": ("Çalışıyor", "#66bb6a"), "sleeping": ("Uykuda", "#64b5f6"),
    "disk-sleep": ("G/Ç Bekliyor", "#ffca28"), "stopped": ("Askıda", "#ffa726"),
    "zombie": ("Zombi", "#ef5350"), "idle": ("Boşta", "#9e9e9e"),
}
COLUMNS = ["PID", "İşlem Adı", "Durum", "CPU (%)", "Bellek (MB)", "Parent PID"]
C_PID, C_NAME, C_STATUS, C_CPU, C_MEM, C_PPID = range(6)
HINT_MSG = "Detayları görmek için tablodan bir işleme tıklayın."


def status_label(status):
    return STATUS_INFO.get(status, (status or "?", "#9a9a9a"))


def _safe(fn, default=None):
    """Tek bir psutil alanını okur; yetki yoksa varsayılanı döner.
    NoSuchProcess (işlem öldü) bilerek yukarı fırlatılır; ZombieProcess onun
    alt sınıfı olduğu için önce yakalanır, yoksa zombiler listeden düşer."""
    try:
        return fn()
    except psutil.ZombieProcess:
        return default
    except psutil.NoSuchProcess:
        raise
    except (psutil.Error, OSError):
        return default


# ---------------------------------------------------------------------------
# Veri toplama (arka plan thread'inde; Qt widget'larına DOKUNMAZ)
# ---------------------------------------------------------------------------
def collect_snapshot(cache):
    """cache: {(pid, create_time): psutil.Process}. Process nesneleri saklanır,
    çünkü proses başına cpu_percent() bir önceki çağrıya göre hesaplanır."""
    t0 = time.perf_counter()
    rows, seen = [], set()
    for base in list_processes():
        pid, ctime = base.get("pid"), base.get("create_time")
        if pid is None:
            continue
        key = (pid, ctime)
        row = {"pid": pid, "name": base.get("name") or "", "ppid": base.get("ppid"),
               "create_time": ctime, "status": "?", "cpu": None, "mem": None}
        try:
            proc = cache.get(key) or cache.setdefault(key, psutil.Process(pid))
            with proc.oneshot():
                row["status"] = _safe(proc.status, "?")
                cpu = _safe(lambda: proc.cpu_percent(None))
                row["cpu"] = None if cpu is None else cpu / CPU_COUNT
                mem = _safe(proc.memory_info)
                row["mem"] = None if mem is None else mem.rss / MB
        except psutil.NoSuchProcess:
            cache.pop(key, None)
            continue
        seen.add(key)
        rows.append(row)

    for key in [k for k in cache if k not in seen]:   # ölenleri önbellekten at
        del cache[key]

    vm = psutil.virtual_memory()
    return {"rows": rows, "cpu": psutil.cpu_percent(None), "ram": vm.percent,
            "ram_used": (vm.total - vm.available) / 1024 ** 3, "ram_total": vm.total / 1024 ** 3,
            "ms": (time.perf_counter() - t0) * 1000}


class DataWorker(QObject):
    """QThread'e taşınır; sonucu sinyalle ana thread'e yollar."""
    data_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cache = {}
        psutil.cpu_percent(None)   # ilk ölçüm hep 0.0 döner; ısınma çağrısı

    @pyqtSlot()
    def fetch(self):
        try:
            self.data_ready.emit(collect_snapshot(self._cache))
        except Exception as e:
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Arayüz bileşenleri
# ---------------------------------------------------------------------------
class Graph(QWidget):
    """Son 60 ölçümü gösteren canlı çizgi grafik (QPainter, ek kütüphane yok)."""

    def __init__(self, title, color):
        super().__init__()
        self.title, self.color, self.sub = title, QColor(color), ""
        self.values = deque(maxlen=60)
        self.setMinimumHeight(110)

    def add(self, value, sub=""):
        self.values.append(max(0.0, min(100.0, value)))
        self.sub = sub
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QColor("#555"))
        p.setBrush(QColor("#1e1e1e"))
        p.drawRoundedRect(r, 6, 6)

        head = r.adjusted(10, 8, -10, 0)
        p.setPen(QColor("#e0e0e0"))
        p.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   f"{self.title}   {self.sub}")
        p.setPen(self.color)
        p.drawText(head, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                   f"%{self.values[-1] if self.values else 0:.1f}")

        if len(self.values) < 2:
            return
        g = r.adjusted(10, 30, -10, -8)
        step = g.width() / 59
        x0 = g.right() - step * (len(self.values) - 1)       # en yeni değer sağda
        pts = [(x0 + i * step, g.bottom() - g.height() * v / 100) for i, v in enumerate(self.values)]
        line = QPainterPath()
        line.moveTo(*pts[0])
        for pt in pts[1:]:
            line.lineTo(*pt)
        fill = QPainterPath(line)
        fill.lineTo(pts[-1][0], g.bottom())
        fill.lineTo(pts[0][0], g.bottom())
        fill.closeSubpath()
        soft = QColor(self.color)
        soft.setAlpha(50)
        p.fillPath(fill, QBrush(soft))
        p.setPen(self.color)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line)


class SortItem(QTableWidgetItem):
    """Metin yerine SORT_ROLE'deki değere göre sıralar (PID/CPU/Bellek sayısal)."""

    def __lt__(self, other):
        return self.data(SORT_ROLE) < other.data(SORT_ROLE)


def make_item(text, sort_value, right=False, color=None):
    it = SortItem(text)
    it.setData(SORT_ROLE, sort_value)
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if color:
        it.setForeground(QBrush(QColor(color)))
    return it


STYLE_SHEET = """
QLabel { color: #e0e0e0; font-weight: bold; }
QGroupBox { border: 1px solid #555; border-radius: 6px; margin-top: 12px; padding-top: 8px; color: white; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTableWidget { background-color: #1e1e1e; alternate-background-color: #252525; color: #e0e0e0;
               border: 1px solid #555; selection-background-color: #1f6aa5; selection-color: white; }
QHeaderView::section { background-color: #333; color: white; border: none;
               border-right: 1px solid #444; padding: 5px; font-weight: bold; }
QLineEdit { background-color: #1e1e1e; color: white; border: 1px solid #555; padding: 6px; border-radius: 4px; }
QPushButton { background-color: #333; color: white; padding: 8px 15px; border: 1px solid #555;
              border-radius: 4px; font-weight: bold; }
QPushButton:hover { background-color: #444; }
QPushButton:disabled { color: #777; background-color: #2e2e2e; }
QPushButton#btn_kill { background-color: #c9302c; border: none; }
QPushButton#btn_kill:hover { background-color: #d9534f; }
QPushButton#btn_kill:disabled { background-color: #4a2524; color: #999; }
QPushButton#btn_suspend { background-color: #a86400; border: none; }
QPushButton#btn_suspend:hover { background-color: #c27400; }
QPushButton#btn_suspend:disabled { background-color: #45331a; color: #999; }
QStatusBar { background: #232323; border-top: 1px solid #3a3a3a; }
QComboBox { background: #1e1e1e; color: white; border: 1px solid #555; border-radius: 3px; padding: 2px 8px; }
QComboBox QAbstractItemView { background: #1e1e1e; color: white; selection-background-color: #1f6aa5; }
"""


def apply_dark_theme(app):
    """Fusion + koyu palet: her işletim sisteminde aynı görünüm (mor seçim rengi de kalkar)."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: "#2b2b2b", QPalette.ColorRole.WindowText: "#e0e0e0",
        QPalette.ColorRole.Base: "#1e1e1e", QPalette.ColorRole.AlternateBase: "#252525",
        QPalette.ColorRole.Text: "#e0e0e0", QPalette.ColorRole.Button: "#333333",
        QPalette.ColorRole.ButtonText: "#ffffff", QPalette.ColorRole.Highlight: "#1f6aa5",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.PlaceholderText: "#888888",
    }.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------
class TaskManagerWindow(QMainWindow):
    request_fetch = pyqtSignal()   # ana thread -> işçi thread

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Task Manager Lite - v1.3")
        self.resize(950, 760)
        self.setStyleSheet(STYLE_SHEET)

        self.rows = {}               # pid -> son ölçümdeki satır verisi
        self.selected_pid = None     # seçim satıra değil PID'ye bağlı tutulur
        self._restoring = False      # programatik seçim değişikliklerini yok say
        self._fetching = False

        self._build_ui()
        self._build_worker()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request_refresh)
        self.timer.start(REFRESH_MS)
        self.request_refresh()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # 1. CPU / RAM grafikleri
        graphs = QHBoxLayout()
        self.cpu_graph = Graph("CPU", "#4aa3ff")
        self.ram_graph = Graph("RAM", "#b388ff")
        graphs.addWidget(self.cpu_graph)
        graphs.addWidget(self.ram_graph)
        layout.addLayout(graphs)

        # 2. Arama + yeni görev
        top = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("İşlem adına veya PID'ye göre filtrele...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.apply_filter)
        btn_start = QPushButton("Yeni Görevi Çalıştır")
        btn_start.clicked.connect(self.run_new_process)
        self.lbl_count = QLabel("Listelenen İşlem: 0")
        top.addWidget(self.search_bar, 1)
        top.addWidget(btn_start)
        top.addWidget(self.lbl_count)
        layout.addLayout(top)

        # 3. Proses tablosu
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_MEM, Qt.SortOrder.DescendingOrder)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.table, 1)

        # 4. Alt panel: detaylar + butonlar
        bottom = QHBoxLayout()
        box = QGroupBox("Seçili İşlem Detayları")
        inner = QVBoxLayout(box)
        self.lbl_details = QLabel(HINT_MSG)
        self.lbl_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(self.lbl_details)
        bottom.addWidget(box, 3)

        buttons = QVBoxLayout()
        buttons.addStretch()
        self.btn_suspend = QPushButton("Askıya Al")
        self.btn_suspend.setObjectName("btn_suspend")
        self.btn_suspend.clicked.connect(self.toggle_suspend)
        self.btn_kill = QPushButton("Görevi Sonlandır")
        self.btn_kill.setObjectName("btn_kill")
        self.btn_kill.clicked.connect(self.kill_selected)
        buttons.addWidget(self.btn_suspend)
        buttons.addWidget(self.btn_kill)
        bottom.addLayout(buttons, 1)
        layout.addLayout(bottom)
        self.btn_suspend.setEnabled(False)
        self.btn_kill.setEnabled(False)

        self.lbl_updated = QLabel("Veri bekleniyor…")
        self.combo_interval = QComboBox()
        for text, ms in REFRESH_OPTIONS:
            self.combo_interval.addItem(text, ms)
        self.combo_interval.setCurrentIndex([ms for _, ms in REFRESH_OPTIONS].index(REFRESH_MS))
        self.combo_interval.currentIndexChanged.connect(
            lambda i: self.timer.setInterval(self.combo_interval.itemData(i)))
        self.btn_pause = QPushButton("Duraklat")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setStyleSheet("padding: 3px 12px; font-weight: normal;")
        self.btn_pause.toggled.connect(self.on_pause_toggled)
        self.statusBar().addPermanentWidget(self.lbl_updated)
        self.statusBar().addPermanentWidget(QLabel("  Yenileme:"))
        self.statusBar().addPermanentWidget(self.combo_interval)
        self.statusBar().addPermanentWidget(self.btn_pause)

    def _build_worker(self):
        self.worker_thread = QThread(self)
        self.worker = DataWorker()            # parent verilmez (moveToThread için)
        self.worker.moveToThread(self.worker_thread)
        self.request_fetch.connect(self.worker.fetch)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.failed.connect(self.on_fetch_failed)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.start()

    # ------------------------------------------------------------ Veri akışı
    def request_refresh(self):
        """Önceki ölçüm bitmeden yenisini başlatmaz (istekler üst üste binmez)."""
        if not self._fetching:
            self._fetching = True
            self.request_fetch.emit()

    def on_pause_toggled(self, paused):
        """Otomatik yenilemeyi durdurur/başlatır; duraklatılmışken satır seçmek rahatlaşır."""
        self.btn_pause.setText("Devam" if paused else "Duraklat")
        if paused:
            self.timer.stop()
            self.statusBar().showMessage("Otomatik yenileme duraklatıldı.")
        else:
            self.timer.start()
            self.statusBar().clearMessage()
            self.request_refresh()

    def on_fetch_failed(self, message):
        self._fetching = False
        self.statusBar().showMessage(f"Veri alınamadı: {message}", 5000)

    def on_data_ready(self, snap):
        self._fetching = False
        self.rows = {r["pid"]: r for r in snap["rows"]}
        self.cpu_graph.add(snap["cpu"], f"{CPU_COUNT} mantıksal çekirdek")
        self.ram_graph.add(snap["ram"], f"{snap['ram_used']:.1f} / {snap['ram_total']:.1f} GB")
        self.fill_table(snap["rows"])
        self.apply_filter()
        self.update_details()
        self.lbl_updated.setText(f"Son güncelleme: {datetime.now():%H:%M:%S} · toplama {snap['ms']:.0f} ms")

    def fill_table(self, rows):
        """Tabloyu yeniler; sıralama düzenini, kaydırma konumunu ve seçili PID'yi korur."""
        bar = self.table.verticalScrollBar()
        scroll = bar.value()
        self._restoring = True
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)        # doldururken sıralama satırları kaydırmasın
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            label, color = status_label(r["status"])
            cpu, mem = r["cpu"], r["mem"]
            self.table.setItem(i, C_PID, make_item(str(r["pid"]), r["pid"], right=True))
            self.table.setItem(i, C_NAME, make_item(r["name"], r["name"].lower()))
            self.table.setItem(i, C_STATUS, make_item(label, label, color=color))
            self.table.setItem(i, C_CPU, make_item("—" if cpu is None else f"{cpu:.1f}",
                                                   -1.0 if cpu is None else cpu, right=True))
            self.table.setItem(i, C_MEM, make_item("—" if mem is None else f"{mem:.1f}",
                                                   -1.0 if mem is None else mem, right=True))
            self.table.setItem(i, C_PPID, make_item("—" if r["ppid"] is None else str(r["ppid"]),
                                                    -1 if r["ppid"] is None else r["ppid"], right=True))
        self.table.setSortingEnabled(True)         # indikatördeki sütuna göre yeniden sıralar
        self.table.clearSelection()
        for i in range(self.table.rowCount()):     # satır sıralamayla yer değiştirmiş olabilir
            if self.table.item(i, C_PID).data(SORT_ROLE) == self.selected_pid:
                self.table.selectRow(i)
                break
        self.table.setUpdatesEnabled(True)
        bar.setValue(scroll)
        self._restoring = False

    def apply_filter(self):
        text = self.search_bar.text().strip().lower()
        visible = 0
        for i in range(self.table.rowCount()):
            name = self.table.item(i, C_NAME).text().lower()
            pid = self.table.item(i, C_PID).text()
            show = not text or text in name or text in pid
            self.table.setRowHidden(i, not show)
            visible += show
        total = self.table.rowCount()
        self.lbl_count.setText(f"Listelenen İşlem: {visible} / {total}" if text
                               else f"Listelenen İşlem: {total}")

    # ------------------------------------------------------- Seçim ve detaylar
    def on_selection_changed(self):
        if self._restoring:
            return
        sel = self.table.selectionModel().selectedRows()
        self.selected_pid = self.table.item(sel[0].row(), C_PID).data(SORT_ROLE) if sel else None
        self.update_details()

    def update_details(self):
        row = self.rows.get(self.selected_pid)
        if row is None:
            msg = HINT_MSG if self.selected_pid is None else f"PID {self.selected_pid} artık çalışmıyor."
            self.selected_pid = None
            self.lbl_details.setText(msg)
            self.btn_kill.setEnabled(False)
            self.btn_suspend.setEnabled(False)
            return
        try:
            p = psutil.Process(row["pid"])
            with p.oneshot():
                user = _safe(p.username, "Erişim yok")
                threads = _safe(p.num_threads, "Erişim yok")
        except psutil.NoSuchProcess:
            user = threads = "—"
        parent = self.rows.get(row["ppid"])
        parent_text = "—" if row["ppid"] is None else (
            f"{row['ppid']} ({parent['name']})" if parent else f"{row['ppid']} (çalışmıyor)")
        started = datetime.fromtimestamp(row["create_time"]).strftime("%d.%m.%Y %H:%M:%S") \
            if row["create_time"] else "—"
        self.lbl_details.setText(
            f"PID: {row['pid']}  |  Parent PID: {parent_text}\n"
            f"Kullanıcı: {user}  |  Thread Sayısı: {threads}\n"
            f"Durum: {status_label(row['status'])[0]} ({row['status']})  |  Başlangıç: {started}")
        self.btn_kill.setEnabled(True)
        self.btn_suspend.setEnabled(row["pid"] not in PROTECTED_PIDS)
        self.btn_suspend.setText("Devam Ettir" if row["status"] == "stopped" else "Askıya Al")

    # ------------------------------------------------------- İşlem kontrolü
    def _guard(self, name, action):
        """action()'ı çalıştırır, OS hatalarını diyalogla gösterir. Başarılıysa True."""
        try:
            action()
            return True
        except ValueError as e:                       # backend: korumalı PID
            QMessageBox.warning(self, "Engellendi", str(e))
        except psutil.AccessDenied:
            QMessageBox.critical(self, "Yetki Hatası", f"{name} için Yönetici (Admin) yetkisi gerekiyor.")
        except psutil.NoSuchProcess:
            QMessageBox.warning(self, "Bulunamadı", f"{name} işlemi zaten sonlanmış.")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"İşlem gerçekleştirilemedi.\nDetay: {e}")
        return False

    def _process_of(self, row):
        """PID yeniden kullanılmışsa yanlış işleme dokunmamak için create_time kontrolü."""
        p = psutil.Process(row["pid"])
        if row["create_time"] and abs(p.create_time() - row["create_time"]) > 0.01:
            raise psutil.NoSuchProcess(row["pid"])
        return p

    def run_new_process(self):
        hint = "notepad, mspaint, calc" if IS_WINDOWS else "gedit, xterm, sleep 60"
        cmd, ok = QInputDialog.getText(self, "Yeni Görev Çalıştır", f"Çalıştırılacak komut (örn: {hint}):")
        if not ok or not cmd.strip():
            return
        try:
            parts = [s.strip('"') for s in shlex.split(cmd.strip(), posix=not IS_WINDOWS)]
            new_pid = start_process(parts[0], parts[1:])
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"İşlem başlatılamadı: {e}")
            return
        self.statusBar().showMessage(f"'{cmd.strip()}' başlatıldı (PID: {new_pid})", 5000)
        self.selected_pid = new_pid
        self.request_refresh()

    def toggle_suspend(self):
        row = self.rows.get(self.selected_pid)
        if row is None:
            return
        resume = row["status"] == "stopped"
        action = (lambda: self._process_of(row).resume()) if resume \
            else (lambda: self._process_of(row).suspend())
        if self._guard(row["name"], action):
            sig = "SIGCONT" if resume else "SIGSTOP"
            self.statusBar().showMessage(
                f"{row['name']} (PID {row['pid']}) {'devam ettirildi' if resume else 'askıya alındı'} ({sig})", 5000)
            self.request_refresh()

    def kill_selected(self):
        row = self.rows.get(self.selected_pid)
        if row is None:
            return
        if QMessageBox.question(self, "Onay", f"{row['name']} (PID: {row['pid']}) sonlandırılsın mı?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        # Backend: korumalı PID kontrolü + create_time doğrulaması
        if self._guard(row["name"], lambda: terminate_process(row["pid"], expected_create_time=row["create_time"])):
            self.statusBar().showMessage(f"{row['name']} için sonlandırma sinyali gönderildi, doğrulanıyor…")
            QTimer.singleShot(1500, lambda: self.verify_termination(row))   # UI'ı bloklamadan kontrol

    def verify_termination(self, row):
        """terminate() sadece sinyal gönderir; işlem gerçekten kapandı mı kontrol edilir."""
        name = row["name"]
        try:
            status = self._process_of(row).status()
        except psutil.NoSuchProcess:
            status = None
        except psutil.AccessDenied:
            status = "?"
        if status is None:
            QMessageBox.information(self, "Başarılı", f"{name} başarıyla sonlandırıldı.")
        elif status == "zombie":
            QMessageBox.information(self, "Zombi İşlem", f"{name} sonlandı ancak ZOMBİ durumunda: "
                                    "parent işlem henüz wait() çağırıp çıkış kodunu toplamadı.")
        elif QMessageBox.question(self, "İşlem hâlâ çalışıyor",
                                  f"{name} sonlandırma sinyaline (SIGTERM) yanıt vermedi.\nZorla kapatılsın mı? (SIGKILL)",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                  ) == QMessageBox.StandardButton.Yes:
            self._guard(name, lambda: self._process_of(row).kill())
        self.request_refresh()

    def closeEvent(self, event):
        self.timer.stop()
        self.worker_thread.quit()
        self.worker_thread.wait(3000)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = TaskManagerWindow()
    window.show()
    sys.exit(app.exec())