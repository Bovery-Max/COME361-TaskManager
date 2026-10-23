import sys
import psutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QMessageBox, QHeaderView,
    QProgressBar, QGroupBox
)
from PyQt6.QtCore import QTimer, Qt


class AdvancedTaskManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Task Manager Lite - v1.1")
        self.resize(850, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; color: white; }
            QLabel { color: #e0e0e0; font-weight: bold; }
            QGroupBox { border: 1px solid #555; border-radius: 5px; margin-top: 10px; color: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
            QTableWidget { background-color: #1e1e1e; color: white; gridline-color: #444; border: 1px solid #555; }
            QHeaderView::section { background-color: #333; color: white; border: 1px solid #444; padding: 4px; }
            QLineEdit { background-color: #1e1e1e; color: white; border: 1px solid #555; padding: 5px; border-radius: 3px;}
            QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; color: white; }
            QProgressBar::chunk { background-color: #1f6aa5; }
            QPushButton { background-color: #c9302c; color: white; padding: 8px 15px; border-radius: 4px; font-weight: bold;}
            QPushButton:hover { background-color: #d9534f; }
        """)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # 1. Üst Panel: CPU ve RAM Barları
        sys_layout = QHBoxLayout()

        cpu_layout = QVBoxLayout()
        self.lbl_cpu = QLabel("Toplam CPU: %0")
        self.bar_cpu = QProgressBar()
        cpu_layout.addWidget(self.lbl_cpu)
        cpu_layout.addWidget(self.bar_cpu)

        ram_layout = QVBoxLayout()
        self.lbl_ram = QLabel("Toplam RAM: %0")
        self.bar_ram = QProgressBar()
        ram_layout.addWidget(self.lbl_ram)
        ram_layout.addWidget(self.bar_ram)

        sys_layout.addLayout(cpu_layout)
        sys_layout.addLayout(ram_layout)
        layout.addLayout(sys_layout)

        # 2. Arama ve Filtreleme
        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("İşlem adına veya PID'ye göre filtrele...")
        self.search_bar.textChanged.connect(self.filter_table)

        self.lbl_proc_count = QLabel("Listelenen İşlem: 0")
        search_layout.addWidget(self.search_bar)
        search_layout.addWidget(self.lbl_proc_count)
        layout.addLayout(search_layout)

        # 3. Proses Tablosu
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["PID", "İşlem Adı", "Durum", "CPU (%)", "Bellek (MB)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)  # YENİ: Tablo daha okunaklı olsun diye satır renkleri
        self.table.itemClicked.connect(self.show_details)  # YENİ: Tıklama olayı
        layout.addWidget(self.table)

        # 4. YENİ EKLENEN KISIM: Alt Panel (Detaylar ve Buton)
        bottom_layout = QHBoxLayout()

        self.details_box = QGroupBox("Seçili İşlem Detayları")
        details_inner = QVBoxLayout()
        self.lbl_details = QLabel("Detayları görmek için tablodan bir işleme tıklayın.")
        details_inner.addWidget(self.lbl_details)
        self.details_box.setLayout(details_inner)
        bottom_layout.addWidget(self.details_box, stretch=3)

        self.btn_kill = QPushButton("Görevi Sonlandır")
        self.btn_kill.clicked.connect(self.kill_process)
        bottom_layout.addWidget(self.btn_kill, stretch=1, alignment=Qt.AlignmentFlag.AlignBottom)

        layout.addLayout(bottom_layout)

        # Otomatik Güncelleme
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(2000)
        self.update_data()

    def update_data(self):
        # 1. Yenilemeden önce kullanıcının mevcut seçimini (PID'sini) kaydet
        selected_pid = None
        selected_row = self.table.currentRow()
        if selected_row >= 0:
            # PID'yi tutuyoruz çünkü yenilemeden sonra satır numarası değişebilir
            selected_pid = self.table.item(selected_row, 0).text()

        # CPU ve RAM Barlarını Güncelle
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        self.lbl_cpu.setText(f"Toplam CPU: %{cpu}")
        self.bar_cpu.setValue(int(cpu))
        self.lbl_ram.setText(f"Toplam RAM: %{ram}")
        self.bar_ram.setValue(int(ram))

        # Tabloyu Güncelle (Arama çubuğu boşsa)
        if not self.search_bar.text():
            procs = list(psutil.process_iter(['pid', 'name', 'status', 'cpu_percent', 'memory_info']))
            self.lbl_proc_count.setText(f"Listelenen İşlem: {min(len(procs), 40)}")

            # Tablo yenilemesi sırasında arayüzün titremesini önlemek için güncellemeyi dondur
            self.table.setUpdatesEnabled(False)
            self.table.setRowCount(0)

            new_selected_row = -1  # Yeniden seçilecek satırın indeksi

            for i, p in enumerate(procs[:40]):
                self.table.insertRow(i)
                pid_str = str(p.info['pid'])
                self.table.setItem(i, 0, QTableWidgetItem(pid_str))
                self.table.setItem(i, 1, QTableWidgetItem(p.info['name'] or ""))
                self.table.setItem(i, 2, QTableWidgetItem(str(p.info['status'])))
                self.table.setItem(i, 3, QTableWidgetItem(str(p.info['cpu_percent'])))
                mem_mb = round(p.info['memory_info'].rss / (1024 * 1024), 1)
                self.table.setItem(i, 4, QTableWidgetItem(str(mem_mb)))

                # 2. Eğer bu satır, yenilemeden önceki seçili PID ise indeksini kaydet
                if selected_pid and pid_str == selected_pid:
                    new_selected_row = i

            # 3. Tablo yenilendikten sonra, eski seçimi tekrar aktifleştir
            if new_selected_row >= 0:
                self.table.selectRow(new_selected_row)

            # Arayüz güncellemelerini tekrar aç
            self.table.setUpdatesEnabled(True)

    def filter_table(self, text):
        text = text.lower()
        visible_count = 0
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 1)
            pid_item = self.table.item(row, 0)
            match = text in name_item.text().lower() or text in pid_item.text()
            self.table.setRowHidden(row, not match)
            if match:
                visible_count += 1
        self.lbl_proc_count.setText(f"Listelenen İşlem: {visible_count}")

    def show_details(self, item):
        row = item.row()
        pid = int(self.table.item(row, 0).text())
        try:
            p = psutil.Process(pid)
            details = (f"PID: {p.pid} | Parent PID: {p.ppid()}\n"
                       f"Kullanıcı: {p.username()}\n"
                       f"Thread Sayısı: {p.num_threads()}")
            self.lbl_details.setText(details)
        except psutil.AccessDenied:
            self.lbl_details.setText("Detaylara erişim reddedildi (Admin yetkisi gerekli).")
        except psutil.NoSuchProcess:
            self.lbl_details.setText("İşlem zaten sonlanmış.")

    def kill_process(self):
        row = self.table.currentRow()
        if row == -1:
            QMessageBox.warning(self, "Uyarı", "Lütfen sonlandırmak için bir işlem seçin.")
            return

        pid = int(self.table.item(row, 0).text())
        name = self.table.item(row, 1).text()

        reply = QMessageBox.question(self, 'Onay', f"{name} (PID: {pid}) sonlandırılsın mı?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            try:
                p = psutil.Process(pid)
                p.terminate()
                self.update_data()
                QMessageBox.information(self, "Başarılı", f"{name} başarıyla sonlandırıldı.")

            except psutil.AccessDenied:
                # Yönetici yetkisi olmayan işlemlerde verilecek profesyonel uyarı
                QMessageBox.critical(self, "Yetki Hatası (Access Denied)",
                                     f"{name} işlemini sonlandırmak için Yönetici (Admin) yetkisi gerekiyor.\nBu bir sistem işlemi olabilir.")

            except psutil.NoSuchProcess:
                # Biz butona basana kadar işlem zaten kendiliğinden kapandıysa
                QMessageBox.warning(self, "Bulunamadı", f"{name} işlemi zaten sonlanmış.")

            except Exception as e:
                QMessageBox.critical(self, "Hata", f"İşlem sonlandırılamadı!\nDetay: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AdvancedTaskManager()
    window.show()
    sys.exit(app.exec())