#!/bin/bash

# ==============================================================================
# Hyperwheel - Complete Setup Script (for use from GitHub repo)
# ==============================================================================
# This script automates the full installation and configuration of the
# Hyperfine-to-Flywheel pipeline on a Raspberry Pi. It should be run with
# sudo privileges from within the cloned 'hyperwheel' repository directory.
# ==============================================================================

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Configuration Variables ---
REPO_DIR=$(pwd)
ORTHANC_USER="orthanc"
PYTHON_ENV_DIR="/var/lib/orthanc/python_env"
FW_CLI_DIR="/var/lib/orthanc/.fw"
EXPORT_DIR="/var/lib/orthanc/export"

# --- Helper Functions ---
print_step() {
  echo ""
  echo "============================================================"
  echo "=> $1"
  echo "============================================================"
}

# --- Main Setup Functions ---
check_root() {
  if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo."
    exit 1
  fi
}

update_system() {
  print_step "Updating System Packages"
  apt update && apt upgrade -y
}

install_dependencies() {
  print_step "Installing Orthanc"
  apt install -y orthanc
}

setup_network() {
  print_step "Auto-Configuring Network"
  local config_file="/usr/share/orthanc/network_config.json"
  
  echo "Detecting current network from DHCP-assigned IP..."
  local current_ip
  current_ip=$(ip addr show eth0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)

  if [[ -z "$current_ip" ]]; then
      echo "Error: Could not determine current IP for eth0. Is it connected?"
      exit 1
  fi
  echo "Current IP detected: $current_ip"

  local pi_ip gateway scanner_ip
  if [[ "$current_ip" == "10.42.0."* ]]; then
      echo "Detected new scanner network (v9.0.1+)."
      pi_ip="10.42.0.88/24"; gateway="10.42.0.1"; scanner_ip="10.42.0.1"
  elif [[ "$current_ip" == "10.0.0."* ]]; then
      echo "Detected old scanner network (< v9.0.1)."
      pi_ip="10.0.0.88/24"; gateway="10.0.0.41"; scanner_ip="10.0.0.41"
  else
      echo "Error: Unknown network detected for IP $current_ip."
      exit 1
  fi

  # --- Find connection name dynamically ---
  echo "Detecting NetworkManager connection profile for eth0..."
  local eth_con_name
  eth_con_name=$(nmcli -t -f NAME,DEVICE connection show | awk -F: '$2=="eth0" {print $1}' | head -n 1)

  if [[ -z "$eth_con_name" ]]; then
      echo "No existing connection profile found for eth0."
      echo "Creating a new dedicated profile named 'eth0-hyperwheel'..."
      nmcli connection add type ethernet ifname eth0 con-name "eth0-hyperwheel"
      eth_con_name="eth0-hyperwheel"
  else
      echo "Successfully detected active ethernet profile: '$eth_con_name'"
  fi
  # ---------------------------------------------------

  echo "Configuring static IP for eth0 to $pi_ip on profile '$eth_con_name'..."
  nmcli connection modify "$eth_con_name" ipv4.addresses "$pi_ip" ipv4.gateway "$gateway"
  nmcli connection modify "$eth_con_name" ipv4.dns "1.1.1.1 1.0.0.1"
  nmcli connection modify "$eth_con_name" ipv4.method manual
  
  echo "Restarting connection to apply changes..."
  nmcli connection down "$eth_con_name" && nmcli connection up "$eth_con_name"

  echo "Generating network config file for Python scripts..."
  tee "$config_file" > /dev/null <<EOL
{
  "scanner_ip": "$scanner_ip"
}
EOL
  echo "Network setup complete."
}

install_flywheel_cli() {
  print_step "Installing and Configuring Flywheel CLI"
  
  if [ -f "$FW_CLI_DIR/fw-beta" ]; then
    echo "Flywheel CLI already installed. Skipping installation."
  else
    # Install the CLI as the orthanc user
    sudo -u "$ORTHANC_USER" sh -c "curl -sSL https://storage.googleapis.com/flywheel-dist/fw-cli/0.29/install.sh | FW_CLI_INSTALL_DIR=$FW_CLI_DIR/ sh"
  fi

  echo "Applying Flywheel configurations..."
  
  # Disable auto-updates and compatibility checks, executing as the orthanc user
  sudo -u "$ORTHANC_USER" "$FW_CLI_DIR/fw-beta" config set disable_auto_update true
  sudo -u "$ORTHANC_USER" "$FW_CLI_DIR/fw-beta" config set disable_compatibility_check true
  
  echo "Flywheel CLI installed and configured successfully."
}

setup_python_env() {
  print_step "Setting up Python Environment"
  if [ -d "$PYTHON_ENV_DIR" ]; then
    echo "Python environment already exists. Skipping creation."
  else
    sudo -u "$ORTHANC_USER" sh -c "python3 -m venv $PYTHON_ENV_DIR"
  fi
  
  echo "Installing Python packages..."
  sudo -u "$ORTHANC_USER" sh -c "$PYTHON_ENV_DIR/bin/pip install paramiko pydicom scp flask gunicorn"
}

deploy_and_secure_files() {
  print_step "Deploying and Securing Files"
  
  echo "Creating staging directory: $EXPORT_DIR"
  install -o "$ORTHANC_USER" -g "$ORTHANC_USER" -d "$EXPORT_DIR"
  
  echo "Copying scripts and configuration files from repository..."
  # Copy files into /usr/share/orthanc
  cp "$REPO_DIR"/config/usr_share_orthanc/*.lua /usr/share/orthanc/
  cp "$REPO_DIR"/config/usr_share_orthanc/*.py /usr/share/orthanc/
  
  # Copy config files into /etc/orthanc
  cp "$REPO_DIR"/config/etc_orthanc/*.json /etc/orthanc/

  # Copy templates to their final locations for user editing
  cp "$REPO_DIR"/config/usr_share_orthanc/routing.json.template /usr/share/orthanc/routing.json
  cp "$REPO_DIR"/config/usr_share_orthanc/.fw_keychain.json.template /usr/share/orthanc/.fw_keychain.json

  echo ""
  echo "!v!v!v!v!v!v!v!v!v!v!v!v!v ACTION REQUIRED v!v!v!v!v!v!v!v!v!v!v!v!v!"
  echo "The script is now paused."
  echo "Please open a NEW terminal and manually edit the following two files"
  echo "with your site-specific details:"
  echo "  1. /usr/share/orthanc/routing.json      (Study routing)"
  echo "  2. /usr/share/orthanc/.fw_keychain.json (Flywheel API keys)"
  echo "For detailed instructions on how to edit the files via command line,"
  echo "follow the Setup Guide on the Hyperwheel GitHub repository."
  echo "Once you have saved your configurations, return here."
  echo "!^!^!^!^!^!^!^!^!^!^!^!^!^ ACTION REQUIRED ^!^!^!^!^!^!^!^!^!^!^!^!^!"
  read -p "Press [Enter] when required action is completed..."
  
  echo "Applying final permissions..."
  chown -R orthanc:orthanc /usr/share/orthanc/
  chown -R orthanc:orthanc /etc/orthanc/
  
  chmod 600 /usr/share/orthanc/.fw_keychain.json /usr/share/orthanc/export.lua /usr/share/orthanc/routing.json /usr/share/orthanc/network_config.json
  chmod 600 /etc/orthanc/orthanc.json /etc/orthanc/credentials.json
  chmod 700 /usr/share/orthanc/rrdf_sync.py
}

enforce_sudo_password() {
  print_step "Enforcing sudo Password Security"
  local sudoers_file="/etc/sudoers.d/010_pi-nopasswd"

  if [ -f "$sudoers_file" ]; then
    echo "Found $sudoers_file."
    echo "Modifying rules to require a password for sudo commands..."
    
    # Use sed to safely replace NOPASSWD with PASSWD
    sed -i 's/NOPASSWD:/PASSWD:/g' "$sudoers_file"
    
    echo "Security update applied successfully."
  else
    echo "Note: $sudoers_file not found."
    echo "Sudo password rules may already be secure or managed elsewhere on this OS."
  fi
}

setup_ipad_dashboard() {
  print_step "Configuring iPad Dashboard & Hotspot"

  # 1. Force the radio unblocked at the hardware and NetworkManager level
  rfkill unblock wifi || true
  nmcli radio wifi on || true
  
  # 2. Give the hardware 3 seconds to physically initialize
  sleep 3 
  ip link set wlan0 up || true

  # 3. Create Hotspot (Hardcoded)
  nmcli device wifi hotspot ifname wlan0 ssid Hyperwheel password '#2020Imaging' || true

  # 4. Ensure IP and Autoconnect are permanently set
  nmcli connection modify Hotspot ipv4.addresses 192.168.99.1/24 || true
  nmcli connection modify Hotspot ipv4.method shared || true
  nmcli connection modify Hotspot connection.autoconnect yes || true
  nmcli connection up Hotspot || true

  echo "Creating systemd service for the Dashboard..."
  cat <<EOF | sudo tee /etc/systemd/system/hyperwheel-dashboard.service > /dev/null
[Unit]
Description=Hyperwheel iPad Dashboard
After=network-online.target NetworkManager.service orthanc.service
Wants=network-online.target

[Service]
User=root
Group=root
WorkingDirectory=/usr/share/orthanc
ExecStartPre=-/usr/sbin/rfkill unblock wifi
ExecStartPre=-/usr/bin/nmcli connection up Hotspot
ExecStart=/var/lib/orthanc/python_env/bin/gunicorn -w 2 -b 0.0.0.0:5000 dashboard:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  echo "Enabling dashboard service..."
  systemctl daemon-reload
  systemctl enable hyperwheel-dashboard
  systemctl start hyperwheel-dashboard
  
  echo "Dashboard setup complete. Accessible at http://192.168.99.1:5000"
}

setup_chromium_bookmarks() {
  print_step "Configuring Chromium Bookmarks"

  # Retrieve the user who invoked sudo (fallback to 'pi' if empty)
  REAL_USER=${SUDO_USER:-pi}
  echo "Setting bookmarks for desktop user: $REAL_USER"

  echo "Closing Chromium safely..."
  killall chromium-browser 2>/dev/null || true
  sleep 2

  echo "Injecting custom URLs into Bookmarks..."
  
  # Execute Python as the real user, NOT root, to preserve file permissions
  sudo -u "$REAL_USER" python3 -c '
import json
import os

bookmark_path = os.path.expanduser("~/.config/chromium/Default/Bookmarks")

try:
    with open(bookmark_path, "r") as f:
        data = json.load(f)

    # Wipe existing children and add the required lab bookmarks
    data["roots"]["bookmark_bar"]["children"] = [
        {"name": "Hyperfine Login", "type": "url", "url": "https://10.42.0.1:8080/"},
        {"name": "Orthanc PACS", "type": "url", "url": "http://localhost:8042/"},
        {"name": "Upload Monitor", "type": "url", "url": "http://192.168.99.1:5000/"}
    ]

    with open(bookmark_path, "w") as f:
        json.dump(data, f, indent=4)
        
    print("Bookmarks wiped and updated successfully.")
    
except FileNotFoundError:
    print(f"Warning: {bookmark_path} not found.")
    print("Chromium must be launched at least once on this user account before bookmarks can be edited via script.")
except Exception as e:
    print(f"Error modifying bookmarks: {e}")
'
}

finalize() {
  print_step "Finalizing Installation"
  echo "Enabling and restarting the Orthanc service..."
  systemctl enable orthanc
  systemctl restart orthanc
  echo ""
  echo "------------------------------------------------------------"
  echo "Setup Complete! The pipeline is now running."
  echo "------------------------------------------------------------"
  echo ""
  echo "To monitor the system, run:"
  echo "  sudo tail -f /var/log/orthanc/Orthanc.log"
}

# --- Run Script ---
main() {
  check_root
  update_system
  install_dependencies
  setup_network
  install_flywheel_cli
  setup_python_env
  deploy_and_secure_files
  enforce_sudo_password
  setup_ipad_dashboard
  setup_chromium_bookmarks
  finalize
}

main