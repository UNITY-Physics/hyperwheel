# Orthanc to Flywheel Automation Pipeline

## 1. Overview

This document describes the automated pipeline responsible for receiving DICOM and RRDF data from a Hyperfine MRI scanner, processing it, and securely uploading it to a designated project in Flywheel.

The system is built around an **Orthanc DICOM server** running on a Raspberry Pi 5. The core logic is handled by a custom **Lua script** that is triggered by Orthanc events. This script orchestrates helper scripts and configuration files to create a robust workflow.

The primary goals of this pipeline are:
* To create a human-readable, organized copy of all incoming DICOM data.
* To automatically synchronize raw research data (RRDF, `.h5` files) from the scanner and associate it with the correct DICOM series.
* To securely upload the complete dataset (DICOM and RRDF files) to the correct Flywheel project.
* To safely clean up local storage only after verifying a successful upload.

***
## 2. System Architecture

The pipeline relies on several key components and directories.

### Key Directories
* `/var/lib/orthanc/`
    This serves as the "home" directory for the `orthanc` user where all **image data** and **dependencies** are installed to avoid permission issues.
    * `./.fw/`: The Flywheel CLI installation.
    * `./python_env/`: A dedicated Python virtual environment containing necessary packages (`paramiko`, `scp`, `pydicom`).
    * `./db-v6/`: This is the main **Orthanc storage directory**. DICOM files are stored here in Orthanc's internal format. This directory is not intended for human interaction.
    * `./export/`: This is the **human-readable staging area**. The `export.lua` script organizes DICOM and raw data here into a clear `Study/Subject/Session/Acquisition` hierarchy before it's uploaded to Flywheel.
* `/usr/share/orthanc/` and `/etc/orthanc/`
    These directories contain all the **custom scripts and configurations** that define the pipeline's logic.


***
## 3. Configuration Files

The pipeline's behavior is controlled by three main configuration files.

### `orthanc.json`
This is the main configuration file for the Orthanc server. While many settings are left as default, the following are essential for the pipeline's operation.

#### Key Edited Settings
* `"LuaScripts"`: Points to `/usr/share/orthanc/export.lua`, telling Orthanc to load and execute our main automation script in response to DICOM events.
* `"DicomAet"`: Sets the DICOM Application Entity Title to **`HYPERWHEEL`**. This is the name the MRI scanner must be configured to send data to.
* `"RestApiWriteToFileSystemEnabled"`: Set to `true`, this critical setting allows the Lua script to write files to the local filesystem, which is necessary to create the staging area in `/var/lib/orthanc/export/`.
* `"ExecuteLuaEnabled"`: Set to `true`, this enables remote script execution capabilities used by the pipeline.

#### Important Default Settings
* `"StorageDirectory"` & `"IndexDirectory"`: Both are set to `/var/lib/orthanc/db-v6/`, the internal database location for Orthanc.
* `"DicomPort"`: Set to **`4242`**. This is the TCP port the scanner must be configured to send DICOM files to.
* `"StableAge"`: Set to `60` seconds. This tells Orthanc to wait for 60 seconds of inactivity after the last DICOM file of a study arrives before triggering the main upload process in `export.lua`.

### `routing.json`
This file acts as a **switchboard**. It maps the study name (read from the DICOM tag `OperatorsName`) to a specific Flywheel project destination. This allows for easy configuration of new studies without changing the core script.
* **Example**: `{"study": "fw://group/project"}`

### `.fw_keychain.json`
This is a **secure credentials file**. It maps a study name to the corresponding Flywheel API key needed to perform the upload. This keeps sensitive API keys separate from the main application logic.

***
## 4. The Automation Workflow (Step-by-Step)

The process begins the moment a DICOM file is sent from the scanner.

### Phase 1: DICOM Arrival (`OnStoredInstance`)
1.  A DICOM instance is sent to Orthanc's DICOM port (4242).
2.  Orthanc stores the file in its internal storage (`/var/lib/orthanc/db-v6/`).
3.  This event triggers the `OnStoredInstance` function in **`export.lua`**.
4.  The script reads the DICOM tags (`PatientID`, `StudyDate`, `SeriesDescription`, etc.) to build a structured path.
5.  A copy of the DICOM file is written to the export directory in a Flywheel-compatible structure.
    * **Example Path**: `/var/lib/orthanc/export/study/PatientID/YYYY-MM-DD_hh_mm_ss/...`

### Phase 2: Study Upload (`OnStableStudy`)
1.  After the final DICOM file of a study arrives, Orthanc waits for the `StableAge` of 60 seconds.
2.  This triggers the `OnStableStudy` function in **`export.lua`**.
3.  **RRDF Sync**: The script executes **`rrdf_sync.py`**. This Python script:
    * Connects to the scanner via SSH.
    * Finds and downloads the latest raw data folder (`rrdf_*`).
    * Matches the RRDF files to the correct DICOM acquisition folders using timestamps.
    * Moves and renames the RRDF files into the local staging area.
4.  **Flywheel Login**: The script reads the `routing.json` and `.fw_keychain.json` files to get the correct API key and destination for the current study. It then executes the `fw login` command.
5.  **Flywheel Import**: The script runs the `fw import folder` command, pointing it at the top-level study directory in the staging area (e.g., `/var/lib/orthanc/export/prisma`). This uploads all contents (DICOMs and synced RRDF files) to the correct Flywheel project.

### Phase 3: Verification & System Cleanup
1.  After the import command finishes, the `VerifyAndCleanupStudy` function is called.
2.  **File-by-File Check**: The script iterates through every file in the local staging area and, for each one, runs an `fw ls` command to confirm that an identical file exists in the correct location on Flywheel.
3.  **Safe Deletion**: The script follows a strict "all-or-nothing" safety protocol.
    * **Only if every single file is successfully verified on Flywheel**, the script proceeds with a two-step cleanup:
        1.  First, it deletes all the verified files and the empty directory structure from the human-readable staging area (`/var/lib/orthanc/export/`).
        2.  Second, it sends a `DELETE` command to the Orthanc server's REST API to securely remove the original study from the internal database (`/var/lib/orthanc/db-v6/`).
    * If even one file *cannot* be verified, **no deletion occurs**. Both the staged copy and the original Orthanc database copy are kept for safety and manual review.


### Sources
* Flywheel CLI: https://flywheel-io.gitlab.io/tools/app/cli/fw-beta/
* Server-side scripting with Lua: https://orthanc.uclouvain.be/book/users/lua.html
* Cheat-sheet of the Rest API: https://orthanc.uclouvain.be/book/users/rest-cheatsheet.html


### Installation steps
1. Update RPi

`sudo apt upgrade -y`

2. Install Orthanc

`sudo apt install orthanc`

3. Install Flywheel CLI

`sudo -u orthanc sh -c "curl -sSL https://storage.googleapis.com/flywheel-dist/fw-cli/stable/install.sh | FW_CLI_INSTALL_DIR=/usr/share/orthanc/flywheel/ sh"`

4. Create Python Viritual Environment

`sudo -u orthanc sh -c "python -m venv /var/lib/orthanc/python_env"`

5. Install Python dependencies

`sudo -u orthanc sh -c "/var/lib/orthanc/python_env/bin/pip install paramiko pydicom scp datetime"`

6. Create folders (export)

`???`

7. Set permissions

`???`
