import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

# Yagmur'un kullanacağı kütüphane. Eğer yüklü değilse rastgele simüle eder.
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    import random
    HAS_PSUTIL = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class TaskManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Task Manager Lite")
        self.geometry("880x640")
        self.minsize(720, 500)

        # Sahte Process Verisi
        self.all_processes = [
            {"pid": 1042, "name": "chrome.exe", "cpu": 14.2, "ram": 520.4, "status": "Running"},
            {"pid": 2311, "name": "code.exe (VS Code)", "cpu": 3.8, "ram": 340.1, "status": "Running"},
            {"pid": 5890, "name": "spotify.exe", "cpu": 0.9, "ram": 185.0, "status": "Sleeping"},
            {"pid": 8412, "name": "discord.exe", "cpu": 2.1, "ram": 210.5, "status": "Running"},
            {"pid": 412,  "name": "system", "cpu": 0.4, "ram": 64.0, "status": "Running"},
            {"pid": 9304, "name": "steam.exe", "cpu": 0.1, "ram": 110.2, "status": "Sleeping"},
        ]

        self._setup_ui()
        self.populate_table(self.all_processes)

        # CPU & RAM barlarını düzenli güncelleyen döngüyü başlat
        self.update_system_metrics()

    def _setup_ui(self):
        # ==========================================
        # 1. EN ÜST: Sistem Performans Göstergeleri (CPU / RAM)
        # ==========================================
        self.metrics_frame = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8)
        self.metrics_frame.pack(fill="x", padx=15, pady=(15, 5))

        # --- CPU Kartı ---
        self.cpu_frame = ctk.CTkFrame(self.metrics_frame, fg_color="transparent")
        self.cpu_frame.pack(side="left", fill="x", expand=True, padx=15, pady=10)

        self.cpu_label = ctk.CTkLabel(
            self.cpu_frame, 
            text="Toplam CPU: %0.0", 
            font=("Segoe UI", 12, "bold")
        )
        self.cpu_label.pack(anchor="w")

        self.cpu_bar = ctk.CTkProgressBar(self.cpu_frame, height=12)
        self.cpu_bar.set(0.0)  # Değer aralığı 0.0 - 1.0 arasındadır
        self.cpu_bar.pack(fill="x", pady=(5, 0))

        # --- RAM Kartı ---
        self.ram_frame = ctk.CTkFrame(self.metrics_frame, fg_color="transparent")
        self.ram_frame.pack(side="right", fill="x", expand=True, padx=15, pady=10)

        self.ram_label = ctk.CTkLabel(
            self.ram_frame, 
            text="Toplam RAM: %0.0", 
            font=("Segoe UI", 12, "bold")
        )
        self.ram_label.pack(anchor="w")

        self.ram_bar = ctk.CTkProgressBar(self.ram_frame, height=12)
        self.ram_bar.set(0.0)
        self.ram_bar.pack(fill="x", pady=(5, 0))

        # ==========================================
        # 2. ARAMA VE BİLGİ ALANI
        # ==========================================
        self.search_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.search_frame.pack(fill="x", padx=15, pady=(10, 5))

        self.search_entry = ctk.CTkEntry(
            self.search_frame,
            placeholder_text="İşlem adına veya PID'ye göre filtrele...",
            width=320
        )
        self.search_entry.pack(side="left")
        self.search_entry.bind("<KeyRelease>", self.filter_processes)

        self.info_label = ctk.CTkLabel(
            self.search_frame,
            text=f"Listelenen İşlem: {len(self.all_processes)}",
            text_color="gray"
        )
        self.info_label.pack(side="right")

        # ==========================================
        # 3. TABLO (Treeview)
        # ==========================================
        self.table_frame = ctk.CTkFrame(self)
        self.table_frame.pack(fill="both", expand=True, padx=15, pady=5)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#2b2b2b",
            foreground="white",
            rowheight=30,
            fieldbackground="#2b2b2b",
            font=("Segoe UI", 10),
            borderwidth=0
        )
        style.configure(
            "Treeview.Heading",
            background="#181818",
            foreground="white",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0
        )
        style.map("Treeview", background=[("selected", "#1f538d")])

        columns = ("pid", "name", "status", "cpu", "ram")
        self.tree = ttk.Treeview(self.table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("pid", text="PID", command=lambda: self.sort_column("pid", False))
        self.tree.heading("name", text="İşlem Adı", command=lambda: self.sort_column("name", False))
        self.tree.heading("status", text="Durum", command=lambda: self.sort_column("status", False))
        self.tree.heading("cpu", text="CPU (%)", command=lambda: self.sort_column("cpu", True))
        self.tree.heading("ram", text="Bellek (MB)", command=lambda: self.sort_column("ram", True))

        self.tree.column("pid", width=80, anchor="center")
        self.tree.column("name", width=260, anchor="w")
        self.tree.column("status", width=110, anchor="center")
        self.tree.column("cpu", width=100, anchor="e")
        self.tree.column("ram", width=110, anchor="e")

        scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ==========================================
        # 4. ALT AKSİYON BUTONLARI
        # ==========================================
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.pack(fill="x", padx=15, pady=(10, 15))

        self.btn_end_task = ctk.CTkButton(
            self.bottom_frame,
            text="Görevi Sonlandır",
            fg_color="#c42b1c",
            hover_color="#9e1e12",
            font=("Segoe UI", 11, "bold"),
            command=self.end_selected_process
        )
        self.btn_end_task.pack(side="right")

    def update_system_metrics(self):
        """Toplam CPU ve RAM tüketimini ölçüp arayüz barlarını günceller."""
        if HAS_PSUTIL:
            # Gerçek sistem verisi
            cpu_percent = psutil.cpu_percent(interval=None)
            ram_percent = psutil.virtual_memory().percent
        else:
            # psutil yoksa simülasyon
            import random
            cpu_percent = round(random.uniform(5.0, 45.0), 1)
            ram_percent = round(random.uniform(40.0, 75.0), 1)

        # CustomTkinter Progress Bar 0.0 ile 1.0 arasında çalışır
        self.cpu_bar.set(cpu_percent / 100.0)
        self.ram_bar.set(ram_percent / 100.0)

        # Metin etiketlerini güncelle
        self.cpu_label.configure(text=f"Toplam CPU: %{cpu_percent}")
        self.ram_label.configure(text=f"Toplam RAM: %{ram_percent}")

        # Yüksek kullanımda renk uyarısı (Örn: %80 üstü kırmızı)
        self.cpu_bar.configure(progress_color="#c42b1c" if cpu_percent > 80 else "#1f538d")
        self.ram_bar.configure(progress_color="#c42b1c" if ram_percent > 80 else "#1f538d")

        # 1500 ms (1.5 saniye) sonra bu fonksiyonu tekrar çağır (Arayüz donmaz)
        self.after(1500, self.update_system_metrics)

    def populate_table(self, data):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for proc in data:
            self.tree.insert("", "end", values=(
                proc["pid"],
                proc["name"],
                proc["status"],
                f"{proc['cpu']:.1f} %",
                f"{proc['ram']:.1f} MB"
            ))
        self.info_label.configure(text=f"Listelenen İşlem: {len(data)}")

    def filter_processes(self, event=None):
        query = self.search_entry.get().lower().strip()
        if not query:
            self.populate_table(self.all_processes)
            return

        filtered = [
            p for p in self.all_processes
            if query in p["name"].lower() or query in str(p["pid"])
        ]
        self.populate_table(filtered)

    def sort_column(self, col, reverse):
        if col in ["cpu", "ram", "pid"]:
            self.all_processes.sort(key=lambda x: x[col], reverse=reverse)
        else:
            self.all_processes.sort(key=lambda x: str(x[col]).lower(), reverse=reverse)

        self.populate_table(self.all_processes)
        self.tree.heading(col, command=lambda: self.sort_column(col, not reverse))

    def end_selected_process(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Seçim Yok", "Lütfen sonlandırmak istediğiniz işlemi tablodan seçin.")
            return

        values = self.tree.item(selected_item[0], "values")
        pid = int(values[0])
        name = values[1]

        confirm = messagebox.askyesno(
            "İşlemi Sonlandır",
            f"'{name}' (PID: {pid}) işlemini sonlandırmak istediğinize emin misiniz?"
        )

        if confirm:
            self.all_processes = [p for p in self.all_processes if p["pid"] != pid]
            self.filter_processes()
            messagebox.showinfo("Başarılı", f"PID {pid} başarıyla sonlandırıldı.")


if __name__ == "__main__":
    app = TaskManagerApp()
    app.mainloop()