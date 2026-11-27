"""Backend: proses listeleme, sonlandırma ve başlatma (psutil + subprocess)."""
import os
import subprocess

import psutil

# 0: Idle/scheduler, 1: init/systemd, 4: Windows System, kendi PID'imiz
PROTECTED_PIDS = {0, 1, 4, os.getpid()}


def list_processes():
    """[{pid, name, ppid, create_time}, ...] döner."""
    if os.name != "nt":
        return [p.info for p in psutil.process_iter(["pid", "name", "ppid", "create_time"])]
    # Windows'ta ppid() her çağrıda tüm sistemin snapshot'ını alır (N işlem -> N snapshot, ~3 sn).
    # Haritayı bir kez kurup tüm işlemler için kullanıyoruz.
    # ponytail: psutil'in özel API'si; psutil güncellemesinde kırılırsa yavaş yola düşer.
    try:
        from psutil._pswindows import ppid_map
        ppids = ppid_map()
    except Exception:
        return [p.info for p in psutil.process_iter(["pid", "name", "ppid", "create_time"])]
    rows = []
    for p in psutil.process_iter(["pid", "name", "create_time"]):
        p.info["ppid"] = ppids.get(p.info["pid"])
        rows.append(p.info)
    return rows


def terminate_process(pid, expected_create_time=None):
    """SIGTERM / TerminateProcess gönderir.
    ValueError: korumalı PID. NoSuchProcess: işlem yok ya da PID yeniden kullanılmış."""
    if pid in PROTECTED_PIDS:
        raise ValueError(f"PID {pid} korumalı bir sistem işlemi, sonlandırılamaz.")
    p = psutil.Process(pid)
    if expected_create_time is not None and abs(p.create_time() - expected_create_time) > 0.01:
        raise psutil.NoSuchProcess(pid, None, "PID başka bir işleme ait")
    p.terminate()


def terminate_tree(pid, expected_create_time=None):
    """İşlemi ve tüm alt işlemlerini (child, grandchild...) sonlandırır.
    Önce child'lar, en son kendisi: parent'ın ölen child'ı yeniden başlatmasını önler.
    Döner: (sinyal gönderilen Process listesi, yetki yetmediği için atlanan Process listesi).
    ValueError: korumalı PID. NoSuchProcess / AccessDenied: kök işlem için."""
    if pid in PROTECTED_PIDS:
        raise ValueError(f"PID {pid} korumalı bir sistem işlemi, sonlandırılamaz.")
    root = psutil.Process(pid)
    if expected_create_time is not None and abs(root.create_time() - expected_create_time) > 0.01:
        raise psutil.NoSuchProcess(pid, None, "PID başka bir işleme ait")
    children = [c for c in root.children(recursive=True) if c.pid not in PROTECTED_PIDS]
    sent, denied = [], []
    for proc in children + [root]:
        try:
            proc.terminate()
            sent.append(proc)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            if proc is root:
                raise
            denied.append(proc)
    return sent, denied


def start_process(program, args=()):
    """Yeni işlem başlatır, PID'sini döner."""
    return subprocess.Popen([program, *args]).pid


if __name__ == "__main__":
    procs = list_processes()
    assert procs and {"pid", "name", "ppid", "create_time"} <= procs[0].keys()
    try:
        terminate_process(os.getpid())
        raise AssertionError("own PID must be protected")
    except ValueError:
        pass

    # terminate_tree: parent + child birlikte kapanmalı
    import sys
    import time
    child_code = "import time; time.sleep(30)"
    parent_code = ("import subprocess, sys, time; "
                   f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(30)")
    parent = subprocess.Popen([sys.executable, "-c", parent_code])
    deadline = time.time() + 10
    while not psutil.Process(parent.pid).children() and time.time() < deadline:
        time.sleep(0.1)
    tree = [psutil.Process(parent.pid)] + psutil.Process(parent.pid).children(recursive=True)
    assert len(tree) >= 2, "child process did not start"
    sent, denied = terminate_tree(parent.pid)
    assert not denied and {p.pid for p in tree} <= {p.pid for p in sent}
    gone, alive = psutil.wait_procs(tree, timeout=5)
    parent.wait(5)
    assert not alive, f"still alive: {alive}"
    print(f"ok: {len(procs)} processes, tree of {len(tree)} terminated")
