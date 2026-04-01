"""
=====================================================================
FILE: rrdf_sync.py
PROJECT: Hyperwheel
DESCRIPTION: Automatically synchronizes and matches Raw Research Data
             (RRDF) from the Hyperfine scanner to local DICOM folders.

LOGIC: 
  1. Syncs 'rrdf_' folders from the scanner over SCP.
  2. Extracts acquisition timestamps from DICOM headers.
  3. Matches RRDF files via filename timestamps.
  4. Performs 'Sweep Forward' matching for multi-plane acquisitions.
=====================================================================
"""

import paramiko
from scp import SCPClient
import sys
import datetime
import os
import glob
import shutil
import re
import pydicom
import json

# --- System Configuration ---
DICOM_EXPORT_ROOT = '/var/lib/orthanc/export'
TEMP_DOWNLOAD_DIR = '/tmp/rrdf_download'
NETWORK_CONFIG_PATH = '/usr/share/orthanc/network_config.json'

# Scanner connection parameters (IP is loaded dynamically)
SWOOP_PORT, SWOOP_USER, SWOOP_PASS = 25125, 'rrdf', 'RawSCP'

def get_scanner_ip():
    """Reads the scanner IP address from the network config file."""
    try:
        with open(NETWORK_CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config['scanner_ip']
    except FileNotFoundError:
        print(f"[RRDF] [ERROR] Network configuration file not found at {NETWORK_CONFIG_PATH}")
        print("[RRDF] [INFO] Please run the setup.sh script first.")
        sys.exit(1)
    except (json.JSONDecodeError, KeyError):
        print(f"[RRDF] [ERROR] Could not read 'scanner_ip' from {NETWORK_CONFIG_PATH}")
        sys.exit(1)


def rename_calipr_dicom_files(acquisition_folder_path):
    """
    CALIPR scans produce two DICOM files. This function figures out which is which 
    based on the time they were created and renames them to '_protonDensity' and '_T2map'.
    """
    print(f"[RRDF] [INFO] Renaming CALIPR DICOMs in: '{os.path.basename(os.path.normpath(acquisition_folder_path))}'")

    dcm_files = glob.glob(os.path.join(acquisition_folder_path, '*.dcm'))
    if len(dcm_files) != 2:
        print(f"[RRDF] [WARN] Expected exactly 2 DICOM files for CALIPR, found {len(dcm_files)}. Skipping rename.")
        return

    file_times = []
    try:
        for f_path in dcm_files:
            dcm = pydicom.dcmread(f_path)
            # ContentTime tells us exactly when this specific image was generated
            content_time_str = dcm.ContentTime
            file_times.append({'path': f_path, 'time': float(content_time_str)})
    except Exception as e:
        print(f"[RRDF] [ERROR] Error reading ContentTime from CALIPR DICOMs: {e}. Skipping rename.")
        return

    # Sort chronologically: the first image is protonDensity, the second is the T2map.
    file_times.sort(key=lambda x: x['time'])
    protonDensity_path = file_times[0]['path']
    T2map_path = file_times[1]['path']

    base_name = os.path.basename(os.path.normpath(acquisition_folder_path))
    new_protonDensity_path = os.path.join(acquisition_folder_path, f"{base_name}_protonDensity.dcm")
    new_T2map_path = os.path.join(acquisition_folder_path, f"{base_name}_T2map.dcm")
    
    try:
        print(f"[RRDF] [SUCCESS] Renamed -> '{os.path.basename(new_protonDensity_path)}'")
        os.rename(protonDensity_path, new_protonDensity_path)
        print(f"[RRDF] [SUCCESS] Renamed -> '{os.path.basename(new_T2map_path)}'")
        os.rename(T2map_path, new_T2map_path)
    except Exception as e:
        print(f"[RRDF] [ERROR] Error renaming CALIPR files: {e}")


def relocate_rrdf_files_securely(temp_download_path, dicom_session_path):
    """
    Matches RRDF files to their corresponding DICOM folders in two steps.
    """
    print(f"[RRDF] [INFO] Matching RRDF files for Session: {os.path.basename(dicom_session_path)}")

    # -------------------------------------------------------------------------
    # Step 1: Read the exact time for every DICOM folder
    # -------------------------------------------------------------------------
    dicom_blocks = {} # Groups folders by their exact timestamp
    
    for folder_path in glob.glob(os.path.join(dicom_session_path, '*/')):
        folder_path = os.path.normpath(folder_path)
        
        dicom_files = glob.glob(os.path.join(folder_path, '*.dcm'))
        if not dicom_files:
            continue
        try:
            # stop_before_pixels significantly improves speed
            dcm = pydicom.dcmread(dicom_files[0], stop_before_pixels=True)
            
            acq_dt_str = getattr(dcm, 'AcquisitionDateTime', None)
            if not acq_dt_str:
                date_str = getattr(dcm, 'SeriesDate', getattr(dcm, 'StudyDate', '19700101'))
                time_str = getattr(dcm, 'SeriesTime', '000000')
                acq_dt_str = date_str + time_str
                
            # Remove microseconds so we can match exactly down to the second
            acq_dt_clean = acq_dt_str.split('.')[0]
            acq_time = datetime.datetime.strptime(acq_dt_clean, "%Y%m%d%H%M%S")

            folder_data = {
                'path': folder_path,
                'name': os.path.basename(os.path.normpath(folder_path)).lower()
            }

            if acq_time not in dicom_blocks:
                dicom_blocks[acq_time] = []
            dicom_blocks[acq_time].append(folder_data)

        except Exception as e:
            print(f"[RRDF] [WARN] Could not read DICOM in {folder_path}. Error: {e}")

    # -------------------------------------------------------------------------
    # Step 2: Tie-breaker logic for folders with the exact same time
    # -------------------------------------------------------------------------
    def resolve_concurrent_acquisitions(folders):
        """
        Tie-breaker: If multiple folders share a timestamp (e.g., DWI + ADC),
        picks the primary raw data folder by prioritizing the folder with the lowest Series Number.
        Example: Series 9 (Acquisition) is prioritized over Series 901 (Map).
        """
        if len(folders) == 1:
            return folders[0]['path']
            
        # Sort folders numerically based on the leading digits in the folder name
        try:
            # Matches digits at the start of the string (e.g., '9' from '9_T2_Mapping')
            folders.sort(key=lambda x: int(re.match(r'(\d+)', x['name']).group(1)))
        except (AttributeError, ValueError, IndexError):
            # Fallback to alphabetical sorting if folder names lack leading numbers
            folders.sort(key=lambda x: x['name'])

        return folders[0]['path']
    
    # -------------------------------------------------------------------------
    # Step 3: Extract the exact creation time from the RRDF filenames
    # -------------------------------------------------------------------------
    rrdf_files = []
    # Looks for the timestamp at the end of the filename: _YYYYMMDD_HHMMSS.h5
    rrdf_pattern = re.compile(r'_(\d{8}_\d{6})\.h5$') 
    
    for rrdf_path in glob.glob(os.path.join(temp_download_path, '*.h5')):
        filename = os.path.basename(rrdf_path)
        match = rrdf_pattern.search(filename)
        if match:
            try:
                # Reading the time from the filename is much safer than checking file creation dates
                rrdf_time = datetime.datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
                rrdf_files.append({'path': rrdf_path, 'filename': filename, 'time': rrdf_time})
            except ValueError:
                print(f"[RRDF] [WARN] Could not parse the time from filename {filename}")
        else:
            print(f"[RRDF] [WARN] No timestamp found in filename {filename}")

    # Sort chronologically so we process them in the order they were scanned
    rrdf_files.sort(key=lambda x: x['time'])

    # -------------------------------------------------------------------------
    # Step 4: Phase 1 - Exact Matches
    # -------------------------------------------------------------------------
    unmatched_rrdfs = []
    for rrdf in rrdf_files:
        # If the RRDF timestamp perfectly matches a DICOMs timestamp, move it there
        if rrdf['time'] in dicom_blocks:
            target_folder = resolve_concurrent_acquisitions(dicom_blocks[rrdf['time']])
            dest_foldername = os.path.basename(target_folder)
            print(f"[RRDF] [SUCCESS] Exact match: Moving '{rrdf['filename']}' -> '{dest_foldername}'")
            shutil.move(rrdf['path'], target_folder)
            
            # If we just moved files into a CALIPR folder, trigger the renamer
            if 'calipr' in dest_foldername.lower():
                rename_calipr_dicom_files(target_folder)
        else:
            # Save files without an exact match for Phase 2
            unmatched_rrdfs.append(rrdf)

    # -------------------------------------------------------------------------
    # Step 5: Phase 2 - Sweep Forward
    # -------------------------------------------------------------------------
    sorted_dicom_times = sorted(dicom_blocks.keys())
    for rrdf in unmatched_rrdfs:
        matched = False
        # For leftover RRDF (like single planes of a DWI scan), look forward in time
        # and assign them to the chronological next DICOM folder.
        for dcm_time in sorted_dicom_times:
            if dcm_time > rrdf['time']:
                target_folder = resolve_concurrent_acquisitions(dicom_blocks[dcm_time])
                dest_foldername = os.path.basename(target_folder)
                print(f"[RRDF] [SUCCESS] Sweep forward match: Moving '{rrdf['filename']}' -> '{dest_foldername}'")
                shutil.move(rrdf['path'], target_folder)
                matched = True
                break
        
        if not matched:
            print(f"[RRDF] [WARN] Could not find a matching DICOM folder for '{rrdf['filename']}'.")


def find_local_dicom_sessions(base_export_path):
    """
    Maps local DICOM export folders to the naming convention used by
    the scanner for RRDF folders (rrdf_YYYYMMDD_HHMMSS).
    """
    sessions = {}
    print(f"[RRDF] [INFO] Scanning local directory '{base_export_path}' for DICOM sessions...")
    
    session_pattern = re.compile(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})_(\d{2})_(\d{2})')

    for root, dirs, files in os.walk(base_export_path):
        for d in dirs:
            match = session_pattern.match(d)
            if match:
                year, month, day, hour, minute, second = match.groups()
                expected_rrdf_name = f"rrdf_{year}{month}{day}_{hour}{minute}{second}"
                full_path = os.path.join(root, d)
                sessions[expected_rrdf_name] = full_path
                print(f"[RRDF] [INFO] Found session: {d} (Looking for: {expected_rrdf_name})")
    
    return sessions

def get_remote_rrdf_folders(ssh):
    """Retrieves list of available RRDF directories on the scanner."""
    stdin, stdout, stderr = ssh.exec_command("ls -1 RRDF")
    output = stdout.read().decode('ascii').split('\n')
    return [f.strip() for f in output if f.strip().startswith('rrdf_')]

def main():
    """Main script execution."""
    
    # --- 1. Find local sessions ---
    local_sessions = find_local_dicom_sessions(DICOM_EXPORT_ROOT)
    
    if not local_sessions:
        print("[RRDF] [INFO] No local DICOM sessions found. Exiting.")
        return

    # --- 2. Connect to the scanner ---
    SWOOP_IP = get_scanner_ip()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"[RRDF] [INFO] Connecting to scanner at {SWOOP_IP}...")
        ssh.connect(SWOOP_IP, username=SWOOP_USER, password=SWOOP_PASS, port=SWOOP_PORT)
        print("[RRDF] [SUCCESS] Connection successful.")
    except Exception as e:
        print(f"[RRDF] [ERROR] SSH connection failed: {e}")
        sys.exit(1)

    try:
        # --- 3. Get remote folders ---
        remote_folders = get_remote_rrdf_folders(ssh)
        print(f"[RRDF] [INFO] Found {len(remote_folders)} RRDF folders on the scanner.")

        # --- 4. Process matches ---
        for expected_rrdf, local_session_path in local_sessions.items():
            if expected_rrdf in remote_folders:
                print(f"[RRDF] [INFO] Processing matching session: {expected_rrdf}")
                remote_path = f'RRDF/{expected_rrdf}'

                # Download the files to a temporary folder
                print(f"[RRDF] [INFO] Downloading '{remote_path}' to '{TEMP_DOWNLOAD_DIR}'...")
                if os.path.exists(TEMP_DOWNLOAD_DIR): shutil.rmtree(TEMP_DOWNLOAD_DIR)
                os.makedirs(TEMP_DOWNLOAD_DIR)

                with SCPClient(ssh.get_transport()) as scp:
                    scp.get(remote_path, recursive=True, local_path=TEMP_DOWNLOAD_DIR)

                temp_local_rrdf_path = os.path.join(TEMP_DOWNLOAD_DIR, expected_rrdf)
                print("[RRDF] [SUCCESS] Download complete.")

                # Match files to DICOM folders
                relocate_rrdf_files_securely(temp_local_rrdf_path, local_session_path)

                # Optional: Delete files from the scanner to free up space
                # print(f"[RRDF] [INFO] Deleting '{expected_rrdf}' from the scanner...")
                # stdin, stdout, stderr = ssh.exec_command(f"rm -r {remote_path}")
                # if stdout.channel.recv_exit_status() == 0:
                #     print(f"Successfully deleted.")
                # else:
                #     print(f"Failed to delete: {stderr.read().decode('ascii').strip()}")

            else:
                print(f"[RRDF] [INFO] Skipping session: {os.path.basename(local_session_path)} (Folder '{expected_rrdf}' not found on scanner)")

    finally:
        # --- 5. Clean up ---
        if os.path.exists(TEMP_DOWNLOAD_DIR):
            shutil.rmtree(TEMP_DOWNLOAD_DIR)
            print(f"[RRDF] [INFO] Cleaned up temp folder: {TEMP_DOWNLOAD_DIR}")
        print("[RRDF] [INFO] Closed SSH connection.")
        ssh.close()

if __name__ == '__main__':
    main()