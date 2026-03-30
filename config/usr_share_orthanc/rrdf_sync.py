"""
hyperwheel_rrdf_sync.py

For Project Hyperwheel

This script automates the synchronization of raw research data files (RRDF)
from a Hyperfine MRI scanner to the local data server.

Workflow:
1.  Scans the local DICOM export directory to find existing session folders.
2.  Connects to the scanner via SSH using paramiko.
3.  For each local session, looks for a matching RRDF folder on the scanner.
4.  Downloads the matching RRDF folder via SCP.
5.  Matches each downloaded RRDF file to its corresponding DICOM acquisition
    folder by checking if its timestamp falls within the Acquisition Window.
6.  Relocates the RRDF file without renaming it.
7.  Performs special renaming for CALIPR series DICOM files.
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

# --- Configuration ---
DICOM_EXPORT_ROOT = '/var/lib/orthanc/export'
TEMP_DOWNLOAD_DIR = '/tmp/rrdf_download'
NETWORK_CONFIG_PATH = '/usr/share/orthanc/network_config.json'

# Scanner connection details (IP is loaded dynamically)
SWOOP_PORT, SWOOP_USER, SWOOP_PASS = 25125, 'rrdf', 'RawSCP'

def get_scanner_ip():
    """Reads the scanner IP from the network config file."""
    try:
        with open(NETWORK_CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config['scanner_ip']
    except FileNotFoundError:
        print(f"Error: Network config file not found at {NETWORK_CONFIG_PATH}")
        print("Please run the setup_network.sh script first.")
        sys.exit(1)
    except (json.JSONDecodeError, KeyError):
        print(f"Error: Could not read 'scanner_ip' from {NETWORK_CONFIG_PATH}")
        sys.exit(1)


def rename_calipr_dicom_files(acquisition_folder_path):
    """
    Renames the two DICOM files in a CALIPR acquisition folder based on
    their ContentTime tag, identifying them as protonDensity and T2map.
    """
    print(f"  Performing special CALIPR DICOM renaming in: '{os.path.basename(os.path.normpath(acquisition_folder_path))}'")

    # Find the two DICOM files in the folder.
    dcm_files = glob.glob(os.path.join(acquisition_folder_path, '*.dcm'))
    if len(dcm_files) != 2:
        print(f"  Warning: Expected 2 DICOM files for CALIPR renaming, but found {len(dcm_files)}. Skipping.")
        return

    # Read the ContentTime from each file to determine its type.
    file_times = []
    try:
        for f_path in dcm_files:
            dcm = pydicom.dcmread(f_path)
            content_time_str = dcm.ContentTime
            file_times.append({'path': f_path, 'time': float(content_time_str)})
    except Exception as e:
        print(f"  Error reading ContentTime from CALIPR DICOMs: {e}. Skipping rename.")
        return

    # Sort by time: the earlier file is protonDensity, the later is T2map.
    file_times.sort(key=lambda x: x['time'])
    protonDensity_path = file_times[0]['path']
    T2map_path = file_times[1]['path']

    # Rename the files based on the acquisition folder's name.
    base_name = os.path.basename(os.path.normpath(acquisition_folder_path))
    new_protonDensity_path = os.path.join(acquisition_folder_path, f"{base_name}_protonDensity.dcm")
    new_T2map_path = os.path.join(acquisition_folder_path, f"{base_name}_T2map.dcm")
    try:
        print(f"  Renaming protonDensity file -> '{os.path.basename(new_protonDensity_path)}'")
        os.rename(protonDensity_path, new_protonDensity_path)
        print(f"  Renaming T2map file -> '{os.path.basename(new_T2map_path)}'")
        os.rename(T2map_path, new_T2map_path)
    except Exception as e:
        print(f"  Error during CALIPR DICOM rename: {e}")


def get_dicom_acquisition_times(session_path):
    """Scans a session's subfolders to extract acquisition windows (start and end) from DICOM metadata."""
    acq_windows = {}
    for folder_path in glob.glob(os.path.join(session_path, '*/')):
        dicom_files = glob.glob(os.path.join(folder_path, '*.dcm'))
        if not dicom_files:
            continue
        try:
            dcm = pydicom.dcmread(dicom_files[0])
            
            # 1. Parse AcquisitionDateTime (Start Time)
            # Handle potential fractional seconds safely
            acq_dt_str = dcm.AcquisitionDateTime
            try:
                acq_start = datetime.datetime.strptime(acq_dt_str, "%Y%m%d%H%M%S.%f")
            except ValueError:
                acq_start = datetime.datetime.strptime(acq_dt_str.split('.')[0], "%Y%m%d%H%M%S")
            
            # 2. Parse AcquisitionDuration
            acq_dur_str = getattr(dcm, 'AcquisitionDuration', 0)
            acq_duration_sec = float(acq_dur_str)
            
            # 3. Calculate End Time
            acq_end = acq_start + datetime.timedelta(seconds=acq_duration_sec)
            
            acq_windows[folder_path] = (acq_start, acq_end)
        except Exception as e:
            print(f"Warning: Could not read DICOM metadata for {dicom_files[0]}. Error: {e}")
    return acq_windows


def parse_rrdf_timestamps(rrdf_folder_path):
    """Parses timestamps from the filenames of RRDF files."""
    rrdf_times = {}
    for file_path in glob.glob(os.path.join(rrdf_folder_path, '*.h5')):
        filename = os.path.basename(file_path)
        match = re.search(r'_(\d{8})_(\d{6})\.h5', filename)
        if match:
            date_part, time_part = match.groups()
            rrdf_datetime = datetime.datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S")
            rrdf_times[file_path] = rrdf_datetime
    return rrdf_times


def relocate_rrdf_files_by_time(temp_download_path, dicom_session_path):
    """Moves each RRDF file to the DICOM acquisition folder if its timestamp falls within the DICOM's time window."""
    print(f"\n--- Starting RRDF Relocation for Session: {os.path.basename(dicom_session_path)} ---")
    
    # Get the time windows (start and end) for each DICOM folder
    dicom_acq_windows = get_dicom_acquisition_times(dicom_session_path)
    # Get the extracted timestamps for each RRDF file
    rrdf_file_times = parse_rrdf_timestamps(temp_download_path)

    for rrdf_path, rrdf_time in rrdf_file_times.items():
        matched_folder = None
        
        # Check if the RRDF file time falls within any DICOM acquisition window
        for folder_path, (acq_start, acq_end) in dicom_acq_windows.items():
            if acq_start <= rrdf_time <= acq_end:
                matched_folder = folder_path
                break  # Stop searching once a valid window is found

        if matched_folder:
            original_rrdf_filename = os.path.basename(rrdf_path)
            dest_foldername = os.path.basename(os.path.normpath(matched_folder))
            try:
                print(f"Match found: Moving '{original_rrdf_filename}' -> '{dest_foldername}'")
                # Move the file without renaming it
                shutil.move(rrdf_path, matched_folder)

                # If this is a CALIPR acquisition, trigger special DICOM renaming.
                if 'calipr' in dest_foldername.lower():
                    rename_calipr_dicom_files(matched_folder)
            except Exception as e:
                print(f"  An error occurred during move: {e}")
        else:
            print(f"Warning: '{os.path.basename(rrdf_path)}' (Timestamp: {rrdf_time}) did not fall within any DICOM acquisition window. Not moved.")


def find_local_dicom_sessions(base_export_path):
    """
    Scans the local export directory and returns a dictionary of session paths
    mapped to their expected RRDF folder name.
    Looks for folders matching the pattern YYYY-MM-DD_HH_MM_SS
    """
    sessions = {}
    print(f"Scanning local directory '{base_export_path}' for DICOM sessions...")
    
    # Regex to match the date format used in export.lua
    session_pattern = re.compile(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})_(\d{2})_(\d{2})')

    for root, dirs, files in os.walk(base_export_path):
        for d in dirs:
            match = session_pattern.match(d)
            if match:
                year, month, day, hour, minute, second = match.groups()
                # Construct the expected RRDF folder name format
                expected_rrdf_name = f"rrdf_{year}{month}{day}_{hour}{minute}{second}"
                full_path = os.path.join(root, d)
                sessions[expected_rrdf_name] = full_path
                print(f"  Found session: {d} (Expected RRDF: {expected_rrdf_name})")
    
    return sessions

def get_remote_rrdf_folders(ssh):
    """Gets a list of all rrdf_ folders on the scanner."""
    stdin, stdout, stderr = ssh.exec_command("ls -1 RRDF")
    output = stdout.read().decode('ascii').split('\n')
    # Filter for folders starting with 'rrdf_'
    return [f.strip() for f in output if f.strip().startswith('rrdf_')]

def main():
    """Main function to orchestrate the entire process."""
    
    # --- 0. Find Local DICOM Sessions ---
    local_sessions = find_local_dicom_sessions(DICOM_EXPORT_ROOT)
    
    if not local_sessions:
        print("No local DICOM sessions found in the export directory. Exiting.")
        return

    # --- 1. SSH Connection ---
    SWOOP_IP = get_scanner_ip() # Get the IP dynamically
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"\nConnecting to scanner at {SWOOP_IP}...")
        ssh.connect(SWOOP_IP, username=SWOOP_USER, password=SWOOP_PASS, port=SWOOP_PORT)
        print("Connection successful.\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        # --- 2. Check Remote Folders ---
        remote_folders = get_remote_rrdf_folders(ssh)
        print(f"Found {len(remote_folders)} RRDF folders on the scanner.")

        # --- 3. Process Each Local Session ---
        for expected_rrdf, local_session_path in local_sessions.items():
            if expected_rrdf in remote_folders:
                print(f"\n--- Processing matching session: {expected_rrdf} ---")
                remote_path = f'RRDF/{expected_rrdf}'

                # --- 4. Download ---
                print(f"Downloading remote folder '{remote_path}' to '{TEMP_DOWNLOAD_DIR}'...")
                if os.path.exists(TEMP_DOWNLOAD_DIR): shutil.rmtree(TEMP_DOWNLOAD_DIR)
                os.makedirs(TEMP_DOWNLOAD_DIR)

                with SCPClient(ssh.get_transport()) as scp:
                    scp.get(remote_path, recursive=True, local_path=TEMP_DOWNLOAD_DIR)

                temp_local_rrdf_path = os.path.join(TEMP_DOWNLOAD_DIR, expected_rrdf)
                print("Download complete.")

                # --- 5. Relocate and Rename ---
                relocate_rrdf_files_by_time(temp_local_rrdf_path, local_session_path)

                # Optional: Delete from Scanner (Uncomment if desired)
                # print(f"Deleting '{expected_rrdf}' from the remote server...")
                # stdin, stdout, stderr = ssh.exec_command(f"rm -r {remote_path}")
                # if stdout.channel.recv_exit_status() == 0:
                #     print(f"'{expected_rrdf}' successfully deleted from scanner.")
                # else:
                #     print(f"Error deleting from scanner: {stderr.read().decode('ascii').strip()}")

            else:
                print(f"\nSkipping session: {os.path.basename(local_session_path)}")
                print(f"  Reason: Expected RRDF folder '{expected_rrdf}' not found on the scanner.")

    finally:
        # --- 6. Clean Up ---
        if os.path.exists(TEMP_DOWNLOAD_DIR):
            shutil.rmtree(TEMP_DOWNLOAD_DIR)
            print(f"\nCleaned up temporary directory: {TEMP_DOWNLOAD_DIR}")
        print("Closing SSH connection.")
        ssh.close()

if __name__ == '__main__':
    main()