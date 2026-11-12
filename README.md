# COME 361 - Operating Systems Term Project
## Process & System Performance Monitor (Mini Task Manager)

### Group Members
* Emir Sanal
* Tuna Çakmur
* Berke Çelik
* Yağmur Şimşek

### Team Responsibilities

# Tuna Çakmur - Process Management
* List all running processes.
* Retrieve and display Process IDs (PID).
* Start new processes.
* Terminate selected processes.
* Implement process management functions using Python and `psutil`.

# Berke Çelik - System Monitoring
* Monitor real-time CPU usage.
* Monitor RAM usage.
* Collect process statistics.
* Update system performance data periodically.

# Emir Sanal – GUI Development
* Design the Task Manager interface.
* Create the process table.
* Implement buttons and user interactions.
* Add process searching and sorting features.

# Yağmur Şimşek – OS Integration & Testing
* Research operating system APIs.
* Handle process states.
* Implement error handling.
* Integrate all project modules.
* Test the application and fix integration issues.

## Project Demos

### Windows Demos

**Demo 1 — 25% Progress**

Initial development stage of the Windows Task Manager.

**Demo 2 — 40% Progress**

Intermediate development stage with additional features.

**Demo 3 — 75% Progress**

Updated Windows version with improved process management and system monitoring.

**Implemented Features:**
- Process listing and PID display
- CPU and RAM monitoring
- Process searching and filtering
- Process details
- Process termination with user confirmation
- Automatic refresh every 2 seconds

**Demo Files:**
- Python source code
- Application screenshot

### Current Status

Both Windows and macOS versions are under development. Cross-platform integration and testing are ongoing.
### Description
A GUI-based system performance and process management tool written in Python using `PyQt6` and `psutil`.

### Prerequisites
* Python 3.x
* PyQt6
* psutil

### How to Run
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
