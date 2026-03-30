--[[
=====================================================================
  CONFIGURATION SETTINGS
=====================================================================
--]]

-- 1. Root directory for exported DICOM and RRDF files.
local data_directory = '/var/lib/orthanc/export'

-- 2. Configuration files mapping the OperatorsName to Flywheel projects and API keys.
local routing_path = '/usr/share/orthanc/routing.json'
local keychain_path = '/usr/share/orthanc/.fw_keychain.json'

-- 3. Paths to required executables and helper scripts.
local fw_beta = '/var/lib/orthanc/.fw/fw-beta'
local python_executable = '/var/lib/orthanc/python_env/bin/python'
local rrdf_sync_script_path = '/usr/share/orthanc/rrdf_sync.py'


--[[
=====================================================================
  HELPER FUNCTIONS
=====================================================================
--]]

-- Formats DICOM date and time into a clean YYYY-MM-DD_HH_MM_SS string.
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

-- Sanitizes the SeriesDescription tag for safe folder/file naming.
function CleanSeriesDescription(description)
  if description == nil then return 'NoDescription' end
  local cleaned = tostring(description)
  local suffix = " -NOT FOR DIAGNOSTIC USE"
  if string.sub(cleaned, -string.len(suffix)) == suffix then
    cleaned = string.sub(cleaned, 1, -string.len(suffix) - 1)
  end
  cleaned = string.gsub(cleaned, '%s+-%s+', '_')
  cleaned = string.gsub(cleaned, '%s+', '_')
  cleaned = string.gsub(cleaned, '[^%w_-]', '')
  return cleaned
end

-- Helper to compare DICOM orientation vectors with a slight margin for float errors.
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

-- Determines standard anatomical plane (AXI, SAG, COR) from the DICOM ImageOrientationPatient vector.
function GetOrientationLabel(orientationVector)
  if orientationVector == nil then return '' end

  local vec_table = {}
  if type(orientationVector) == 'string' then
    for num_str in string.gmatch(orientationVector, "([^\\\\]+)") do
      table.insert(vec_table, tonumber(num_str))
    end
  else
    vec_table = orientationVector
  end

  local axial    = {1, 0, 0, 0, 1, 0}
  local sagittal = {0, 1, 0, 0, 0, -1}
  local coronal  = {1, 0, 0, 0, 0, -1}

  if CompareVectors(vec_table, axial) then
    return '_AXI'
  elseif CompareVectors(vec_table, sagittal) then
    return '_SAG'
  elseif CompareVectors(vec_table, coronal) then
    return '_COR'
  else
    return ''
  end
end

-- Cleans the FlipAngle value (e.g., formatting 60.0 as just "60").
function CleanFlipAngle(fa_value)
  if fa_value == nil then return 'UnknownFlipAngle' end
  local num = tonumber(fa_value)
  if num then
    if num == math.floor(num) then
      return string.format('%.0f', num)
    end
  end
  return tostring(fa_value)
end

-- Safely traverses nested DICOM Sequence structures without throwing nil errors.
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

-- Reads and parses a JSON file into a Lua table.
function ParseJsonFile(path)
  local file = io.open(path, "r")
  if not file then
    print('Error: Could not open json file at: ' .. path)
    return nil
  end
  local content = file:read("a")
  file:close()
  return ParseJson(content)
end

  -- This function executes a shell command, logs its full output,
  -- and returns the output as a string for verification.
  function ExecuteAndLog(command)
  local handle = io.popen(command .. ' 2>&1')
  if handle then
    local output = handle:read('*a')
    handle:close()
    print(output)
    return output
  else
    print('Error: Could not execute command: ' .. command)
    return 'Error: Command execution failed.'
  end
end

-- Queries Flywheel to ensure files actually uploaded successfully. 
-- Instantly deletes verified local files and uses .id sidecars to clear Orthanc's internal DB.
function VerifyAndCleanupStudy(local_study_path, fw_project_uri, projectPath)
  print('--- Starting File Verification for ' .. local_study_path .. ' ---')

  local remoteDirCache = {}
  local find_command = string.format("find %s -type f", local_study_path)
  local find_handle = io.popen(find_command)
  if not find_handle then return false end

  local all_files_verified = true

  for local_filepath in find_handle:lines() do
    -- 1. Extract the local directory and filename from the full path
    local filename = string.match(local_filepath, "([^/]+)$")
    local local_dir = string.match(local_filepath, "^(.*/)") 

    if not filename or not local_dir then goto continue end

    -- Only verify actual payload files (DICOM and RRDF). Ignore our .id sidecars or OS hidden files.
    if not (string.match(filename, "%.dcm$") or string.match(filename, "%.h5$")) then
        goto continue
    end

    -- If we haven't fetched the Flywheel file list for this specific folder yet, do it now.
    if not remoteDirCache[local_dir] then
      print('--------------------------------------------------------')
      print('[CACHE MISS] Fetching remote list for directory: ' .. local_dir)

      -- Construct the remote URI for the parent directory
      -- 1. Extract the part of local_dir that comes after projPath
      local sub_path = string.sub(local_dir, string.len(projPath) + 1)

      -- 2. Ensure the sub_path starts with a single slash if it doesn't already
      if string.sub(sub_path, 1, 1) ~= "/" then
        sub_path = "/" .. sub_path
      end
      
      -- 3. Combine the base URI with the subpath
      local fw_uri_to_list = fw_project_uri .. sub_path
      
      print('[FETCH] Listing files from: ' .. fw_uri_to_list)
      local fw_ls_command = string.format('%s ls "%s"', fw_beta, fw_uri_to_list)
      local fw_output = ExecuteAndLog(fw_ls_command)

      remoteDirCache[local_dir] = {}

      -- Parse the Flywheel CLI output to isolate the filenames
      if fw_output then
        for line in fw_output:gmatch("([^\r\n]+)") do
          local remote_filename = string.match(line, "%d%d%d%d%-%d%d%-%d%d%s+%d%d:%d%d%s+(.+)")
          if remote_filename then
            remote_filename = string.gsub(remote_filename, "^%s*(.-)%s*$", "%1") 
            remote_filename = remote_filename:gsub("\r", "") 
            remoteDirCache[local_dir][remote_filename] = true 
          end
        end
      end
    end

    print('[CHECK] Local file: ' .. filename)
    local remoteFileSet = remoteDirCache[local_dir]

    -- If the local file exists in the Flywheel remote directory, it is safe to delete.
    if remoteFileSet and remoteFileSet[filename] then
      print('[SUCCESS] File confirmed. Deleting local payload: ' .. filename)
      os.execute(string.format('rm "%s"', local_filepath))

      -- ATOMIC CLEANUP: Read the sidecar and delete the exact instance from Orthanc DB
      local id_filepath = local_filepath .. '.id'
      local id_file = io.open(id_filepath, 'r')
      if id_file then
          local stored_instance_id = id_file:read('*a'):gsub('%s+', '')
          id_file:close()
          if stored_instance_id and stored_instance_id ~= "" then
              print('          -> Deleting instance from Orthanc DB: ' .. stored_instance_id)
              RestApiDelete('/instances/' .. stored_instance_id)
          end
          -- Clean up the sidecar file now that the instance is purged
          os.execute(string.format('rm "%s"', id_filepath))
      end
    else
      -- File is missing from Flywheel. Do not delete local copies to prevent data loss.
      print('[SKIP] File not found in remote directory. Local copy kept.')
      all_files_verified = false
    end
    
    ::continue::
  end
  find_handle:close()

  -- Remove empty directories leftover from successful file deletions
  print('--- Cleaning up empty directories... ---')
  ExecuteAndLog(string.format("find %s -type d -empty -delete", local_study_path))

  return all_files_verified
end


--[[
=====================================================================
  MAIN LOGIC 1 - TRIGGERED ON STORED INSTANCE
=====================================================================
--]]

-- Triggered every time Orthanc receives a single DICOM slice.
function OnStoredInstance(instanceId, tags)
  -- 1. Determine destination routing based on OperatorsName (e.g. "PRISMA")
  StudyName = string.match(tags['OperatorsName'] or "", ".*/(.*)")
  if not StudyName then
    print('Error: Cannot find valid OperatorsName (0008,1050) tag. Skipping export.')
    return
  end

  -- 2. Extract standard Flywheel-compatible hierarchy identifiers
  local subjectLabel   = tostring(tags['PatientID'] or 'UnknownPatient')
  local seriesNumber   = tostring(tags['SeriesNumber'] or '00')
  local seriesDescription = CleanSeriesDescription(tags['SeriesDescription'])
  local sessionLabel   = FormatSessionLabel(tags['StudyDate'], tags['StudyTime'])

  -- 3. Append physical orientation and flip angle for PSIF sequences
  local fa_part = ""
  if string.find(seriesDescription, "PSIF") then
    local orientationPath = {'SharedFunctionalGroupsSequence', 1, 'PlaneOrientationSequence', 1, 'ImageOrientationPatient'}
    local orientationLabel = GetOrientationLabel(SafeNavigate(tags, orientationPath))
    seriesDescription = seriesDescription .. orientationLabel 
    
    local flipAnglePath = {'SharedFunctionalGroupsSequence', 1, 'MRTimingAndRelatedParametersSequence', 1, 'FlipAngle'}
    local flipAngleString = CleanFlipAngle(SafeNavigate(tags, flipAnglePath))
    if flipAngleString ~= 'UnknownFlipAngle' and flipAngleString ~= "" then
        fa_part = "_FA" .. flipAngleString
    end
  end

  -- 4. Construct the physical filepath (/export/Project/Subject/Session/Acquisition)
  local acquisitionLabel = seriesNumber .. '_' .. seriesDescription .. fa_part
  local StudyPath        = data_directory .. '/' .. StudyName
  local subjectPath      = StudyPath .. '/' .. subjectLabel
  local sessionPath      = subjectPath .. '/' .. sessionLabel
  local acquisitionPath  = sessionPath .. '/' .. acquisitionLabel

  -- 5. Handle special CALIPR naming constraints, otherwise use standard DICOM extension
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

  -- 6. Ensure the directory tree exists
  local mkdirCommand = 'mkdir -p "' .. acquisitionPath .. '"'
  os.execute(mkdirCommand)

  -- Skip if file already exists (prevents duplicate processing overhead)
  local f = io.open(finalFilepath, 'rb')
  if f ~= nil then
    io.close(f)
    print('File already exists, skipping: ' .. finalFilepath)
    return
  end

  -- 7. Write the DICOM payload to the hard drive
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

  -- 8. Sidecar Creation: Save the internal Orthanc Instance ID right next to the DICOM file
  -- This creates a 1:1 map so VerifyAndCleanupStudy can safely delete exactly what was uploaded.
  local idFile = io.open(finalFilepath .. '.id', 'w')
  if idFile then
    idFile:write(instanceId)
    idFile:close()
  end

  print('Exported instance ' .. instanceId .. ' to ' .. finalFilepath)
end

--[[
=====================================================================
  MAIN LOGIC 2 - TRIGGERED ON STABLE STUDY
=====================================================================
--]]

-- Triggered automatically by Orthanc when a study receives no new DICOMs for a defined timeout (e.g., 60 seconds).
function OnStableStudy(studyId, tags, metadata)
  print('A study has become stable (ID: ' .. studyId .. '). Triggering export sweep...')

  local routes = ParseJsonFile(routing_path)
  local api_keys = ParseJsonFile(keychain_path)

  if not routes or not api_keys then
    error('Could not load routing or keychain files. Stopping.')
    return
  end

  -- 1. Sync raw data (RRDF files) from the scanner over SSH
  print('Executing RRDF synchronization script...')
  local rrdf_command = python_executable .. ' ' .. rrdf_sync_script_path
  ExecuteAndLog(rrdf_command)
  print('RRDF sync finished.')

  -- 2. Iterate over every project folder waiting in the export directory.
  -- This ensures any backlog created by offline periods is systematically uploaded.
  local p = io.popen('ls -1 ' .. data_directory .. ' 2>/dev/null')
  if p then
    for projName in p:lines() do
      -- Ignore hidden files, system directories, and the .fw CLI folder
      if projName ~= "" and projName ~= "." and projName ~= ".." then
        
        local projNameLower = string.lower(projName)
        local fw_project_uri = routes[projNameLower]
        local fw_api_key = api_keys[projNameLower]
        local projPath = data_directory .. '/' .. projName

        -- 3a. Unroutable Project Handling: If a project folder isn't in routing.json, delete it.
        if not fw_project_uri or not fw_api_key then
          print('[CLEANUP] No routing rule or API key found for staged project: ' .. string.upper(projName))
          
          -- Read sidecar files to clear unroutable instances from Orthanc DB
          local idsCmd = io.popen('find "' .. projPath .. '" -type f -name "*.id" 2>/dev/null')
          if idsCmd then
             for idFile in idsCmd:lines() do
                local f = io.open(idFile, 'r')
                if f then
                   local stored_id = f:read('*a'):gsub('%s+', '')
                   f:close()
                   if stored_id ~= "" then RestApiDelete('/instances/' .. stored_id) end
                end
             end
             idsCmd:close()
          end
          print('[CLEANUP] Deleting unroutable directory: ' .. projPath)
          os.execute('rm -rf "' .. projPath .. '"')

        -- 3b. Valid Project Handling: Upload to Flywheel
        else
          print('--- Processing Project: ' .. string.upper(projName) .. ' ---')
          print('Destination found: ' .. fw_project_uri)

          -- Authenticate the CLI with this specific project's API Key
          local login_command = 'FW_CLI_API_KEY=' .. fw_api_key .. ' ' .. fw_beta .. ' login'
          local login_output = ExecuteAndLog(login_command)

          if not string.find(login_output, "Logged in to") then
            print('[ERROR] Flywheel login FAILED for ' .. string.upper(projName) .. '. Please check API key and network connection. Aborting sync.')
          else
            print('Flywheel login successful.')

            -- Import the entire project folder in bulk. 
            -- We explicitly exclude the sidecar .id files and Mac .DS_Store files so they don't upload to Flywheel.
            local import_command = string.format(
                '%s import run --project "%s" --storage "%s" --exclude "path=~.*\\.DS_Store" --exclude "path=~.*\\.id$" --tree --wait',
                fw_beta,
                fw_project_uri,
                projPath
            )
            print('Executing Flywheel Import: ' .. import_command)
            ExecuteAndLog(import_command)
            print('Import command finished.')

            -- Loop through every specific Session folder within the Project to verify its contents
            local sessionsCmd = io.popen('find "' .. projPath .. '" -mindepth 2 -maxdepth 2 -type d 2>/dev/null')
            if sessionsCmd then
              for sessionPath in sessionsCmd:lines() do
                -- Execute the cleanup validation
                local cleanup_successful = VerifyAndCleanupStudy(sessionPath, fw_project_uri, projPath)

                if cleanup_successful then
                  print('[SUCCESS] All local files verified and purged for: ' .. sessionPath)
                  -- Only delete the Session directory container if the contents were safely verified and emptied
                  os.execute('rm -rf "' .. sessionPath .. '"')
                else
                  print('[WARNING] Verification failed for ' .. sessionPath .. '. Keeping unverified files safe.')
                end
              end
              sessionsCmd:close()
            end

            -- Log out before moving to the next project
            ExecuteAndLog(fw_beta .. ' logout')
            print('Flywheel sync process finished for: ' .. string.upper(projName))
          end
        end
      end
    end
    p:close()
  end
end