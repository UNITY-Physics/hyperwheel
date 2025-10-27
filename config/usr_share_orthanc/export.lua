--[[
=====================================================================
  CONFIGURATION SETTINGS
=====================================================================
--]]

-- 1. Root directory for exported DICOM files.
local data_directory = '/var/lib/orthanc/export'

-- 2. Path to the routing and keychain files mapping OperatorsName to study details.
local routing_path = '/usr/share/orthanc/routing.json'
local keychain_path = '/usr/share/orthanc/.fw_keychain.json'

-- Paths to required executables and scripts
local fw_beta = '/var/lib/orthanc/.fw/fw-beta'
local python_executable = '/var/lib/orthanc/python_env/bin/python'
local rrdf_sync_script_path = '/usr/share/orthanc/rrdf_sync.py'


--[[
=====================================================================
  HELPER FUNCTIONS
=====================================================================
--]]

function FormatSessionLabel(studyDate, studyTime)
  local dateStr = tostring(studyDate or 'UnknownStudyDate')
  local timeStr = tostring(studyTime or 'UnknownStudyTime')

  if dateStr == 'UnknownStudyDate' or timeStr == 'UnknownStudyTime' then
    return dateStr .. '_' .. timeStr
  end

  local year  = string.sub(dateStr, 1, 4)
  local month = string.sub(dateStr, 5, 6)
  local day   = string.sub(dateStr, 7, 8)

  local cleanTimeStr = string.match(timeStr, '^(%d*)')
  local hour = string.sub(cleanTimeStr, 1, 2)
  local min  = string.sub(cleanTimeStr, 3, 4)
  local sec  = string.sub(cleanTimeStr, 5, 6)

  if #year == 4 and #month == 2 and #day == 2 and #hour == 2 and #min == 2 and #sec == 2 then
    return string.format('%s-%s-%s_%s_%s_%s', year, month, day, hour, min, sec)
  else
    return dateStr .. '_' .. timeStr
  end
end

function CleanSeriesDescription(description)
  if description == nil then return 'NoDescription' end
  local cleaned = tostring(description)
  local suffix = " -NOT FOR DIAGNOSTIC USE"
  if string.sub(cleaned, -string.len(suffix)) == suffix then
    cleaned = string.sub(cleaned, 1, -string.len(suffix) - 1)
  end
  cleaned = string.gsub(cleaned, '%s+-%s+', '_')
  cleaned = string.gsub(cleaned, '%s+', '_')
  cleaned = string.gsub(cleaned, '[^%w_.-]', '')
  return cleaned
end

-- Compares two number vectors with a tolerance
function CompareVectors(vec1, vec2, tolerance)
  if not vec1 or not vec2 or #vec1 ~= #vec2 then
    return false
  end
  tolerance = tolerance or 0.01
  for i = 1, #vec1 do
    if math.abs(tonumber(vec1[i]) - tonumber(vec2[i])) > tolerance then
      return false
    end
  end
  return true
end

-- Returns an orientation label from the DICOM vector
function GetOrientationLabel(orientationVector)
  if orientationVector == nil then return '' end -- Return empty string if tag is missing

  local vec_table = {}
  -- Check if the input is a string (like "0\\1\\0...") and parse it into a table.
  if type(orientationVector) == 'string' then
    for num_str in string.gmatch(orientationVector, "([^\\\\]+)") do
      table.insert(vec_table, tonumber(num_str))
    end
  else
    -- If it's not a string, assume it's already a table
    vec_table = orientationVector
  end

  local axial    = {1, 0, 0, 0, 1, 0}
  local sagittal = {0, 1, 0, 0, 0, -1}
  local coronal  = {1, 0, 0, 0, 0, -1}

  -- Use the newly created "vec_table" for comparisons
  if CompareVectors(vec_table, axial) then
    return '_AXI'
  elseif CompareVectors(vec_table, sagittal) then
    return '_SAG'
  elseif CompareVectors(vec_table, coronal) then
    return '_COR'
  else
    return '' -- Return empty if it's an oblique or unknown orientation
  end
end

function CleanFlipAngle(fa_value)
  if fa_value == nil then return 'UnknownFlipAngle' end
  
  local num = tonumber(fa_value)
  if num then
    -- Check if the number is a whole number (e.g., 60.0, 300.00)
    if num == math.floor(num) then
      return string.format('%.0f', num) -- Format as an integer (e.g., "60")
    end
  end
  
  -- Fallback for non-numeric or non-integer values
  return tostring(fa_value)
end

function SafeNavigate(tbl, path_keys)
  local current = tbl
  for i = 1, #path_keys do
    local key = path_keys[i]
    if type(current) ~= 'table' or current[key] == nil then
      return nil
    end
    current = current[key]
  end
  return current
end

function ParseJsonFile(path) -- Parses json files
  local file = io.open(path, "r")
  if not file then
    print('Error: Could not open json file at: ' .. path)
    return nil
  end
  local content = file:read("a")
  file:close()

  return ParseJson(content)
end

function ExecuteAndLog(command)
  -- This function executes a shell command, logs its full output,
  -- and returns the output as a string for verification.
  local handle = io.popen(command .. ' 2>&1') -- Redirects stderr to stdout
  if handle then
    local output = handle:read('*a')
    handle:close()
    print(output) -- Always print the output for the log file
    return output -- Return the output for analysis
  else
    print('Error: Could not execute command: ' .. command)
    return 'Error: Command execution failed.'
  end
end


function VerifyAndCleanupStudy(local_study_path, fw_project_uri)
  print('--- Starting File Verification for ' .. local_study_path .. ' ---')

  local remoteDirCache = {}

  local find_command = string.format("find %s -type f", local_study_path)
  local find_handle = io.popen(find_command)
  if not find_handle then
    print('[ERROR] Could not execute find command. Skipping cleanup.')
    return false
  end

  local all_files_verified = true

  for local_filepath in find_handle:lines() do
    -- 1. Extract the local directory and filename from the full path
    local filename = string.match(local_filepath, "([^/]+)$")
    -- Use string.match to capture everything up to the last '/'
    local local_dir = string.match(local_filepath, "^(.*/)") 

    -- If pattern matching failed somehow, skip this file
    if not filename or not local_dir then
        print("[ERROR] Could not parse filename or directory from path: " .. local_filepath)
        all_files_verified = false
        goto continue -- Skip to the next iteration of the loop
    end

    -- 2. Check if we have the file list for this directory in our cache
    if not remoteDirCache[local_dir] then
      print('--------------------------------------------------------')
      print('[CACHE MISS] First file in this directory. Fetching remote list for directory: ' .. local_dir)

      -- Construct the relative path (ensure base path ends with '/')
      local base_path = local_study_path
      if not base_path:find('/$') then base_path = base_path .. '/' end
      
      local relative_dir = string.gsub(local_dir, base_path, '', 1) -- Replace only the first occurrence
      relative_dir = string.gsub(relative_dir, "/$", "") -- Remove trailing slash
      
      local fw_uri_to_list = fw_project_uri .. '/' .. relative_dir

      print('[FETCH] Listing files from: ' .. fw_uri_to_list)
      local fw_ls_command = string.format('%s ls "%s"', fw_beta, fw_uri_to_list)
      local fw_output = ExecuteAndLog(fw_ls_command)

      -- Create a new 'set' in the cache for this directory
      remoteDirCache[local_dir] = {}

      if fw_output then
        -- Parse the output to get just the filenames
        -- This skips the header and extracts the name part, handling spaces
        for line in fw_output:gmatch("([^\r\n]+)") do
          -- A robust pattern to find the filename at the end of the line
          -- It looks for the date (YYYY-MM-DD) and time (HH:MM) and captures everything after it.
          local remote_filename = string.match(line, "%d%d%d%d%-%d%d%-%d%d%s+%d%d:%d%d%s+(.+)")
          if remote_filename then
            remote_filename = string.gsub(remote_filename, "^%s*(.-)%s*$", "%1") -- Trim whitespace
            remote_filename = remote_filename:gsub("\r", "") -- Remove potential carriage returns
            print('[FOUND REMOTE FILE] ' .. remote_filename)
            remoteDirCache[local_dir][remote_filename] = true -- Add filename to the set
          end
        end
      else
        print('[ERROR] The fw ls command failed for directory: ' .. fw_uri_to_list)
        -- Don't mark all files as failed, just note the error and continue
        -- all_files_verified will remain false because files in this dir won't be verified
      end
    end

    -- 3. Now, perform the verification using the (now populated) cache
    print('[CHECK] Local file: ' .. filename)
    local remoteFileSet = remoteDirCache[local_dir]

    if remoteFileSet and remoteFileSet[filename] then
      print('[SUCCESS] File confirmed. Deleting local copy.')
      os.execute(string.format('rm "%s"', local_filepath))
    else
      print('[SKIP] File not found in remote directory. Local copy kept.')
      all_files_verified = false
    end
    
    ::continue::
  end
  find_handle:close()

  -- 4. Clean up empty directories
  print('--- Cleaning up empty directories... ---')
  ExecuteAndLog(string.format("find %s -type d -empty -delete", local_study_path))

  print('--- File Verification and Cleanup Finished ---')
  return all_files_verified
end


--[[
=====================================================================
  MAIN LOGIC 1 - TRIGGERED ON STORED INSTANCE
=====================================================================
--]]

function OnStoredInstance(instanceId, tags)
  -- 1. Get OperatorsName to determine the study
  StudyName = string.match(tags['OperatorsName'], ".*/(.*)")
  if not StudyName then
    print('Error: Cannot find OperatorsName (0008,1050) tag. Skipping export.')
    return
  end

  -- 2. Retrieve and clean essential DICOM tags
  local subjectLabel   = tostring(tags['PatientID'] or 'UnknownPatient')
  local seriesNumber   = tostring(tags['SeriesNumber'] or '00')
  local seriesDescription = CleanSeriesDescription(tags['SeriesDescription'])
  local sessionLabel   = FormatSessionLabel(tags['StudyDate'], tags['StudyTime'])

  -- 3. Determine FlipAngle and Orientation to append to acquisition label if series is PSIF
  local fa_part = ""
  if string.find(seriesDescription, "PSIF") then
    -- Get Orientation
    local orientationPath = {'SharedFunctionalGroupsSequence', 1, 'PlaneOrientationSequence', 1, 'ImageOrientationPatient'}
    local orientationVector = SafeNavigate(tags, orientationPath)
    local orientationLabel = GetOrientationLabel(orientationVector)
    seriesDescription = seriesDescription .. orientationLabel -- Append _AXI, _SAG, or _COR
    
    -- Get Flip Angle
    local flipAnglePath = {'SharedFunctionalGroupsSequence', 1, 'MRTimingAndRelatedParametersSequence', 1, 'FlipAngle'}
    local flipAngleValue = SafeNavigate(tags, flipAnglePath)
    local flipAngleString = CleanFlipAngle(flipAngleValue)
    if flipAngleString ~= 'UnknownFlipAngle' and flipAngleString ~= "" then
        fa_part = "_FA" .. flipAngleString
    end
  end

  -- 4. Construct the full path with the study name folder
  local acquisitionLabel = seriesNumber .. '_' .. seriesDescription .. fa_part
  StudyPath              = data_directory .. '/' .. StudyName
  local subjectPath      = StudyPath .. '/' .. subjectLabel
  local sessionPath      = subjectPath .. '/' .. sessionLabel
  local acquisitionPath  = sessionPath .. '/' .. acquisitionLabel

  -- 5. Determine the final file path based on series type
  local finalFilepath
  if string.find(seriesDescription, "CALIPR") then
    local filepath1 = acquisitionPath .. '/' .. acquisitionLabel .. '_1.dcm'
    local filepath2 = acquisitionPath .. '/' .. acquisitionLabel .. '_2.dcm'
    local f1 = io.open(filepath1, 'rb')
    if f1 == nil then
      finalFilepath = filepath1
    else
      io.close(f1)
      finalFilepath = filepath2
    end
  else
    local filename = acquisitionLabel .. '.dcm'
    finalFilepath = acquisitionPath .. '/' .. filename
  end

  -- 6. Create the full directory path if it doesn't exist
  local mkdirCommand = 'mkdir -p "' .. acquisitionPath .. '"'
  os.execute(mkdirCommand)

  -- 7. Skip if this specific file already exists
  local f = io.open(finalFilepath, 'rb')
  if f ~= nil then
    io.close(f)
    print('File already exists, skipping: ' .. finalFilepath)
    return
  end

  -- 8. Fetch DICOM file content from Orthanc and write to disk
  local dicomData = RestApiGet('/instances/' .. instanceId .. '/file')
  if dicomData == nil then
    print('Error: Failed to fetch DICOM data for instance ' .. instanceId)
    return
  end

  local outFile, err_open = io.open(finalFilepath, 'wb')
  if not outFile then
      print('Error: Failed to open file for writing: ' .. finalFilepath .. '. Error: ' .. tostring(err_open))
      return
  end
  outFile:write(dicomData)
  outFile:close()

  print('Exported instance ' .. instanceId .. ' to ' .. finalFilepath)
end


--[[
=====================================================================
  MAIN LOGIC 2 - TRIGGERED ON STABLE STUDY
=====================================================================
--]]

function OnStableStudy(studyId, tags, metadata)
  print('A study has become stable. Beginning upload process for StudyID: ' .. studyId)

  -- 1. Check again if OperatorsName exists. Can be nil in some cases.
  if not StudyName then
    print('[CLEANUP] OperatorsName (0008,1050) tag is empty. Deleting study from Orthanc internal storage (ID: ' .. studyId .. ')')
    RestApiDelete('/studies/' .. studyId)
    print('[CLEANUP] Study has been deleted.')
    return
  end

  print('Processing data for study: ' .. string.upper(StudyName))

  -- 2. Load routing rules and API keys from files
  local routes = ParseJsonFile(routing_path)
  local api_keys = ParseJsonFile(keychain_path)

  if not routes or not api_keys then
    error('Could not load routing or keychain files. Halting.')
    return
  end

  -- 3. Find the matching route for the current OperatorsName
  local fw_project_uri = routes[StudyName]

  -- Handle unroutable studies
  if not fw_project_uri then
    print('[CLEANUP] No routing rule found for study: ' .. string.upper(StudyName))
    print('[CLEANUP] Deleting from local export directory: ' .. StudyPath)
    os.execute(string.format('rm -rf "%s"', StudyPath))
    
    print('[CLEANUP] Deleting study from Orthanc internal storage (ID: ' .. studyId .. ')')
    RestApiDelete('/studies/' .. studyId)
    
    print('[CLEANUP] Unroutable study has been deleted.')
    return -- Stop further processing for this study
  end

  -- 4. Get the specific destination and API key from the matched rule
  local fw_api_key = api_keys[StudyName]

  if not fw_api_key then
    error('Found route, but could not find API key name for the ' .. string.upper(StudyName) .. ' study in keychain. Halting.')
    return
  end

  print('Destination found: ' .. fw_project_uri)

  -- 5. RRDF synchronization
  print('Executing RRDF synchronization script...')
  local rrdf_command = python_executable .. ' ' .. rrdf_sync_script_path
  ExecuteAndLog(rrdf_command)
  print('RRDF sync finished.')

  -- 6. Login and Import using the dynamic values
  print('Logging into Flywheel...')
  local login_command = 'FW_CLI_API_KEY=' .. fw_api_key .. ' ' .. fw_beta .. ' login'
  local login_output = ExecuteAndLog(login_command) -- Capture output for verification

  if not string.find(login_output, "Logged in to") then
    error('Flywheel login FAILED. Please check API key and network connection. Aborting sync.')
    return -- Stop execution
  end
  print('Flywheel login successful.')

  local import_command = string.format(
      '%s import run --project "%s" --storage "%s" --exclude "path=~.DS_Store" --tree --wait',
      fw_beta,
      fw_project_uri,
      StudyPath
  )
  print('Executing Flywheel Import: ' .. import_command)
  ExecuteAndLog(import_command)
  print('Import command finished.')

  -- 7. Verify and Cleanup individual local files in the local /export folder.
  local cleanup_successful = VerifyAndCleanupStudy(StudyPath, fw_project_uri)

  -- 8. Delete from Orthanc's internal database ONLY IF the export folder cleanup was a complete success.
  if cleanup_successful then
    print('[SUCCESS] All local files verified and cleaned from export folder.')
    print('[CLEANUP] Deleting study from Orthanc internal storage (ID: ' .. studyId .. ')')
    RestApiDelete('/studies/' .. studyId)
  else
    print('[WARNING] Not all files could be verified on Flywheel. The original study will be kept in Orthanc storage for safety.')
  end

  ExecuteAndLog(fw_beta .. ' logout')

  print('Flywheel sync process finished for: ' .. string.upper(StudyName))
end