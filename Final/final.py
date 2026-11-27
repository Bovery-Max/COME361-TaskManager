"""
Task Manager Lite - v1.4  (GUI katmanı / final.py)
COME 361 - İşletim Sistemleri

v1.4 değişiklikleri
- "Performans" sekmesi: çekirdek başına CPU grafikleri, disk ve ağ G/Ç hızı,
  toplam işlem/thread, açık kalma süresi, swap, bağlam değişimi ve kesme sayısı.
- Yeni sütunlar: Kullanıcı, Disk (KB/sn), Thread. Arama kullanıcı adında da çalışıyor.
- Sağ tık: işlem ağacını sonlandır (child'lar dahil), CPU affinity (çekirdek
  ataması), dosya konumunu aç.
- Detay paneli sekmeli: Genel (+ komut satırı, affinity), Kaynaklar (bellek
  ayrıntısı, bağlam değişimi, CPU süreleri), Açık Dosyalar, Ağ Bağlantıları.
- Listeyi CSV olarak dışa aktarma (Ctrl+S).

"""

import csv
import html
import math
import os
import sys
import shlex
import socket
import subprocess
import time
from collections import deque, defaultdict
from datetime import datetime

import psutil
from PyQt6.QtCore import (
    Qt, QTimer, QThread, QObject, pyqtSignal, pyqtSlot,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    QItemSelectionModel, QPointF,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QPainterPath, QLinearGradient, QFont,
    QPalette, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableView, QTreeWidget, QTreeWidgetItem, QPushButton, QLineEdit, QLabel,
    QMessageBox, QHeaderView, QGroupBox, QInputDialog, QTabWidget, QComboBox,
    QMenu, QAbstractItemView, QDialog, QDialogButtonBox, QCheckBox, QFileDialog,
    QScrollArea, QFrame, QSplitter, QSizePolicy,
)

# Ekip arkadaşımızın backend modülü
# process_ops.py aynı klasörde (repo: Final/) ya da bir üst klasörde olabilir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from process_ops import (list_processes, terminate_process, terminate_tree, start_process,
                         PROTECTED_PIDS)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_REFRESH_MS = 5000
REFRESH_OPTIONS = [("1 sn", 1000), ("2 sn", 2000), ("5 sn", 5000), ("10 sn", 10000)]
HISTORY_LEN = 60                      # grafikte tutulan ölçüm sayısı
MB = 1024 * 1024
GB = 1024 ** 3
CPU_COUNT = psutil.cpu_count() or 1
PHYSICAL_CORES = psutil.cpu_count(logical=False)
IS_WINDOWS = os.name == "nt"
HAS_AFFINITY = hasattr(psutil.Process, "cpu_affinity")   # macOS'ta yok
HAS_PROC_IO = hasattr(psutil.Process, "io_counters")     # macOS'ta yok

COLORS = {
    "bg": "#2b2b2b", "panel": "#1e1e1e", "border": "#555555", "grid": "#3a3a3a",
    "text": "#e0e0e0", "muted": "#9a9a9a", "accent": "#1f6aa5",
    "cpu": "#4aa3ff", "ram": "#b388ff", "disk": "#66bb6a", "net": "#ffa726",
}

# psutil durum kodu -> (Türkçe etiket, renk)
STATUS_INFO = {
    "running":    ("Çalışıyor", "#66bb6a"),
    "sleeping":   ("Uykuda", "#64b5f6"),
    "disk-sleep": ("G/Ç Bekliyor", "#ffca28"),
    "stopped":    ("Askıda", "#ffa726"),
    "zombie":     ("Zombi", "#ef5350"),
    "idle":       ("Boşta", "#9e9e9e"),
    "dead":       ("Ölü", "#ef5350"),
}

WINDOWS_PRIORITY_NAMES = {
    64: "Düşük", 16384: "Normalin altında", 32: "Normal",
    32768: "Normalin üstünde", 128: "Yüksek", 256: "Gerçek zamanlı",
}

# Tablo sütunları
COLUMNS = ["PID", "İşlem Adı", "Kullanıcı", "Durum", "CPU (%)", "Bellek (MB)",
           "Disk (KB/sn)", "Thread", "Parent PID"]
(C_PID, C_NAME, C_USER, C_STATUS, C_CPU, C_MEM,
 C_IO, C_THREADS, C_PPID) = range(len(COLUMNS))
# Sayısal sütun -> (satır anahtarı, gösterim biçimi)
NUMERIC_COLUMNS = {
    C_CPU: ("cpu", "{:.1f}"), C_MEM: ("mem_mb", "{:.1f}"), C_IO: ("io_rate", "{:.1f}"),
    C_THREADS: ("threads", "{}"), C_PPID: ("ppid", "{}"),
}
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

# Kaynaklar sekmesinde psutil alan adlarının Türkçe karşılıkları
MEMORY_FIELD_NAMES = {
    "rss": "RSS (fiziksel bellek)", "vms": "VMS (sanal bellek)",
    "wset": "Çalışma kümesi (working set)", "peak_wset": "Tepe çalışma kümesi",
    "private": "Özel bellek (private)", "pagefile": "Sayfa dosyası kullanımı",
    "num_page_faults": "Sayfa hatası (page fault)", "shared": "Paylaşılan bellek",
    "text": "Kod bölümü (text)", "data": "Veri + yığın (data)",
}


def status_label(status):
    return STATUS_INFO.get(status, (status or "?", COLORS["muted"]))


def short_user(user):
    """Windows 'DOMAIN\\kullanici' -> 'kullanici'."""
    return user.split("\\")[-1] if user else "—"


def fmt_rate(bytes_per_sec):
    if bytes_per_sec >= MB:
        return f"{bytes_per_sec / MB:.1f} MB/sn"
    return f"{bytes_per_sec / 1024:.1f} KB/sn"


def cpus_text(cpus):
    if cpus is None:
        return "Erişim yok"
    if len(cpus) == CPU_COUNT:
        return f"Tümü ({CPU_COUNT} çekirdek)"
    return ", ".join(map(str, cpus))


def grid_columns(n):
    """n adet öğeyi kareye yakın ızgarada dizmek için sütun sayısı."""
    return max(2, math.ceil(math.sqrt(n)))


def priority_text(value):
    if value is None:
        return "Erişim yok"
    if IS_WINDOWS:
        return WINDOWS_PRIORITY_NAMES.get(int(value), str(int(value)))
    return f"nice {int(value)}"


def priority_options():
    """Sağ tık menüsündeki öncelik seçenekleri (platforma göre)."""
    if IS_WINDOWS:
        return [
            ("Düşük", psutil.IDLE_PRIORITY_CLASS),
            ("Normalin altında", psutil.BELOW_NORMAL_PRIORITY_CLASS),
            ("Normal", psutil.NORMAL_PRIORITY_CLASS),
            ("Normalin üstünde", psutil.ABOVE_NORMAL_PRIORITY_CLASS),
            ("Yüksek", psutil.HIGH_PRIORITY_CLASS),
        ]
    return [
        ("Düşük (nice 19)", 19),
        ("Normalin altında (nice 10)", 10),
        ("Normal (nice 0)", 0),
        ("Yüksek (nice -10, root gerekir)", -10),
    ]


def signal_name(kind):
    """Arayüz mesajlarında OS seviyesindeki karşılığı göstermek için."""
    names = {
        "suspend": ("NtSuspendProcess", "SIGSTOP"),
        "resume": ("NtResumeProcess", "SIGCONT"),
        "kill": ("TerminateProcess", "SIGKILL"),
    }
    win, posix = names[kind]
    return win if IS_WINDOWS else posix


def _message(icon, parent, title, text, buttons=QMessageBox.StandardButton.Ok):
    """QMessageBox.critical/warning/... yerine: metin düz yazı olarak gösterilir,
    böylece işlem adlarındaki '<b>' gibi etiketler HTML olarak yorumlanmaz."""
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()
    return box.standardButton(box.clickedButton())


def _safe(fn, default=None):
    """Tek bir psutil alanını okur; yetki yoksa varsayılanı döner.
    NoSuchProcess (işlem öldü) bilerek yukarı fırlatılır."""
    try:
        return fn()
    except (psutil.AccessDenied, psutil.ZombieProcess):
        return default
    except psutil.NoSuchProcess:
        raise
    except (psutil.Error, OSError, RuntimeError):
        return default


def _system_counters():
    """Hız (x / sn) hesaplanacak sistem geneli sayaçlar."""
    disk = _safe(psutil.disk_io_counters)
    net = _safe(psutil.net_io_counters)
    stats = _safe(psutil.cpu_stats)
    return {
        "disk_read": disk.read_bytes if disk else 0,
        "disk_write": disk.write_bytes if disk else 0,
        "net_recv": net.bytes_recv if net else 0,
        "net_sent": net.bytes_sent if net else 0,
        "ctx": stats.ctx_switches if stats else 0,
        "intr": stats.interrupts if stats else 0,
    }


# ---------------------------------------------------------------------------
# Veri toplama (arka plan thread'inde çalışır, Qt widget'larına DOKUNMAZ)
# ---------------------------------------------------------------------------
def collect_snapshot(state):
    """Sistemin anlık görüntüsünü toplar.

    state: ölçümler arasında saklanan veriler, anahtar (pid, create_time):
      procs: psutil.Process  (proses başına cpu_percent() önceki çağrıya göre hesaplanır)
      io:    (okunan+yazılan bayt, zaman)  -> disk hızı
      users: kullanıcı adı (değişmez; her seferinde sorgulamamak için)
      sys:   (sistem sayaçları, zaman)     -> disk/ağ/bağlam değişimi hızı
    """
    t0 = time.perf_counter()
    now = time.monotonic()
    proc_cache, io_prev, users = state["procs"], state["io"], state["users"]
    rows, seen = [], set()

    for base in list_processes():
        pid = base.get("pid")
        if pid is None:
            continue
        ctime = base.get("create_time")
        key = (pid, ctime)
        row = {
            "pid": pid, "name": base.get("name") or "", "ppid": base.get("ppid"),
            "create_time": ctime, "status": "?", "cpu": None, "mem_mb": None,
            "user": None, "threads": None, "io_rate": None,
        }
        try:
            proc = proc_cache.get(key)
            if proc is None:
                proc = psutil.Process(pid)
                proc_cache[key] = proc
            with proc.oneshot():
                row["status"] = _safe(proc.status, "?")
                cpu = _safe(lambda: proc.cpu_percent(None))
                row["cpu"] = None if cpu is None else cpu / CPU_COUNT
                mem = _safe(proc.memory_info)
                row["mem_mb"] = None if mem is None else mem.rss / MB
                row["threads"] = _safe(proc.num_threads)
                if key not in users:
                    users[key] = _safe(proc.username)
                row["user"] = users[key]
                io = _safe(proc.io_counters) if HAS_PROC_IO else None
                if io is not None:
                    total = io.read_bytes + io.write_bytes
                    prev = io_prev.get(key)
                    row["io_rate"] = (0.0 if prev is None or now <= prev[1]
                                      else max(0.0, total - prev[0]) / (now - prev[1]) / 1024)
                    io_prev[key] = (total, now)
        except psutil.NoSuchProcess:
            continue
        seen.add(key)
        rows.append(row)

    # Ölen proseslerin önbellek kayıtlarını temizle
    for cache in (proc_cache, io_prev, users):
        for key in list(cache):
            if key not in seen:
                del cache[key]

    counters = _system_counters()
    prev = state["sys"]
    if prev is None or now <= prev[1]:
        rates = dict.fromkeys(counters, 0.0)
    else:
        dt = now - prev[1]
        rates = {k: max(0, v - prev[0][k]) / dt for k, v in counters.items()}
    state["sys"] = (counters, now)

    vm = psutil.virtual_memory()
    return {
        "rows": rows,
        "cpu_total": psutil.cpu_percent(None),
        "percpu": psutil.cpu_percent(None, percpu=True),
        "ram_percent": vm.percent,
        "ram_used_gb": (vm.total - vm.available) / GB,
        "ram_total_gb": vm.total / GB,
        "swap": _safe(psutil.swap_memory),
        "freq": _safe(psutil.cpu_freq),
        "boot_time": psutil.boot_time(),
        "threads_total": sum(r["threads"] or 0 for r in rows),
        "rates": rates,
        "elapsed_ms": (time.perf_counter() - t0) * 1000,
    }


class DataWorker(QObject):
    """QThread'e taşınan işçi nesne. fetch() arka plan thread'inde çalışır,
    sonucu sinyal ile ana thread'e gönderir (queued connection)."""
    data_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._state = {"procs": {}, "io": {}, "users": {}, "sys": None}
        psutil.cpu_percent(None)  # ilk ölçüm hep 0.0 döner; ısınma çağrıları
        psutil.cpu_percent(None, percpu=True)

    @pyqtSlot()
    def fetch(self):
        try:
            self.data_ready.emit(collect_snapshot(self._state))
        except Exception as e:  # arka plandaki hata uygulamayı düşürmesin
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# Canlı grafik bileşeni (QPainter ile, ek kütüphane gerektirmez)
# ---------------------------------------------------------------------------
class HistoryGraph(QWidget):
    """fmt=None: yüzde grafiği (0-100). fmt verilirse (örn. fmt_rate) eksen
    otomatik ölçeklenir. compact: çekirdek ızgarası için küçük sürüm."""

    def __init__(self, title, color, parent=None, fmt=None, compact=False):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.values = deque(maxlen=HISTORY_LEN)
        self.subtitle = ""
        self.fmt = fmt
        self.compact = compact
        self.setMinimumHeight(72 if compact else 125)

    def add_value(self, value, subtitle=""):
        value = max(0.0, float(value))
        self.values.append(value if self.fmt else min(100.0, value))
        self.subtitle = subtitle
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        # Kart arka planı
        p.setPen(QPen(QColor(COLORS["border"]), 1))
        p.setBrush(QColor(COLORS["panel"]))
        p.drawRoundedRect(rect, 6, 6)

        # Başlık, anlık değer, alt yazı
        current = self.values[-1] if self.values else 0.0
        value_text = self.fmt(current) if self.fmt else f"%{current:.1f}"
        top_value = (100.0 if self.fmt is None
                     else max(max(self.values, default=0.0) * 1.15, 1024.0))
        f_title = QFont(self.font())
        f_title.setBold(True)
        f_title.setPointSize(8 if self.compact else 10)
        p.setFont(f_title)
        p.setPen(QColor(COLORS["text"]))
        p.drawText(rect.adjusted(8 if self.compact else 12, 6 if self.compact else 8, -12, 0),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.title)

        f_value = QFont(f_title)
        f_value.setPointSize(9 if self.compact else 16)
        p.setFont(f_value)
        p.setPen(self.color)
        p.drawText(rect.adjusted(12, 4, -8 if self.compact else -12, 0),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, value_text)

        if not self.compact:
            f_sub = QFont(self.font())
            f_sub.setPointSize(8)
            p.setFont(f_sub)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(rect.adjusted(12, 28, -12, 0),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.subtitle)

        # Grafik alanı
        g = rect.adjusted(8, 26, -8, -6) if self.compact else rect.adjusted(12, 50, -12, -10)
        if g.height() < 10:
            return
        p.setPen(QPen(QColor(COLORS["grid"]), 1, Qt.PenStyle.DashLine))
        for frac in (0.25, 0.5, 0.75):
            y = g.bottom() - g.height() * frac
            p.drawLine(QPointF(g.left(), y), QPointF(g.right(), y))

        if len(self.values) < 2:
            return
        step = g.width() / (HISTORY_LEN - 1)
        start_x = g.right() - step * (len(self.values) - 1)   # en yeni değer sağda
        pts = [QPointF(start_x + i * step, g.bottom() - g.height() * v / top_value)
               for i, v in enumerate(self.values)]

        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)

        fill = QPainterPath(line)
        fill.lineTo(pts[-1].x(), g.bottom())
        fill.lineTo(pts[0].x(), g.bottom())
        fill.closeSubpath()
        grad = QLinearGradient(0, g.top(), 0, g.bottom())
        top_c, bottom_c = QColor(self.color), QColor(self.color)
        top_c.setAlpha(110)
        bottom_c.setAlpha(10)
        grad.setColorAt(0, top_c)
        grad.setColorAt(1, bottom_c)
        p.fillPath(fill, QBrush(grad))

        p.setPen(QPen(self.color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line)


# ---------------------------------------------------------------------------
# Tablo modeli (Model/View)
# ---------------------------------------------------------------------------
def _heat_color(ratio):
    """0..1 arası yoğunluk -> amber tonlu arka plan (Windows Görev Yöneticisi gibi)."""
    ratio = max(0.0, min(1.0, ratio))
    return QColor(255, 170, 0, int(25 + 130 * ratio))


class ProcessTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._pid_index = {}

    # --- Qt'nin istediği temel metotlar ---
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if c == C_PID:
                return str(r["pid"])
            if c == C_NAME:
                return r["name"]
            if c == C_USER:
                return short_user(r["user"])
            if c == C_STATUS:
                return status_label(r["status"])[0]
            if c in NUMERIC_COLUMNS:
                key, fmt = NUMERIC_COLUMNS[c]
                return "—" if r[key] is None else fmt.format(r[key])

        elif role == SORT_ROLE:  # sayısal sıralama (metin değil)
            if c == C_PID:
                return r["pid"]
            if c == C_NAME:
                return r["name"].lower()
            if c == C_USER:
                return short_user(r["user"]).lower()
            if c == C_STATUS:
                return status_label(r["status"])[0]
            if c in NUMERIC_COLUMNS:
                v = r[NUMERIC_COLUMNS[c][0]]
                return -1.0 if v is None else v

        elif role == Qt.ItemDataRole.ForegroundRole and c == C_STATUS:
            return QColor(status_label(r["status"])[1])

        elif role == Qt.ItemDataRole.BackgroundRole:
            if c == C_CPU and r["cpu"] is not None and r["cpu"] >= 1.0:
                return _heat_color(r["cpu"] / 50.0)
            if c == C_MEM and r["mem_mb"] is not None and r["mem_mb"] >= 100:
                return _heat_color(r["mem_mb"] / 1500.0)
            if c == C_IO and r["io_rate"] is not None and r["io_rate"] >= 100:
                return _heat_color(r["io_rate"] / 5000.0)

        elif role == Qt.ItemDataRole.TextAlignmentRole and (c == C_PID or c in NUMERIC_COLUMNS):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        elif role == Qt.ItemDataRole.ToolTipRole:
            if c == C_STATUS:
                return f"İşletim sistemi durumu: {r['status']}"
            if c == C_USER and r["user"]:
                return r["user"]
            if c in (C_CPU, C_MEM, C_IO, C_THREADS) and r[NUMERIC_COLUMNS[c][0]] is None:
                return "Erişim engellendi (yönetici yetkisi gerekebilir)"
        return None

    # --- Yardımcılar ---
    def row_at(self, row):
        return self._rows[row]

    def row_of_pid(self, pid):
        return self._pid_index.get(pid)

    def update_rows(self, new_rows):
        """Artımlı güncelleme: ölenleri sil, yaşayanları güncelle, yenileri ekle.
        Model sıfırlanmadığı için Qt seçim ve kaydırma konumunu kendisi korur."""
        key = lambda r: (r["pid"], r["create_time"])
        new_by_key = {key(r): r for r in new_rows}

        for i in range(len(self._rows) - 1, -1, -1):
            if key(self._rows[i]) not in new_by_key:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()

        existing = set()
        for i, r in enumerate(self._rows):
            k = key(r)
            self._rows[i] = new_by_key[k]
            existing.add(k)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._rows) - 1, len(COLUMNS) - 1))

        added = [r for k, r in new_by_key.items() if k not in existing]
        if added:
            start = len(self._rows)
            self.beginInsertRows(QModelIndex(), start, start + len(added) - 1)
            self._rows.extend(added)
            self.endInsertRows()

        self._pid_index = {r["pid"]: i for i, r in enumerate(self._rows)}


class ProcessFilterProxy(QSortFilterProxyModel):
    """İsim, PID veya kullanıcıya göre filtreleme + sayısal sıralama."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_filter_text(self, text):
        self._text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._text:
            return True
        r = self.sourceModel().row_at(source_row)
        return (self._text in r["name"].lower() or self._text in str(r["pid"])
                or self._text in short_user(r["user"]).lower())


# ---------------------------------------------------------------------------
# Tema
# ---------------------------------------------------------------------------
STYLE_SHEET = """
QMainWindow { background-color: #2b2b2b; }
QLabel { color: #e0e0e0; }
QLabel#caption { color: #9a9a9a; }
QLabel#value { color: #e0e0e0; font-weight: bold; }
QLabel#count { font-weight: bold; }
QGroupBox { border: 1px solid #555; border-radius: 6px; margin-top: 12px; padding-top: 8px;
            color: white; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTableView, QTreeView { background-color: #1e1e1e; alternate-background-color: #252525;
            color: #e0e0e0; border: 1px solid #555; gridline-color: #333;
            selection-background-color: #1f6aa5; selection-color: white; outline: 0; }
QHeaderView::section { background-color: #333; color: white; border: none;
            border-right: 1px solid #444; border-bottom: 1px solid #444;
            padding: 5px; font-weight: bold; }
QLineEdit { background-color: #1e1e1e; color: white; border: 1px solid #555;
            padding: 6px; border-radius: 4px; }
QLineEdit:focus { border: 1px solid #1f6aa5; }
QPushButton { background-color: #333; color: white; padding: 8px 15px;
            border: 1px solid #555; border-radius: 4px; font-weight: bold; }
QPushButton:hover { background-color: #444; }
QPushButton:disabled { color: #777; background-color: #2e2e2e; border-color: #3a3a3a; }
QPushButton#btn_kill { background-color: #c9302c; border: none; }
QPushButton#btn_kill:hover { background-color: #d9534f; }
QPushButton#btn_kill:disabled { background-color: #4a2524; color: #999; }
QPushButton#btn_suspend { background-color: #a86400; border: none; }
QPushButton#btn_suspend:hover { background-color: #c27400; }
QPushButton#btn_suspend:disabled { background-color: #45331a; color: #999; }
QPushButton#btn_small { padding: 3px 10px; font-weight: normal; }
QPushButton#btn_kill, QPushButton#btn_suspend { padding: 4px 14px; }
QLineEdit#value_line { background: transparent; border: none; padding: 0;
            color: #e0e0e0; font-weight: bold; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #333; color: #bbb; padding: 7px 16px; border: 1px solid #444;
            border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px;
            margin-right: 2px; }
QTabBar::tab:selected { background: #1e1e1e; color: white; border-color: #555; }
QTabBar::tab:hover:!selected { background: #3a3a3a; }
QStatusBar { background: #232323; color: #bbb; border-top: 1px solid #3a3a3a; }
QStatusBar QLabel { color: #bbb; }
QComboBox { background: #1e1e1e; color: white; border: 1px solid #555;
            border-radius: 3px; padding: 2px 8px; }
QComboBox QAbstractItemView { background: #1e1e1e; color: white;
            selection-background-color: #1f6aa5; }
QMenu { background: #2b2b2b; color: white; border: 1px solid #555; }
QMenu::item { padding: 6px 22px; }
QMenu::item:selected { background: #1f6aa5; }
QMenu::item:disabled { color: #777; }
QMenu::separator { height: 1px; background: #444; margin: 4px 8px; }
QToolTip { background: #1e1e1e; color: white; border: 1px solid #555; }
QScrollArea { background: transparent; border: none; }
QCheckBox { color: #e0e0e0; }
QSplitter::handle:vertical { background: #3a3a3a; height: 4px; margin: 2px 40px; border-radius: 2px; }
QSplitter::handle:vertical:hover { background: #1f6aa5; }
"""


def apply_dark_palette(app):
    """Fusion stili + koyu palet: Windows / Linux / macOS'ta aynı görünüm.
    (Windows'taki mor seçim rengi sistem vurgu renginden geliyordu.)"""
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#2b2b2b",
        QPalette.ColorRole.WindowText: "#e0e0e0",
        QPalette.ColorRole.Base: "#1e1e1e",
        QPalette.ColorRole.AlternateBase: "#252525",
        QPalette.ColorRole.Text: "#e0e0e0",
        QPalette.ColorRole.Button: "#333333",
        QPalette.ColorRole.ButtonText: "#ffffff",
        QPalette.ColorRole.Highlight: "#1f6aa5",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#1e1e1e",
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#888888",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#777777"))
    app.setPalette(pal)


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------
class TaskManagerWindow(QMainWindow):
    request_fetch = pyqtSignal()   # ana thread -> işçi thread

    DETAIL_FIELDS = [
        ("name", "İşlem"), ("pid", "PID"),
        ("parent", "Parent"), ("user", "Kullanıcı"),
        ("status", "Durum"), ("threads", "Thread Sayısı"),
        ("started", "Başlangıç"), ("priority", "Öncelik"),
        ("affinity", "CPU Affinity"), ("mem_pct", "RAM Payı"),
    ]

    SYS_FIELDS = [
        ("procs", "İşlem sayısı"), ("threads", "Toplam thread"), ("cores", "Çekirdek"),
        ("uptime", "Açık kalma süresi"), ("boot", "Açılış zamanı"), ("freq", "CPU frekansı"),
        ("swap", "Takas alanı (swap)"), ("ctx", "Bağlam değişimi"), ("intr", "Kesmeler (interrupt)"),
    ]
    TAB_TABLE, TAB_TREE, TAB_PERF = range(3)
    DTAB_GENERAL, DTAB_RESOURCES, DTAB_FILES, DTAB_CONNS = range(4)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Task Manager Lite - v1.4")
        self.setStyleSheet(STYLE_SHEET)

        self._rows = []
        self._rows_by_pid = {}
        self._selected_pid = None
        self._restoring = False        # programatik seçim değişikliklerini yok say
        self._fetching = False
        self._pending_fetch = False
        self._tree_items = {}          # pid -> QTreeWidgetItem
        self._tree_signature = None

        self._build_ui()
        # Ekranın ~%70'i kadar, ortada aç (küçük ekranlarda taşmasın)
        avail = self.screen().availableGeometry()
        self.resize(min(1050, int(avail.width() * 0.7)), min(760, int(avail.height() * 0.75)))
        geo = self.frameGeometry()
        geo.moveCenter(avail.center())
        self.move(geo.topLeft())
        self._build_worker()
        self._build_shortcuts()
        self._show_details_message("Detayları görmek için listeden bir işlem seçin.")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request_refresh)
        self.timer.start(DEFAULT_REFRESH_MS)
        self.request_refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        # 1. Canlı CPU / RAM grafikleri
        # (widget içinde: Performans sekmesinde gizlenebilsin)
        self.graphs_box = QWidget()
        graphs = QHBoxLayout(self.graphs_box)
        graphs.setContentsMargins(0, 0, 0, 0)
        self.cpu_graph = HistoryGraph("CPU Kullanımı", COLORS["cpu"])
        self.ram_graph = HistoryGraph("Bellek (RAM) Kullanımı", COLORS["ram"])
        graphs.addWidget(self.cpu_graph)
        graphs.addWidget(self.ram_graph)
        layout.addWidget(self.graphs_box)

        # 2. Arama + yeni görev
        top = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText(
            "İşlem adı, PID veya kullanıcıya göre filtrele...  (Ctrl+F)")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.on_search_changed)
        self.btn_start = QPushButton("Yeni Görev Çalıştır")
        self.btn_start.clicked.connect(self.run_new_process)
        self.btn_export = QPushButton("CSV Dışa Aktar")
        self.btn_export.setToolTip("Görünen listeyi CSV dosyasına kaydet (Ctrl+S)")
        self.btn_export.clicked.connect(self.export_csv)
        self.lbl_proc_count = QLabel("Listelenen İşlem: 0")
        self.lbl_proc_count.setObjectName("count")
        top.addWidget(self.search_bar, 1)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_export)
        top.addWidget(self.lbl_proc_count)
        layout.addLayout(top)

        # 3. Sekmeler: tablo + ağaç
        self.tabs = QTabWidget()

        self.model = ProcessTableModel(self)
        self.proxy = ProcessFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_MEM, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        hdr = self.table.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((C_PID, 70), (C_USER, 110), (C_STATUS, 105), (C_CPU, 75),
                           (C_MEM, 100), (C_IO, 100), (C_THREADS, 65), (C_PPID, 85)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().currentRowChanged.connect(self.on_table_current_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: self.show_context_menu(pos, self.table))
        self.tabs.addTab(self.table, "İşlemler")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["İşlem Adı", "PID", "Durum", "Bellek (MB)"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        thdr = self.tree.header()
        thdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        thdr.setStretchLastSection(False)
        for col, width in ((1, 80), (2, 120), (3, 110)):
            self.tree.setColumnWidth(col, width)
        self.tree.currentItemChanged.connect(self.on_tree_current_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda pos: self.show_context_menu(pos, self.tree))
        self.tabs.addTab(self.tree, "İşlem Ağacı (Parent → Child)")
        self.tabs.addTab(self._build_performance_tab(), "Performans")
        self.tabs.currentChanged.connect(self.on_tab_changed)
        # Performans sekmesinin büyük minimumu pencereyi uzatmasın; o sekmede
        # üst grafikler ve alt panel gizlendiği için zaten yer açılıyor.
        self.tabs.setMinimumHeight(150)

        # 4. Alt panel: detaylar (sekmeli), butonlar sekme çubuğunun sağ köşesinde
        self.detail_tabs = QTabWidget()
        self.bottom_panel = self.detail_tabs
        general = QWidget()
        grid = QGridLayout(general)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        # Satır başına 4 alan; "İşlem" iki alan genişliğinde (uzun isimler/mesajlar için)
        self.detail_values = {}
        slot = 0
        for key, title in self.DETAIL_FIELDS:
            r, pair = divmod(slot, 4)
            wide = key == "name"
            cap = QLabel(title + ":")
            cap.setObjectName("caption")
            val = QLabel("—")
            val.setObjectName("value")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # işlem adı vb. dışarıdan gelir: HTML olarak yorumlanmasın (renkli Durum hariç)
            val.setTextFormat(Qt.TextFormat.RichText if key == "status" else Qt.TextFormat.PlainText)
            # uzun değer pencereyi genişletmesin; tam metin tooltip'te
            val.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            grid.addWidget(cap, r, pair * 2)
            grid.addWidget(val, r, pair * 2 + 1, 1, 3 if wide else 1)
            self.detail_values[key] = val
            slot += 2 if wide else 1
        last_row = (slot + 3) // 4
        # Yol / Komut: tek satır, yatay kaydırılabilir, seçilip kopyalanabilir
        self.lbl_exe = self._make_value_line()
        self.lbl_cmdline = self._make_value_line()
        for i, (title, line) in enumerate((("Yol:", self.lbl_exe), ("Komut:", self.lbl_cmdline))):
            cap = QLabel(title)
            cap.setObjectName("caption")
            grid.addWidget(cap, last_row + i, 0)
            grid.addWidget(line, last_row + i, 1, 1, 7)
        for col in (1, 3, 5, 7):
            grid.setColumnStretch(col, 1)
        self.detail_tabs.addTab(general, "Genel")

        # Ağır bilgiler: yalnızca seçili işlem için ve sekme açıkken okunur
        self.res_view = self._make_list(["Alan", "Değer"])
        self.files_view = self._make_list(["Dosya Yolu", "FD"])
        self.conn_view = self._make_list(["Tür", "Yerel Adres", "Uzak Adres", "Durum"])
        self.detail_tabs.addTab(self.res_view, "Kaynaklar")
        self.detail_tabs.addTab(self.files_view, "Açık Dosyalar")
        self.detail_tabs.addTab(self.conn_view, "Ağ Bağlantıları")
        self.detail_tabs.currentChanged.connect(lambda _: self._update_details())

        corner = QWidget()
        buttons = QHBoxLayout(corner)
        buttons.setContentsMargins(0, 0, 0, 3)
        buttons.setSpacing(6)
        self.btn_suspend = QPushButton("Askıya Al")
        self.btn_suspend.setObjectName("btn_suspend")
        self.btn_suspend.clicked.connect(self.toggle_suspend)
        self.btn_kill = QPushButton("Görevi Sonlandır")
        self.btn_kill.setObjectName("btn_kill")
        self.btn_kill.clicked.connect(self.kill_selected)
        buttons.addWidget(self.btn_suspend)
        buttons.addWidget(self.btn_kill)
        self.detail_tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        # Liste ile detay paneli arasındaki çizgi sürüklenerek boyut ayarlanır
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.bottom_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([10000, 1])   # açılışta detay paneli minimumda, kalan yer listeye
        layout.addWidget(splitter, 1)

        # 5. Durum çubuğu
        sb = self.statusBar()
        self.lbl_updated = QLabel("Veri bekleniyor…")
        self.combo_interval = QComboBox()
        for text, ms in REFRESH_OPTIONS:
            self.combo_interval.addItem(text, ms)
        self.combo_interval.setCurrentIndex(
            [ms for _, ms in REFRESH_OPTIONS].index(DEFAULT_REFRESH_MS))
        self.combo_interval.currentIndexChanged.connect(self.on_interval_changed)
        self.btn_pause = QPushButton("Duraklat")
        self.btn_pause.setObjectName("btn_small")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self.on_pause_toggled)
        sb.addPermanentWidget(self.lbl_updated)
        sb.addPermanentWidget(QLabel("  Yenileme:"))
        sb.addPermanentWidget(self.combo_interval)
        sb.addPermanentWidget(self.btn_pause)

    @staticmethod
    def _make_value_line():
        line = QLineEdit("—")
        line.setObjectName("value_line")
        line.setReadOnly(True)
        line.setFrame(False)
        return line

    @staticmethod
    def _make_list(headers):
        view = QTreeWidget()
        view.setHeaderLabels(headers)
        view.setRootIsDecorated(False)
        view.setAlternatingRowColors(True)
        view.setUniformRowHeights(True)
        view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        view.header().setStretchLastSection(False)
        return view

    @staticmethod
    def _fill_list(view, rows, empty_text):
        bar = view.verticalScrollBar()
        pos = bar.value()                     # yenilemede kaydırma konumu korunsun
        view.clear()
        for values in rows:
            QTreeWidgetItem(view, [str(v) for v in values])
        if not rows:
            QTreeWidgetItem(view, [empty_text])
        bar.setValue(pos)

    def _build_performance_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        summary = QGroupBox("Sistem Özeti")
        grid = QGridLayout(summary)
        grid.setHorizontalSpacing(14)
        self.sys_values = {}
        for i, (key, title) in enumerate(self.SYS_FIELDS):
            r, col = divmod(i, 3)
            cap = QLabel(title + ":")
            cap.setObjectName("caption")
            val = QLabel("—")
            val.setObjectName("value")
            grid.addWidget(cap, r, col * 2)
            grid.addWidget(val, r, col * 2 + 1)
            grid.setColumnStretch(col * 2 + 1, 1)
            self.sys_values[key] = val
        layout.addWidget(summary)

        io_row = QHBoxLayout()
        self.disk_graph = HistoryGraph("Disk G/Ç (okuma + yazma)", COLORS["disk"], fmt=fmt_rate)
        self.net_graph = HistoryGraph("Ağ (alma + gönderme)", COLORS["net"], fmt=fmt_rate)
        io_row.addWidget(self.disk_graph)
        io_row.addWidget(self.net_graph)
        layout.addLayout(io_row)

        cores = QGroupBox(f"Çekirdek Başına CPU Kullanımı ({CPU_COUNT} mantıksal çekirdek)")
        core_grid = QGridLayout(cores)
        cols = grid_columns(CPU_COUNT)
        self.core_graphs = []
        for i in range(CPU_COUNT):
            g = HistoryGraph(f"CPU {i}", COLORS["cpu"], compact=True)
            core_grid.addWidget(g, i // cols, i % cols)
            self.core_graphs.append(g)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(cores)
        layout.addWidget(scroll, 1)
        return page

    def _update_performance(self, snap):
        rates = snap["rates"]
        self.disk_graph.add_value(
            rates["disk_read"] + rates["disk_write"],
            f"Okuma {fmt_rate(rates['disk_read'])} · Yazma {fmt_rate(rates['disk_write'])}")
        self.net_graph.add_value(
            rates["net_recv"] + rates["net_sent"],
            f"Alma {fmt_rate(rates['net_recv'])} · Gönderme {fmt_rate(rates['net_sent'])}")
        for graph, value in zip(self.core_graphs, snap["percpu"]):
            graph.add_value(value)

        up = int(time.time() - snap["boot_time"])
        days, rem = divmod(up, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        swap, freq = snap["swap"], snap["freq"]
        values = {
            "procs": str(len(snap["rows"])),
            "threads": f"{snap['threads_total']:,}".replace(",", "."),
            "cores": f"{PHYSICAL_CORES or '?'} fiziksel / {CPU_COUNT} mantıksal",
            "uptime": f"{days} gün {h:02}:{m:02}:{s:02}",
            "boot": datetime.fromtimestamp(snap["boot_time"]).strftime("%d.%m.%Y %H:%M"),
            "freq": f"{freq.current:.0f} MHz" if freq and freq.current else "—",
            "swap": (f"{swap.used / GB:.1f} / {swap.total / GB:.1f} GB (%{swap.percent:.0f})"
                     if swap else "—"),
            "ctx": f"{rates['ctx']:,.0f} / sn".replace(",", "."),
            "intr": f"{rates['intr']:,.0f} / sn".replace(",", "."),
        }
        for key, text in values.items():
            self.sys_values[key].setText(text)

    def _build_worker(self):
        self.worker_thread = QThread(self)
        self.worker = DataWorker()                 # parent'ı olmamalı (moveToThread için)
        self.worker.moveToThread(self.worker_thread)
        self.request_fetch.connect(self.worker.fetch)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.failed.connect(self.on_fetch_failed)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.start()

    def _build_shortcuts(self):
        for view in (self.table, self.tree):
            sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), view)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(self.kill_selected)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.request_refresh)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.export_csv)

    def _focus_search(self):
        self.search_bar.setFocus()
        self.search_bar.selectAll()

    # ------------------------------------------------------------ Veri akışı
    def request_refresh(self):
        """Zamanlayıcıdan veya bir işlemden sonra çağrılır. Önceki ölçüm bitmeden
        yenisini başlatmaz (istekler üst üste binmez)."""
        if self._fetching:
            self._pending_fetch = True
            return
        self._fetching = True
        self.request_fetch.emit()

    def on_fetch_failed(self, message):
        self._fetching = False
        self.statusBar().showMessage(f"Veri alınamadı: {message}", 5000)

    def on_data_ready(self, snap):
        self._fetching = False
        self._rows = snap["rows"]
        self._rows_by_pid = {r["pid"]: r for r in self._rows}

        self.cpu_graph.add_value(snap["cpu_total"], f"{CPU_COUNT} mantıksal çekirdek")
        self.ram_graph.add_value(
            snap["ram_percent"],
            f"{snap['ram_used_gb']:.1f} / {snap['ram_total_gb']:.1f} GB kullanımda")
        self._update_performance(snap)   # sekme gizliyken de: grafik geçmişi kesilmesin

        self._restoring = True
        try:
            self.model.update_rows(self._rows)
        finally:
            self._restoring = False
        self._sync_table_selection()
        if self.tabs.currentIndex() == self.TAB_TREE:
            self._sync_tree()

        self._update_count_label()
        self._update_details()
        self.lbl_updated.setText(
            f"Son güncelleme: {datetime.now():%H:%M:%S} · veri toplama {snap['elapsed_ms']:.0f} ms")

        if self._pending_fetch:
            self._pending_fetch = False
            self.request_refresh()

    def _update_count_label(self):
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        if self.search_bar.text():
            self.lbl_proc_count.setText(f"Listelenen İşlem: {shown} / {total}")
        else:
            self.lbl_proc_count.setText(f"Listelenen İşlem: {total}")

    # --------------------------------------------------------------- Seçim
    def _current_row(self):
        if self._selected_pid is None:
            return None
        return self._rows_by_pid.get(self._selected_pid)

    def _select_pid(self, pid):
        self._selected_pid = pid
        self._update_details()

    def on_table_current_changed(self, current, previous):
        if self._restoring or not current.isValid():
            return
        src = self.proxy.mapToSource(current)
        self._select_pid(self.model.row_at(src.row())["pid"])

    def on_tree_current_changed(self, current, previous):
        if self._restoring or current is None:
            return
        self._select_pid(current.data(0, Qt.ItemDataRole.UserRole))

    def _proxy_index_of_pid(self, pid):
        src = self.model.row_of_pid(pid) if pid is not None else None
        if src is None:
            return None
        idx = self.proxy.mapFromSource(self.model.index(src, 0))
        return idx if idx.isValid() else None

    def _sync_table_selection(self):
        """Tablodaki seçimi _selected_pid ile eşitler. Seçili işlem öldüyse
        Qt'nin seçimi sessizce komşu satıra kaydırmasını engeller."""
        sm = self.table.selectionModel()
        target = self._proxy_index_of_pid(self._selected_pid)
        self._restoring = True
        try:
            if target is None:
                if sm.hasSelection() or self.table.currentIndex().isValid():
                    sm.clear()
            elif (self.table.currentIndex().row() != target.row()
                  or not sm.isRowSelected(target.row(), QModelIndex())):
                sm.setCurrentIndex(
                    target,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows)
        finally:
            self._restoring = False

    def _focus_pid(self, pid):
        """Verilen PID'yi aktif görünümde seçer ve görünür yapar."""
        if pid not in self._rows_by_pid:
            return
        if self.tabs.currentIndex() == self.TAB_TREE:
            item = self._tree_items.get(pid)
            if item is not None:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
            return
        if self._proxy_index_of_pid(pid) is None and self.search_bar.text():
            self.search_bar.clear()             # filtre gizliyorsa temizle
        idx = self._proxy_index_of_pid(pid)
        if idx is not None:
            self.table.setCurrentIndex(idx)
            self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    # -------------------------------------------------------- İşlem ağacı
    def _parent_map(self):
        """pid -> geçerli parent pid. PID yeniden kullanımına karşı koruma:
        parent, çocuğundan SONRA oluşturulmuşsa gerçek parent olamaz."""
        result = {}
        for r in self._rows:
            par = self._rows_by_pid.get(r["ppid"])
            ok = par is not None and par["pid"] != r["pid"]
            if ok and par["create_time"] is not None and r["create_time"] is not None:
                ok = par["create_time"] <= r["create_time"]
            result[r["pid"]] = par["pid"] if ok else None
        return result

    def _fill_tree_item(self, item, r):
        label, color = status_label(r["status"])
        item.setText(0, r["name"] or "?")
        item.setText(1, str(r["pid"]))
        item.setText(2, label)
        item.setText(3, "—" if r["mem_mb"] is None else f"{r['mem_mb']:.1f}")
        item.setData(0, Qt.ItemDataRole.UserRole, r["pid"])
        item.setForeground(2, QBrush(QColor(color)))
        item.setToolTip(2, f"İşletim sistemi durumu: {r['status']}")
        for col in (1, 3):
            item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _sync_tree(self):
        parent_of = self._parent_map()
        signature = frozenset((r["pid"], r["create_time"], parent_of[r["pid"]]) for r in self._rows)

        # Yapı değişmediyse ağacı yeniden kurma, sadece değerleri güncelle
        if signature == self._tree_signature:
            for r in self._rows:
                item = self._tree_items.get(r["pid"])
                if item is not None:
                    self._fill_tree_item(item, r)
            self._sync_tree_selection()
            return

        first_build = self._tree_signature is None
        self._tree_signature = signature
        expanded = {pid for pid, it in self._tree_items.items() if it.isExpanded()}
        vbar = self.tree.verticalScrollBar()
        scroll = vbar.value()

        children, roots = defaultdict(list), []
        for r in sorted(self._rows, key=lambda x: x["name"].lower()):
            par = parent_of[r["pid"]]
            (children[par] if par is not None else roots).append(r)

        self._restoring = True
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self._tree_items = {}
            stack = [(r, None) for r in reversed(roots)]
            while stack:
                r, parent_item = stack.pop()
                if r["pid"] in self._tree_items:      # döngü koruması
                    continue
                item = QTreeWidgetItem()
                self._fill_tree_item(item, r)
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                self._tree_items[r["pid"]] = item
                kids = children.get(r["pid"], [])
                if kids:
                    item.setToolTip(0, f"{len(kids)} doğrudan alt işlem (child)")
                for ch in reversed(kids):
                    stack.append((ch, item))

            for pid, item in self._tree_items.items():
                if pid in expanded or (first_build and item.parent() is None):
                    item.setExpanded(True)

            self._sync_tree_selection()
            self.tree.doItemsLayout()
            vbar.setValue(scroll)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._restoring = False

    def _sync_tree_selection(self):
        item = self._tree_items.get(self._selected_pid)
        was = self._restoring
        self._restoring = True
        try:
            if item is None:
                self.tree.clearSelection()
                self.tree.setCurrentItem(None)
            elif self.tree.currentItem() is not item:
                self.tree.setCurrentItem(item)
        finally:
            self._restoring = was

    def on_tab_changed(self, index):
        # Performans sekmesinde üst grafikler (tekrar) ve işlem detayı (ilgisiz) gizlenir
        perf = index == self.TAB_PERF
        self.graphs_box.setVisible(not perf)
        self.bottom_panel.setVisible(not perf)
        if index == self.TAB_TREE:
            self._sync_tree()
            item = self._tree_items.get(self._selected_pid)
            if item is not None:
                self.tree.scrollToItem(item)
        else:
            self._sync_table_selection()

    def on_search_changed(self, text):
        self.proxy.set_filter_text(text)
        if text and self.tabs.currentIndex() != self.TAB_TABLE:
            self.tabs.setCurrentIndex(self.TAB_TABLE)
        self._update_count_label()

    # -------------------------------------------------------------- Detaylar
    def _show_details_message(self, message):
        for key, label in self.detail_values.items():
            label.setText(message if key == "name" else "—")
            label.setToolTip("")
        self.lbl_exe.setText("—")
        self.lbl_cmdline.setText("—")
        for view in (self.res_view, self.files_view, self.conn_view):
            view.clear()
        self.btn_kill.setEnabled(False)
        self.btn_suspend.setEnabled(False)

    def _update_details(self):
        pid = self._selected_pid
        if pid is None:
            return
        row = self._rows_by_pid.get(pid)
        try:
            if row is None:
                raise psutil.NoSuchProcess(pid)
            p = psutil.Process(pid)
            with p.oneshot():
                user = _safe(p.username, "Erişim yok")
                threads = _safe(p.num_threads)
                prio = _safe(p.nice)
                exe = _safe(p.exe, "")
                cmdline = _safe(p.cmdline)
                affinity = _safe(p.cpu_affinity) if HAS_AFFINITY else None
                mem_pct = _safe(p.memory_percent)
            self._update_detail_tab(p)
        except psutil.NoSuchProcess:
            self._selected_pid = None
            self._show_details_message(f"PID {pid} artık çalışmıyor (sonlanmış).")
            return

        parent = self._rows_by_pid.get(row["ppid"])
        parent_text = "—" if row["ppid"] is None else (
            f"{row['ppid']} ({parent['name']})" if parent else f"{row['ppid']} (çalışmıyor)")
        label, color = status_label(row["status"])
        ct = row["create_time"]

        values = {
            "name": row["name"],
            "pid": str(pid),
            "parent": parent_text,
            "user": user,
            "status": f"<span style='color:{color}'>{label}</span> ({row['status']})",
            "threads": "Erişim yok" if threads is None else str(threads),
            "started": datetime.fromtimestamp(ct).strftime("%d.%m.%Y %H:%M:%S") if ct else "—",
            "priority": priority_text(prio),
            "affinity": cpus_text(affinity) if HAS_AFFINITY else "Desteklenmiyor",
            "mem_pct": "Erişim yok" if mem_pct is None else f"%{mem_pct:.2f}",
        }
        for key, text in values.items():
            self.detail_values[key].setText(text)
            # tooltip her zaman zengin metin: dış veriyi kaçışla göster
            self.detail_values[key].setToolTip(
                text if key == "status" else f"<p>{html.escape(text)}</p>")
        join = subprocess.list2cmdline if IS_WINDOWS else shlex.join
        for line, text in ((self.lbl_exe, exe or "Erişim yok / yok"),
                           (self.lbl_cmdline, join(cmdline) if cmdline else "Erişim yok / yok")):
            if line.text() != text:          # her yenilemede kullanıcının kaydırmasını bozma
                line.setText(text)
                line.setCursorPosition(0)    # uzun komutu baştan göster

        self.btn_kill.setEnabled(True)
        self.btn_suspend.setEnabled(pid not in PROTECTED_PIDS)
        self.btn_suspend.setText("Devam Ettir" if row["status"] == "stopped" else "Askıya Al")

    def _update_detail_tab(self, p):
        """Açık olan detay sekmesini doldurur (Genel hariç). NoSuchProcess yukarı fırlar."""
        tab = self.detail_tabs.currentIndex()
        denied = "Erişim engellendi (yönetici yetkisi gerekebilir)"

        if tab == self.DTAB_RESOURCES:
            rows = []
            mem = _safe(p.memory_info)
            if mem is not None:
                for field, value in mem._asdict().items():
                    shown = (f"{value:,}".replace(",", ".") if "fault" in field
                             else f"{value / MB:.1f} MB")
                    rows.append((MEMORY_FIELD_NAMES.get(field, field), shown))
            ctx = _safe(p.num_ctx_switches)
            if ctx is not None:
                rows.append(("Bağlam değişimi (gönüllü)", f"{ctx.voluntary:,}".replace(",", ".")))
                rows.append(("Bağlam değişimi (zorunlu)", f"{ctx.involuntary:,}".replace(",", ".")))
            times = _safe(p.cpu_times)
            if times is not None:
                rows.append(("CPU süresi (kullanıcı modu)", f"{times.user:.2f} sn"))
                rows.append(("CPU süresi (çekirdek modu)", f"{times.system:.2f} sn"))
            self._fill_list(self.res_view, rows, denied)

        elif tab == self.DTAB_FILES:
            files = _safe(p.open_files)
            self._fill_list(self.files_view,
                            [(f.path, f.fd if f.fd >= 0 else "—") for f in files or []],
                            denied if files is None else "Açık dosya yok")

        elif tab == self.DTAB_CONNS:
            fn = getattr(p, "net_connections", None) or p.connections   # psutil < 6
            conns = _safe(lambda: fn(kind="inet"))
            proto = {socket.SOCK_STREAM: "TCP", socket.SOCK_DGRAM: "UDP"}
            addr = lambda a: f"{a.ip}:{a.port}" if a else "—"
            rows = [(proto.get(c.type, str(c.type)), addr(c.laddr), addr(c.raddr),
                     "—" if c.status == psutil.CONN_NONE else c.status)
                    for c in conns or []]
            self._fill_list(self.conn_view, rows,
                            denied if conns is None else "Ağ bağlantısı yok")

    # ------------------------------------------------------- İşlem kontrolü
    def _verified_process(self, row):
        """PID yeniden kullanılmışsa yanlış işleme dokunmamak için create_time kontrolü."""
        p = psutil.Process(row["pid"])
        ct = row["create_time"]
        if ct is not None and abs(p.create_time() - ct) > 0.01:
            raise psutil.NoSuchProcess(row["pid"], row["name"], "PID başka bir işleme ait")
        return p

    def _require_row(self):
        row = self._current_row()
        if row is None:
            _message(QMessageBox.Icon.Warning, self, "Uyarı", "Lütfen önce listeden bir işlem seçin.")
        return row

    def run_new_process(self):
        hint = "notepad, mspaint, calc" if IS_WINDOWS else "gedit, xterm, sleep 60"
        cmd, ok = QInputDialog.getText(
            self, "Yeni Görev Çalıştır", f"Çalıştırılacak komut (örn: {hint}):")
        if not ok or not cmd.strip():
            return
        try:
            parts = [s.strip('"') for s in shlex.split(cmd.strip(), posix=not IS_WINDOWS)]
            new_pid = start_process(parts[0], parts[1:])   # process_ops.start_process(program, args)
        except Exception as e:
            _message(QMessageBox.Icon.Critical, self, "Hata", f"İşlem başlatılamadı: {e}")
            return
        self.statusBar().showMessage(f"'{cmd.strip()}' başlatıldı (PID: {new_pid})", 5000)
        self._selected_pid = new_pid
        self.request_refresh()

    def toggle_suspend(self):
        row = self._require_row()
        if row is None:
            return
        if row["pid"] in PROTECTED_PIDS:
            _message(QMessageBox.Icon.Warning, self, "Engellendi", "Bu sistem işlemi askıya alınamaz.")
            return
        resume = row["status"] == "stopped"
        ok, _ = self._try_process(row, lambda p: p.resume() if resume else p.suspend(),
                                  "Devam ettirmek" if resume else "Askıya almak")
        if not ok:
            return
        kind = "resume" if resume else "suspend"
        verb = "devam ettirildi" if resume else "askıya alındı"
        self.statusBar().showMessage(
            f"{row['name']} (PID {row['pid']}) {verb} — {signal_name(kind)}", 6000)
        self.request_refresh()

    def set_priority(self, row, value, label):
        if row["pid"] in PROTECTED_PIDS:
            _message(QMessageBox.Icon.Warning, self, "Engellendi", "Bu sistem işleminin önceliği değiştirilemez.")
            return
        ok, _ = self._try_process(row, lambda p: p.nice(value), "Bu önceliği ayarlamak")
        if not ok:
            return
        self.statusBar().showMessage(f"{row['name']} önceliği: {label}", 5000)
        self._update_details()

    def kill_selected(self):
        row = self._require_row()
        if row is None:
            return
        reply = _message(QMessageBox.Icon.Question, 
            self, "Onay", f"{row['name']} (PID: {row['pid']}) sonlandırılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            # Backend: korumalı PID kontrolü + create_time ile PID çakışması doğrulaması
            terminate_process(row["pid"], expected_create_time=row["create_time"])
        except ValueError as ve:
            _message(QMessageBox.Icon.Warning, self, "Engellendi", str(ve))
            return
        except psutil.AccessDenied:
            _message(QMessageBox.Icon.Critical, self, "Yetki Hatası",
                                 f"{row['name']} işlemini sonlandırmak için Yönetici (Admin) yetkisi gerekiyor.")
            return
        except psutil.NoSuchProcess:
            _message(QMessageBox.Icon.Warning, self, "Bulunamadı", f"{row['name']} işlemi zaten sonlanmış.")
            return
        except Exception as e:
            _message(QMessageBox.Icon.Critical, self, "Hata", f"İşlem sonlandırılamadı!\nDetay: {e}")
            return

        self.statusBar().showMessage(f"{row['name']} için sonlandırma sinyali gönderildi, doğrulanıyor…")
        # terminate() sadece sinyal gönderir; sonucu UI'ı bloklamadan biraz sonra kontrol et
        QTimer.singleShot(1500, lambda: self._verify_termination(row))

    def _verify_termination(self, row):
        name = row["name"]
        try:
            p = self._verified_process(row)
            status = p.status()
        except psutil.NoSuchProcess:
            status = None
        except psutil.AccessDenied:
            status = "?"

        if status is None:
            self.statusBar().showMessage(f"{name} başarıyla sonlandırıldı.", 5000)
            _message(QMessageBox.Icon.Information, self, "Başarılı", f"{name} başarıyla sonlandırıldı.")
        elif status == "zombie":
            _message(QMessageBox.Icon.Information, 
                self, "Zombi İşlem",
                f"{name} sonlandı ancak şu an ZOMBİ durumunda.\n"
                "Parent işlem henüz wait() çağırıp çıkış kodunu toplamadı.")
        else:
            reply = _message(QMessageBox.Icon.Question, 
                self, "İşlem hâlâ çalışıyor",
                f"{name} sonlandırma sinyaline (SIGTERM) yanıt vermedi.\n"
                f"Zorla kapatılsın mı? ({signal_name('kill')})",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    self._verified_process(row).kill()
                    self.statusBar().showMessage(f"{name} zorla sonlandırıldı.", 5000)
                except psutil.NoSuchProcess:
                    self.statusBar().showMessage(f"{name} zaten sonlanmış.", 5000)
                except psutil.AccessDenied:
                    _message(QMessageBox.Icon.Critical, self, "Yetki Hatası",
                                         f"{name} için yönetici (admin) yetkisi gerekiyor.")
        self.request_refresh()

    def _try_process(self, row, fn, what):
        """fn(psutil.Process) çalıştırır, tipik hataları kullanıcıya gösterir.
        Döner: (başarılı mı, fn'in sonucu)."""
        try:
            return True, fn(self._verified_process(row))
        except psutil.AccessDenied:
            _message(QMessageBox.Icon.Critical, self, "Yetki Hatası",
                                 f"{what} için yönetici (admin/root) yetkisi gerekiyor.")
        except psutil.NoSuchProcess:
            _message(QMessageBox.Icon.Warning, self, "Bulunamadı", f"{row['name']} işlemi zaten sonlanmış.")
        except Exception as e:
            _message(QMessageBox.Icon.Critical, self, "Hata", f"{what} başarısız oldu.\nDetay: {e}")
        return False, None

    def kill_tree(self, row):
        ok, kids = self._try_process(row, lambda p: p.children(recursive=True),
                                     "Alt işlemleri okumak")
        if not ok:
            return
        reply = _message(QMessageBox.Icon.Question, 
            self, "Onay",
            f"{row['name']} (PID: {row['pid']}) ve {len(kids)} alt işlemi (child) "
            "birlikte sonlandırılsın mı?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            sent, denied = terminate_tree(row["pid"], expected_create_time=row["create_time"])
        except ValueError as ve:
            _message(QMessageBox.Icon.Warning, self, "Engellendi", str(ve))
            return
        except psutil.AccessDenied:
            _message(QMessageBox.Icon.Critical, self, "Yetki Hatası",
                                 f"{row['name']} için yönetici (admin) yetkisi gerekiyor.")
            return
        except psutil.NoSuchProcess:
            _message(QMessageBox.Icon.Warning, self, "Bulunamadı", f"{row['name']} işlemi zaten sonlanmış.")
            return
        self.statusBar().showMessage(
            f"{len(sent)} işleme sonlandırma sinyali gönderildi, doğrulanıyor…")
        QTimer.singleShot(1500, lambda: self._verify_tree(row, sent, denied))

    def _verify_tree(self, row, sent, denied):
        """is_running() create_time'ı da kontrol eder: PID yeniden kullanıldıysa yanılmaz."""
        alive = [p for p in sent if p.is_running() and _safe(p.status) != "zombie"]
        note = f"\n{len(denied)} alt işlem için yetki yetmedi, atlandı." if denied else ""
        if not alive:
            _message(QMessageBox.Icon.Information, 
                self, "Başarılı",
                f"{row['name']} ağacındaki {len(sent)} işlem sonlandırıldı.{note}")
        else:
            reply = _message(QMessageBox.Icon.Question, 
                self, "Bazı işlemler hâlâ çalışıyor",
                f"{len(alive)} işlem sonlandırma sinyaline yanıt vermedi.{note}\n"
                f"Zorla kapatılsınlar mı? ({signal_name('kill')})",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                for p in alive:
                    try:
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                self.statusBar().showMessage(f"{len(alive)} işlem zorla sonlandırıldı.", 5000)
        self.request_refresh()

    def set_affinity(self, row):
        if row["pid"] in PROTECTED_PIDS:
            _message(QMessageBox.Icon.Warning, self, "Engellendi", "Bu sistem işleminin çekirdek ataması değiştirilemez.")
            return
        ok, current = self._try_process(row, lambda p: p.cpu_affinity(), "Çekirdek atamasını okumak")
        if not ok:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"CPU Affinity — {row['name']} (PID {row['pid']})")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("İşletim sistemi bu işlemi yalnızca seçili çekirdeklerde zamanlayacak:"))
        grid = QGridLayout()
        cols = grid_columns(CPU_COUNT)
        boxes = []
        for i in range(CPU_COUNT):
            cb = QCheckBox(f"CPU {i}")
            cb.setChecked(i in current)
            grid.addWidget(cb, i // cols, i % cols)
            boxes.append(cb)
        lay.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        select_all = buttons.addButton("Tümünü Seç", QDialogButtonBox.ButtonRole.ActionRole)
        select_all.clicked.connect(lambda: [b.setChecked(True) for b in boxes])
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen = [i for i, b in enumerate(boxes) if b.isChecked()]
        if not chosen:
            _message(QMessageBox.Icon.Warning, self, "Uyarı", "En az bir çekirdek seçilmelidir.")
            return
        ok, _ = self._try_process(row, lambda p: p.cpu_affinity(chosen), "Çekirdek atamasını değiştirmek")
        if ok:
            self.statusBar().showMessage(
                f"{row['name']} artık şu çekirdeklerde çalışacak: {cpus_text(chosen)}", 6000)
            self._update_details()

    def open_location(self, row):
        ok, exe = self._try_process(row, lambda p: p.exe(), "Dosya yolunu okumak")
        if not ok:
            return
        if not exe or not os.path.exists(exe):
            _message(QMessageBox.Icon.Information, self, "Bilgi", "Bu işlemin dosya yolu bilinmiyor.")
            return
        try:
            if IS_WINDOWS:
                subprocess.Popen(f'explorer /select,"{exe}"')   # dosyayı seçili açar
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", exe])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(exe)])
        except OSError as e:
            _message(QMessageBox.Icon.Critical, self, "Hata", f"Dosya konumu açılamadı.\nDetay: {e}")

    def export_csv(self):
        default = f"islemler_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV olarak dışa aktar", default, "CSV (*.csv)")
        if not path:
            return
        count = self.proxy.rowCount()   # filtre ve sıralama ekrandaki gibi
        try:
            # utf-8-sig + ';' : Türkçe Excel'de karakterler ve sütunlar doğru açılır
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(COLUMNS)
                for r in range(count):
                    writer.writerow([self.proxy.index(r, c).data() for c in range(len(COLUMNS))])
        except OSError as e:
            _message(QMessageBox.Icon.Critical, self, "Hata", f"Dosya kaydedilemedi.\nDetay: {e}")
            return
        self.statusBar().showMessage(f"{count} işlem dışa aktarıldı: {path}", 6000)

    # ---------------------------------------------------------- Sağ tık menüsü
    def show_context_menu(self, pos, view):
        if view is self.table:
            idx = self.table.indexAt(pos)
            if not idx.isValid():
                return
            self.table.setCurrentIndex(idx)
        else:
            item = self.tree.itemAt(pos)
            if item is None:
                return
            self.tree.setCurrentItem(item)

        row = self._current_row()
        if row is None:
            return
        protected = row["pid"] in PROTECTED_PIDS

        menu = QMenu(self)
        act_copy = menu.addAction(f"PID'yi kopyala ({row['pid']})")
        act_parent = menu.addAction("Parent işleme git")
        act_parent.setEnabled(row["ppid"] in self._rows_by_pid)
        act_location = menu.addAction("Dosya konumunu aç")
        menu.addSeparator()
        act_suspend = menu.addAction("Devam Ettir" if row["status"] == "stopped" else "Askıya Al")
        act_suspend.setEnabled(not protected)
        prio_menu = menu.addMenu("Öncelik (CPU zamanlama)")
        prio_menu.setEnabled(not protected)
        prio_actions = {}
        for label, value in priority_options():
            prio_actions[prio_menu.addAction(label)] = (value, label)
        act_affinity = menu.addAction("Çekirdek ataması (CPU affinity)…")
        act_affinity.setEnabled(HAS_AFFINITY and not protected)
        menu.addSeparator()
        act_kill = menu.addAction("Görevi Sonlandır")
        act_kill_tree = menu.addAction("İşlem ağacını sonlandır (child'lar dahil)")
        act_kill_tree.setEnabled(not protected)

        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_copy:
            QApplication.clipboard().setText(str(row["pid"]))
            self.statusBar().showMessage("PID panoya kopyalandı.", 3000)
        elif chosen == act_parent:
            self._focus_pid(row["ppid"])
        elif chosen == act_suspend:
            self.toggle_suspend()
        elif chosen == act_kill:
            self.kill_selected()
        elif chosen == act_kill_tree:
            self.kill_tree(row)
        elif chosen == act_affinity:
            self.set_affinity(row)
        elif chosen == act_location:
            self.open_location(row)
        elif chosen in prio_actions:
            value, label = prio_actions[chosen]
            self.set_priority(row, value, label)

    # ------------------------------------------------------- Durum çubuğu
    def on_interval_changed(self, index):
        self.timer.setInterval(self.combo_interval.itemData(index))

    def on_pause_toggled(self, paused):
        if paused:
            self.timer.stop()
            self.btn_pause.setText("Devam")
            self.statusBar().showMessage("Otomatik yenileme duraklatıldı (F5 ile elle yenileyebilirsiniz).")
        else:
            self.timer.start()
            self.btn_pause.setText("Duraklat")
            self.statusBar().clearMessage()
            self.request_refresh()

    def closeEvent(self, event):
        self.timer.stop()
        self.worker_thread.quit()
        self.worker_thread.wait()   # süren ölçüm bitsin (Windows'ta ~3 sn sürebilir)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_palette(app)
    window = TaskManagerWindow()
    window.show()
    sys.exit(app.exec())