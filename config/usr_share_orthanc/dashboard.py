from flask import Flask, jsonify, render_template_string
import subprocess
import os
import re

app = Flask(__name__)

LOG_FILE = "/var/log/orthanc/Orthanc.log"
EXPORT_DIR = "/var/lib/orthanc/export/"

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Hyperwheel Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
    <style>
        :root {
            --bg-color: #F2F2F7;       
            --card-color: #FFFFFF;     
            --text-main: #000000;      
            --text-sub: #6E6E73;       
            --text-dim: #C7C7CC;       
            --accent-blue: #007AFF;    
            --accent-green: #34C759;
            --accent-red: #FF3B30;
            --accent-purple: #AF52DE;
            --border-color: #E5E5EA;   
        }
        
        * { box-sizing: border-box; }
        body { background-color: var(--bg-color); color: var(--text-main); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 24px; }
        .container { width: 100%; max-width: 1400px; margin: 0 auto; }
        
        .header-title { font-size: 14px; font-weight: 400; color: var(--text-sub); margin-bottom: 8px; margin-left: 16px; text-transform: uppercase; letter-spacing: -0.2px; }
        .card { background-color: var(--card-color); border-radius: 12px; padding: 32px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .status-header { font-size: 32px; font-weight: 600; margin-bottom: 6px; letter-spacing: -0.5px; }
        .status-subtext { color: var(--text-sub); font-size: 17px; margin-bottom: 28px; font-weight: 400; }
        
        .step { display: flex; align-items: flex-start; margin-bottom: 24px; font-size: 18px; transition: color 0.3s ease; }
        .step-icon { width: 28px; height: 28px; border-radius: 50%; border: 2px solid; display: flex; align-items: center; justify-content: center; margin-right: 18px; font-weight: bold; font-size: 15px; flex-shrink: 0; transition: all 0.3s ease; margin-top: -3px; }
        .step-content { display: flex; flex-direction: column; }
        .step-subtext { font-size: 15px; color: var(--text-sub); margin-top: 6px; display: none; }
        
        .pending { color: var(--text-dim); }
        .pending .step-icon { border-color: var(--text-dim); }
        .active { color: var(--text-main); font-weight: 500; }
        .active .step-icon { border-color: var(--accent-blue); color: var(--accent-blue); animation: pulse 2s infinite; }
        .completed { color: var(--text-main); }
        .completed .step-icon { border-color: var(--accent-green); background-color: var(--accent-green); color: #FFFFFF; border: none; width: 32px; height: 32px; margin-left: -2px; margin-right: 16px;}
        .error { color: var(--accent-red); font-weight: 500; }
        .error .step-icon { border-color: var(--accent-red); background-color: rgba(255, 59, 48, 0.1); color: var(--accent-red); }

        /* --- CLINICAL GRADE STAGING AREA --- */
        #staging-container { width: 100%; }
        
        .staging-project-title { font-size: 15px; font-weight: 600; color: var(--text-sub); margin-bottom: 16px; letter-spacing: 0.5px; border-bottom: 1px solid var(--border-color); padding-bottom: 8px; text-transform: uppercase; }
        
        .staging-group { margin-bottom: 24px; }
        .staging-group:last-child { margin-bottom: 0; }
        
        .staging-group-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 8px; padding: 0 4px; }
        .header-patient { font-size: 17px; font-weight: 600; color: var(--text-main); letter-spacing: -0.3px; }
        
        /* Removed text-transform: uppercase here so "at" stays lowercase */
        .header-session { font-size: 14px; font-weight: 500; color: var(--text-sub); letter-spacing: 0.2px; padding-bottom: 2px; } 
        
        /* Changed from shadow to a clean flat inset border to look good inside the big white card */
        .staging-card { background-color: #FAFAFC; border-radius: 8px; border: 1px solid var(--border-color); overflow: hidden; }
        
        .staging-sequence { padding: 12px 16px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; }
        .staging-sequence:last-child { border-bottom: none; }
        
        .seq-name { font-size: 16px; font-weight: 500; color: var(--text-main); }
        
        .seq-tags { display: flex; gap: 8px; flex-shrink: 0; }
        .tag { padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; }
        .tag-dicom { background: rgba(0, 122, 255, 0.1); color: var(--accent-blue); }
        .tag-rrdf { background: rgba(175, 82, 222, 0.1); color: var(--accent-purple); }

        .adv-btn { background: var(--card-color); color: var(--accent-blue); border: 1px solid var(--border-color); padding: 12px 20px; border-radius: 10px; font-size: 16px; font-weight: 500; cursor: pointer; transition: all 0.2s; display: inline-block; margin-top: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02); }
        .adv-btn:hover { background: #FAFAFA; }
        .adv-btn:active { opacity: 0.6; transform: scale(0.98); }
        
        #log-container { display: none; margin-top: 20px; width: 100%; }
        pre { background: #1C1C1E; color: #32D74B; padding: 20px; border-radius: 12px; overflow-y: scroll; height: 350px; font-size: 13px; font-family: "SF Mono", Menlo, Consolas, monospace; white-space: pre-wrap; word-wrap: break-word; width: 100%; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-title">Hyperwheel Pipeline</div>
        <div class="card">
            <div id="main-status" class="status-header">System Ready</div>
            <div id="sub-status" class="status-subtext">Waiting for scanner data...</div>

            <div id="checklist">
                <div id="step-receive" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Receiving DICOM Data</div><div class="step-subtext" id="subtext-receive">Waiting for study to become stable...</div></div></div>
                <div id="step-sync" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Fetching Raw Data (RRDF)</div></div></div>
                <div id="step-upload" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Uploading to Flywheel</div></div></div>
                <div id="step-verify" class="step pending"><div class="step-icon"></div><div class="step-content"><div class="step-text">Verifying Flywheel Upload</div></div></div>
            </div>
        </div>
        
        <div id="staging-container"></div>

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
            else if (state === 'error') iconEl.innerHTML = '✖';
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
                    const recSubtext = document.getElementById('subtext-receive');

                    ['receive', 'sync', 'upload', 'verify'].forEach(s => setStepState(s, 'pending'));
                    recSubtext.style.display = 'none';

                    if (data.state === 'error') {
                        header.innerText = 'System Error';
                        header.style.color = 'var(--accent-red)';
                        subtext.style.display = 'block';
                        subtext.style.color = 'var(--accent-red)';
                        subtext.innerText = data.error_msg || 'Please contact the data manager or the Hyperwheel GitHub author.';
                        
                        let errPhase = data.failed_phase;
                        if (errPhase === 'uploading') {
                            setStepState('receive', 'completed');
                            setStepState('sync', 'completed');
                            setStepState('upload', 'error');
                        } else if (errPhase === 'syncing') {
                            setStepState('receive', 'completed');
                            setStepState('sync', 'error');
                        } else if (errPhase === 'verifying') {
                            setStepState('receive', 'completed');
                            setStepState('sync', 'completed');
                            setStepState('upload', 'completed');
                            setStepState('verify', 'error');
                        } else {
                            setStepState('receive', 'error');
                        }
                    }
                    else if (data.state === 'idle') {
                        header.innerText = 'System Ready';
                        header.style.color = 'var(--text-main)';
                        subtext.style.display = 'block';
                        subtext.style.color = 'var(--text-sub)';
                        subtext.innerText = 'Waiting for scanner data...';
                    }
                    else if (data.state === 'success') {
                        header.innerText = 'Upload Complete';
                        header.style.color = 'var(--accent-green)';
                        subtext.style.display = 'none';
                        ['receive', 'sync', 'upload', 'verify'].forEach(s => setStepState(s, 'completed'));
                    }
                    else {
                        header.innerText = 'Processing Data';
                        header.style.color = 'var(--accent-blue)';
                        subtext.style.display = 'none';
                        
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
                    }

                    // --- RENDER REFINED STAGING AREA IN A NEW CARD ---
                    let stagingHtml = '';
                    if (data.staging && Object.keys(data.staging).length > 0) {
                        
                        // Start the new Card specifically for pending files
                        stagingHtml += `
                        <div class="card">
                            <div class="status-header" style="font-size: 26px; margin-bottom: 24px; letter-spacing: -0.4px; color: var(--text-main);">Files Pending Flywheel Upload</div>`;
                        
                        for (const [project, patients] of Object.entries(data.staging)) {
                            stagingHtml += `<div class="staging-project-title">${project}</div>`;
                            
                            for (const [patient, sessions] of Object.entries(patients)) {
                                for (const [session, sequences] of Object.entries(sessions)) {
                                    
                                    let sessionLabel = session;
                                    let match = session.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})_(\d{2})/);
                                    if (match) {
                                        // Properly formatted with a lowercase "at"
                                        sessionLabel = `${match[3]}/${match[2]}/${match[1]} at ${match[4]}:${match[5]}`;
                                    }

                                    stagingHtml += `
                                    <div class="staging-group">
                                        <div class="staging-group-header">
                                            <span class="header-patient">${patient}</span>
                                            <span class="header-session">${sessionLabel}</span>
                                        </div>
                                        <div class="staging-card">`;
                                    
                                    for (const [seq, counts] of Object.entries(sequences)) {
                                        if (counts.dicom === 0 && counts.rrdf === 0) continue;
                                        
                                        let tags = '';
                                        if (counts.dicom > 0) tags += `<span class="tag tag-dicom">${counts.dicom} DICOM</span>`;
                                        if (counts.rrdf > 0) tags += `<span class="tag tag-rrdf">${counts.rrdf} RRDF</span>`;

                                        let cleanSeq = seq.replace(/^\d+_/, ''); 
                                        cleanSeq = cleanSeq.replace(/_/g, ' '); 
                                        cleanSeq = cleanSeq.charAt(0).toUpperCase() + cleanSeq.slice(1);

                                        stagingHtml += `
                                            <div class="staging-sequence">
                                                <div class="seq-name">${cleanSeq}</div>
                                                <div class="seq-tags">${tags}</div>
                                            </div>
                                        `;
                                    }
                                    stagingHtml += `</div></div>`;
                                }
                            }
                        }
                        
                        // Close the new card
                        stagingHtml += `</div>`;
                    }
                    
                    const stagingContainer = document.getElementById('staging-container');
                    if (stagingContainer.innerHTML !== stagingHtml) {
                        stagingContainer.innerHTML = stagingHtml;
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
    return render_template_string(HTML_PAGE)

@app.route('/api/status')
def get_status():
    logs_raw = "Could not read logs."
    try:
        logs_raw = subprocess.check_output(['tail', '-n', '600', LOG_FILE]).decode('utf-8')
    except Exception:
        pass

    dicom_count = 0
    rrdf_count = 0
    staging_tree = {}

    try:
        if os.path.exists(EXPORT_DIR):
            for root, dirs, files in os.walk(EXPORT_DIR):
                if '.fw' in root: continue
                
                valid_files = [f for f in files if not f.startswith('.')]
                
                dcms = sum(1 for f in valid_files if f.endswith('.dcm'))
                h5s = sum(1 for f in valid_files if f.endswith('.h5'))
                
                dicom_count += dcms
                rrdf_count += h5s
                
                if len(valid_files) > 0:
                    rel_path = os.path.relpath(root, EXPORT_DIR)
                    if rel_path == ".":
                        proj, pat, session, seq = "ROOT", "Unknown Patient", "Unknown Session", "Base Directory"
                    else:
                        parts = rel_path.split(os.sep)
                        proj = parts[0].upper()
                        pat = parts[1] if len(parts) > 1 else "Unknown Patient"
                        session = parts[2] if len(parts) > 2 else "Unknown Session"
                        seq = " / ".join(parts[3:]) if len(parts) > 3 else "Main"

                    if proj not in staging_tree: staging_tree[proj] = {}
                    if pat not in staging_tree[proj]: staging_tree[proj][pat] = {}
                    if session not in staging_tree[proj][pat]: staging_tree[proj][pat][session] = {}
                    if seq not in staging_tree[proj][pat][session]:
                        staging_tree[proj][pat][session][seq] = {"dicom": 0, "rrdf": 0}
                    
                    staging_tree[proj][pat][session][seq]["dicom"] += dcms
                    staging_tree[proj][pat][session][seq]["rrdf"] += h5s
    except Exception:
        pass

    lines = logs_raw.replace('\r', '\n').splitlines()
    
    export_state = "idle"
    success_idx = -1
    receive_idx = -1
    has_error = False
    error_msg = ""

    for i, line in enumerate(lines):
        if "A study has become stable" in line:
            export_state = "queued"
            has_error = False
            error_msg = ""
        elif "Executing RRDF" in line or "Starting RRDF" in line or "RRDF sync finished" in line:
            if not has_error: export_state = "syncing"
        elif "Processing Project:" in line or "Logging into Flywheel" in line or "Executing Flywheel Import" in line or "Import command finished" in line:
            if not has_error: export_state = "uploading"
        elif "Starting File Verification" in line or "[CHECK] Local file" in line:
            if not has_error: export_state = "verifying"
        elif "[SUCCESS] All local files verified" in line or "[CLEANUP] Deleting study" in line:
            export_state = "success"
            success_idx = i
            has_error = False
            
        if "Exported instance" in line:
            receive_idx = i

        if "FAILED" in line or "ERROR" in line or (line.startswith("E") and "Lua" in line):
            has_error = True
            if "Lua says:" in line:
                error_msg = line.split("Lua says:")[-1].strip()
            elif error_msg == "":
                error_msg = "Please contact the data manager or the Hyperwheel GitHub author."

    is_receiving = receive_idx > success_idx

    state = "idle"
    if has_error:
        state = "error"
    elif (dicom_count + rrdf_count) > 0:
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
        "failed_phase": export_state,
        "error_msg": error_msg,
        "is_receiving": is_receiving,
        "staging": staging_tree,
        "logs": logs_raw
    })

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)