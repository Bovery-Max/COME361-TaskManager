
import psutil
import subprocess


def list_processes():
    processes = []

    for proc in psutil.process_iter(
        ["pid", "name", "ppid", "create_time"]
    ):
        try:
            processes.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return processes


def terminate_process(pid, expected_create_time=None):
    process = psutil.Process(pid)

    # PID'nin başka bir işlem tarafından kullanılmasını önle
    if expected_create_time is not None:
        if abs(process.create_time() - expected_create_time) > 0.01:
            raise psutil.NoSuchProcess(pid)

    # Sistem işlemlerini koru
    if pid in (0, 1):
        raise ValueError("Bu sistem işlemi sonlandırılamaz.")

    process.terminate()


def start_process(program, args=None):
    if args is None:
        args = []

    process = subprocess.Popen([program] + args)

    return process.pid
