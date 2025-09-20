# Orthanc to Flywheel Automation Pipeline

## 1. Overview

This document describes the automated pipeline responsible for receiving DICOM and RRDF files from a Hyperfine MRI scanner, processing it, and securely uploading it to a designated project in Flywheel.

The system is built around an **Orthanc DICOM server** running on a Raspberry Pi 5. The core logic is handled by a custom **Lua script** that is triggered by Orthanc events. This script orchestrates helper scripts and configuration files to create a robust workflow.

The primary goals of this pipeline are:
* To create a Flywheel-compatible, organized copy of all incoming DICOM data.
* To automatically synchronize raw research data (RRDF, `.h5` files) from the scanner and associate it with the correct DICOM series.
* To securely upload the complete dataset (DICOM and RRDF files) to the correct Flywheel project.
* To safely clean up local storage only after verifying a successful upload.

***
## 2. System Architecture

When the pipeline is installed by the `setup.sh` script, all files are organized into the following structure on the Raspberry Pi. Understanding these locations is essential for troubleshooting and maintenance.

```
/
|-- etc/
|   `-- orthanc/
|       |-- orthanc.json         # Main Orthanc server configuration
|       `-- credentials.json     # Login credentials for the Orthanc web UI
|
|-- usr/
|   `-- share/
|       `-- orthanc/             # Contains all custom scripts and configurations
|           |-- export.lua       # Main automation script for the pipeline
|           |-- rrdf_sync.py     # Script to sync raw data from the scanner
|           |-- routing.json     # Maps studies to Flywheel projects
|           |-- .fw_keychain.json # Stores Flywheel API keys
|           `-- network_config.json # Auto-generated file with the scanner's IP
|
`-- var/
    `-- lib/
        `-- orthanc/             # "Home" directory for the orthanc user
            |-- .fw/             # Flywheel CLI installation
            |-- python_env/      # Dedicated Python virtual environment
            |-- db-v6/           # Orthanc's internal storage directory
            `-- export/          # Flywheel-compatible staging area for uploads
```

---
## 3. Configuration Files

The pipeline's behavior is controlled by several key configuration files.

### `orthanc.json`
This is the main configuration file for the Orthanc server. While many settings are left as default, the following are essential for the pipeline's operation.

#### Key Edited Settings
* **`LuaScripts`**: Points to `/usr/share/orthanc/export.lua`, telling Orthanc to load and execute our main automation script in response to DICOM events.
* **`DicomAet`**: Sets the DICOM Application Entity Title to **`HYPERWHEEL`**. The MRI scanner must be configured to send data to this AET.
* **`RestApiWriteToFileSystemEnabled`**: Set to `true`, this allows the Lua script to write files to the local filesystem, which is necessary to create the staging area in `/var/lib/orthanc/export/`.
* **`ExecuteLuaEnabled`**: Set to `true`, this enables remote script execution capabilities used by the pipeline.

#### Important Default Settings
* **`StorageDirectory`** & **`IndexDirectory`**: Both are set to `/var/lib/orthanc/db-v6/`, the internal database location for Orthanc.
* **`DicomPort`**: Set to **`4242`**. This is the TCP port the scanner must be configured to send DICOM files to.
* **`StableAge`**: Set to `60` seconds. This tells Orthanc to wait for 60 seconds of inactivity after the last DICOM file of a study arrives before triggering the main upload process. This setting may need to be adjusted if internet speed is slow.

---
### Other Configuration Files
* **`routing.json`**: A user-configured file that maps a study name (from DICOM tag `OperatorsName`) to a specific Flywheel project destination.
* **`.fw_keychain.json`**: A user-configured file that maps a study name to the corresponding Flywheel API key required for uploads. This keeps sensitive API keys separate from the main application logic and out of version control.
* **`credentials.json`**: Stores the registered username and password for accessing the Orthanc web interface.
* **`network_config.json`**: An auto-generated file that stores the detected scanner IP address, allowing the RRDF sync script to adapt to different scanner networks.

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
    * Reads the scanner's IP address from the auto-generated `network_config.json`.
    * Connects to the scanner via SSH.
    * Finds and downloads the latest raw data folder.
    * Matches RRDF files to DICOM acquisition folders using timestamps and renames them accordingly.
4.  **Flywheel Upload**: The script reads `routing.json` and `.fw_keychain.json` to get the correct destination and API key. It then logs in and runs `fw import` to upload the entire study from the staging area.


### Phase 3: Verification & System Cleanup
1.  After the upload, the `VerifyAndCleanupStudy` function is called.
2.  **Verification**: The script performs a directory-by-directory check, comparing local files against the remote files on Flywheel using `fw ls` to ensure the upload was successful.
3.  **Safe Deletion**:
    * If **every single file** is verified, the script deletes the data from the local staging area (`/export`) and then from Orthanc's internal database (`/db-v6/`).
    * If even one file cannot be verified, **no deletion occurs**, and all local data is preserved for manual inspection.



### Sources
* Flywheel CLI: https://flywheel-io.gitlab.io/tools/app/cli/fw-beta/
* The Flywheel `ls` command: https://docs.flywheel.io/CLI/reference/ls/
* Server-side scripting with Lua: https://orthanc.uclouvain.be/book/users/lua.html
* Cheat-sheet of the Rest API: https://orthanc.uclouvain.be/book/users/rest-cheatsheet.html


---
## 5. Setup Guide

The entire setup process is automated by the `setup.sh` script.

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/UNITY-Physics/hyperwheel.git
    ```

2.  **Run the Setup Script**
    Connect the Raspberry Pi to the scanner via Ethernet, then run the setup script from within the cloned directory.
    ```bash
    cd hyperwheel
    chmod +x setup.sh
    sudo ./setup.sh
    ```
3.  **Configure Your Credentials**
    The script will pause and prompt you to edit two files in a separate terminal. You must fill these in with your site-specific details:
    * `/usr/share/orthanc/routing.json`
    * `/usr/share/orthanc/.fw_keychain.json`

#### 9. Enforce `sudo` Password
For added security, require a password for administrative commands.
* Access the file that controls password rules for users
    ```bash
    sudo visudo /etc/sudoers.d/010_pi-nopasswd
    ```
    
* Find the line
    
    `<your_username> ALL=(ALL) NOPASSWD: ALL`
    
    and change it to
    
    `<your_username> ALL=(ALL) PASSWD: ALL`

#### 10. Finalize Installation
Enable the Orthanc service to start on boot and restart it to apply all changes.
```bash
sudo systemctl enable --now orthanc
```
The setup is complete. The pipeline's activity can be monitored by watching the Orthanc log:
```bash
sudo tail -f /var/log/orthanc/Orthanc.log
```