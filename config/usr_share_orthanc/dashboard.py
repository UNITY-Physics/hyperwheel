from flask import Flask, jsonify, render_template_string
import subprocess
import os
import re

app = Flask(__name__)

# System configurations and paths
LOG_FILE = "/var/log/orthanc/Orthanc.log"
EXPORT_DIR = "/var/lib/orthanc/export/"

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Hyperwheel Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
    <style>
        /* Light theme variables */
        :root {
            --bg-color: #F2F2F7;       
            --card-color: #FFFFFF;     
            --text-main: #000000;      
            --text-sub: #6E6E73;       
            --text-dim: #C7C7CC;       
            --accent-blue: #007AFF;    
            --accent-green: #34C759;   
            --border-color: #E5E5EA;   
        }
        
        * { box-sizing: border-box; }

        body { background-color: var(--bg-color); color: var(--text-main); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 24px; }
        
        /* Layout containers */
        .container { width: 100%; max-width: 1400px; margin: 0 auto; }
        .header-title { font-size: 14px; font-weight: 400; color: var(--text-sub); margin-bottom: 8px; margin-left: 16px; text-transform: uppercase; letter-spacing: -0.2px; }
        
        /* Main status card */
        .card { background-color: var(--card-color); border-radius: 12px; padding: 32px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .status-header { font-size: 32px; font-weight: 600; margin-bottom: 6px; letter-spacing: -0.5px; }
        .status-subtext { color: var(--text-sub); font-size: 17px; margin-bottom: 28px; font-weight: 400; }
        
        /* Metadata display block */
        .metadata-box { background: var(--bg-color); border-radius: 10px; padding: 20px; margin-top: 24px; margin-bottom: 28px; border: 1px solid var(--border-color); }
        .meta-row { display: flex; margin-bottom: 12px; font-size: 16px; }
        .meta-row:last-child { margin-bottom: 0; }
        .meta-label { color: var(--text-sub); width: 140px; flex-shrink: 0; }
        .meta-value { color: var(--text-main); font-weight: 500; }

        /* Pipeline step indicators */
        .step { display: flex; align-items: flex-start; margin-bottom: 24px; font-size: 18px; transition: color 0.3s ease; }
        .step-icon { width: 28px; height: 28px; border-radius: 50%; border: 2px solid; display: flex; align-items: center; justify-content: center; margin-right: 18px; font-weight: bold; font-size: 15px; flex-shrink: 0; transition: all 0.3s ease; margin-top: -3px; }
        .step-content { display: flex; flex-direction: column; }
        .step-subtext { font-size: 15px; color: var(--text-sub); margin-top: 6px; display: none; }
        
        /* Step states */
        .pending { color: var(--text-dim); }
        .pending .step-icon { border-color: var(--text-dim); }
        .active { color: var(--text-main); font-weight: 500; }
        .active .step-icon { border-color: var(--accent-blue); color: var(--accent-blue); animation: pulse 2s infinite; }
        .completed { color: var(--text-main); }
        .completed .step-icon { border-color: var(--accent-green); background-color: var(--accent-green); color: #FFFFFF; border: none; width: 32px; height: 32px; margin-left: -2px; margin-right: 16px;}

        /* Diagnostics toggle button */
        .adv-btn { 
            background: var(--card-color); 
            color: var(--accent-blue); 
            border: 1px solid var(--border-color); 
            padding: 12px 20px; 
            border-radius: 10px; 
            font-size: 16px; 
            font-weight: 500;
            cursor: pointer; 
            transition: all 0.2s; 
            display: inline-block; 
            margin-top: 10px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.02);
            width: auto;
        }
        .adv-btn:hover { background: #FAFAFA; }
        .adv-btn:active { opacity: 0.6; transform: scale(0.98); }
        
        /* Raw log viewer */
        #log-container { display: none; margin-top: 20px; width: 100%; }
        pre { background: #1C1C1E; color: #32D74B; padding: 20px; border-radius: 12px; overflow-y: scroll; height: 350px; font-size: 14px; font-family: "SF Mono", Menlo, Consolas, monospace; white-space: pre-wrap; word-wrap: break-word; width: 100%; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-title">Hyperwheel Pipeline</div>
        <div class="card">
            <div id="main-status" class="status-header">System Ready</div>
            <div id="sub-status" class="status-subtext">Waiting for scanner data...</div>
            
            <div id="metadata-box" class="metadata-box" style="display: none;">
                <div class="meta-row"><div class="meta-label">Project:</div><div id="meta-project" class="meta-value">Unknown</div></div>
                <div class="meta-row"><div class="meta-label">Start time:</div><div id="meta-time" class="meta-value">Unknown</div></div>
                <div class="meta-row" id="meta-dicom-row"><div class="meta-label">DICOM files:</div><div id="meta-dicom" class="meta-value">0</div></div>
                <div class="meta-row" id="meta-rrdf-row"><div class="meta-label">RRDF files:</div><div id="meta-rrdf" class="meta-value">...</div></div>
            </div>

            <div id="checklist">
                <div id="step-receive" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Receiving DICOM Data</div><div class="step-subtext" id="subtext-receive">Waiting for study to become stable...</div></div></div>
                <div id="step-sync" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Fetching Raw Data (RRDF)</div></div></div>
                <div id="step-upload" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Uploading to Flywheel</div></div></div>
                <div id="step-verify" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Verifying Flywheel Upload</div></div></div>
            </div>
        </div>
        <button class="adv-btn" onclick="toggleLogs()">Show Advanced Diagnostics</button>
        <div id="log-container"><pre id="raw-logs">Loading system logs...</pre></div>
    </div>

    <script>
        let autoScrollLogs = true;

        function toggleLogs() {
            const logDiv = document.getElementById('log-container');
            const btn = document.querySelector('.adv-btn');
            if (logDiv.style.display === 'none' || logDiv.style.display === '') {
                logDiv.style.display = 'block';
                btn.innerText = 'Hide Advanced Diagnostics';
                autoScrollLogs = true; 
            } else {
                logDiv.style.display = 'none';
                btn.innerText = 'Show Advanced Diagnostics';
            }
        }

        document.getElementById('raw-logs').addEventListener('scroll', function(e) {
            autoScrollLogs = e.target.scrollHeight - e.target.scrollTop === e.target.clientHeight;
        });

        function setStepState(stepId, state) {
            const stepEl = document.getElementById('step-' + stepId);
            const iconEl = stepEl.querySelector('.step-icon');
            stepEl.className = 'step ' + state;
            if (state === 'active') iconEl.innerHTML = '●';
            else if (state === 'completed') iconEl.innerHTML = '✓';
            else iconEl.innerHTML = '';
        }

        function updateUI() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    const logBox = document.getElementById('raw-logs');
                    logBox.innerText = data.logs;
                    if (autoScrollLogs) logBox.scrollTop = logBox.scrollHeight;

                    const header = document.getElementById('main-status');
                    const subtext = document.getElementById('sub-status');
                    const metaBox = document.getElementById('metadata-box');
                    const recSubtext = document.getElementById('subtext-receive');

                    ['receive', 'sync', 'upload', 'verify'].forEach(s => setStepState(s, 'pending'));
                    recSubtext.style.display = 'none';

                    if (data.state === 'idle') {
                        header.innerText = 'System Ready';
                        header.style.color = 'var(--text-main)';
                        subtext.style.display = 'block';
                        subtext.innerText = 'Waiting for scanner data...';
                        metaBox.style.display = 'none';
                        return;
                    }

                    metaBox.style.display = 'block';
                    subtext.style.display = 'none';
                    document.getElementById('meta-project').innerText = data.project;
                    document.getElementById('meta-time').innerText = data.session;

                    if (data.state === 'success') {
                        header.innerText = 'Upload Complete';
                        header.style.color = 'var(--accent-green)';
                        document.getElementById('meta-dicom-row').style.display = 'none';
                        document.getElementById('meta-rrdf-row').style.display = 'none';
                        ['receive', 'sync', 'upload', 'verify'].forEach(s => setStepState(s, 'completed'));
                        return;
                    }

                    header.innerText = 'Processing Data';
                    header.style.color = 'var(--accent-blue)';
                    
                    document.getElementById('meta-dicom-row').style.display = 'flex';
                    document.getElementById('meta-rrdf-row').style.display = 'flex';
                    document.getElementById('meta-dicom').innerText = data.dicoms;
                    document.getElementById('meta-rrdf').innerText = data.rrdfs === 0 ? '...' : data.rrdfs;

                    if (data.state === 'receiving') {
                        setStepState('receive', 'active');
                        recSubtext.style.display = 'block'; 
                    } else if (data.state === 'queued') {
                        setStepState('receive', 'completed');
                    } else if (data.state === 'syncing') {
                        setStepState('receive', 'completed');
                        setStepState('sync', 'active');
                    } else if (data.state === 'uploading') {
                        setStepState('receive', 'completed');
                        setStepState('sync', 'completed');
                        setStepState('upload', 'active');
                    } else if (data.state === 'verifying') {
                        setStepState('receive', 'completed');
                        setStepState('sync', 'completed');
                        setStepState('upload', 'completed');
                        setStepState('verify', 'active');
                    }
                })
                .catch(err => console.error(err));
        }
        setInterval(updateUI, 2000);
        updateUI();
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    """Serve the primary HTML interface."""
    return render_template_string(HTML_PAGE)

@app.route('/api/status')
def get_status():
    """
    Parse system logs and directory structures to determine the 
    current operational state of the DICOM/RRDF processing pipeline.
    """
    logs_raw = "Could not read logs."
    try:
        # Retrieve recent logs to ensure CLI progress bars do not truncate the operational history
        logs_raw = subprocess.check_output(['tail', '-n', '600', LOG_FILE]).decode('utf-8')
    except Exception:
        pass

    dicom_count = 0
    rrdf_count = 0
    project_name = "Unknown"
    session_raw = ""

    # Parse staging directory for active files and study metadata
    try:
        if os.path.exists(EXPORT_DIR):
            for root, dirs, files in os.walk(EXPORT_DIR):
                if '.fw' in root: continue
                
                valid_files = [f for f in files if not f.startswith('.')]
                
                for f in valid_files:
                    if f.endswith('.dcm'): dicom_count += 1
                    if f.endswith('.h5'): rrdf_count += 1
                
                if len(valid_files) > 0:
                    rel_path = os.path.relpath(root, EXPORT_DIR)
                    parts = rel_path.split(os.sep)
                    
                    if len(parts) >= 3 and project_name == "Unknown":
                        project_name = parts[0].upper()
                    
                    # Extract session timestamp using regex
                    if session_raw == "":
                        for p in parts:
                            if re.match(r'\d{4}-\d{2}-\d{2}_\d{2}_\d{2}_\d{2}', p):
                                session_raw = p
    except Exception:
        pass

    session_formatted = "Unknown"
    if session_raw:
        try:
            d_part, t_part = session_raw.split('_', 1)
            t_formatted = t_part.replace('_', ':')
            y, m, d = d_part.split('-')
            session_formatted = f"{t_formatted} {d}-{m}-{y}"
        except:
            session_formatted = session_raw

    # Sanitize carriage returns generated by CLI progress bars
    lines = logs_raw.replace('\r', '\n').splitlines()
    
    export_state = "idle"
    success_idx = -1
    receive_idx = -1

    # Chronologically parse log events to determine the active pipeline phase
    for i, line in enumerate(lines):
        if "A study has become stable" in line:
            export_state = "queued"
        elif "Executing RRDF" in line or "Starting RRDF" in line or "RRDF sync finished" in line:
            export_state = "syncing"
        elif "Executing Flywheel Import" in line or "Import command finished" in line:
            export_state = "uploading"
        elif "Starting File Verification" in line or "[CHECK] Local file" in line:
            export_state = "verifying"
        elif "[SUCCESS] All local files verified" in line or "[CLEANUP] Deleting study" in line:
            export_state = "success"
            success_idx = i
            
        if "Exported instance" in line:
            receive_idx = i

    # Verify if a new transmission sequence has begun after a prior success
    is_receiving = receive_idx > success_idx

    # Recover study metadata from logs if the local staging directory has already been cleaned
    if (dicom_count + rrdf_count) == 0 and export_state == "success":
        for line in reversed(lines):
            if "Storage /var/lib/orthanc/export/" in line and project_name == "Unknown":
                project_name = line.split("export/")[-1].strip().split('/')[0].upper()
            if "Found session:" in line and session_formatted == "Unknown":
                s_raw = line.split("Found session:")[-1].split()[0].strip()
                try:
                    d_part, t_part = s_raw.split('_', 1)
                    t_formatted = t_part.replace('_', ':')
                    y, m, d = d_part.split('-')
                    session_formatted = f"{t_formatted} {d}-{m}-{y}"
                except:
                    session_formatted = s_raw

    # Resolve final application state
    state = "idle"
    if (dicom_count + rrdf_count) > 0:
        if export_state in ["syncing", "uploading", "verifying"]:
            state = export_state
        else:
            state = "queued" 
    else:
        if is_receiving:
            state = "receiving" 
        elif export_state == "success":
            state = "success" 
        else:
            state = "idle" 

    return jsonify({
        "state": state,
        "project": project_name,
        "session": session_formatted,
        "dicoms": dicom_count,
        "rrdfs": rrdf_count,
        "logs": logs_raw
    })

# Disable client-side caching to guarantee real-time UI updates
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)