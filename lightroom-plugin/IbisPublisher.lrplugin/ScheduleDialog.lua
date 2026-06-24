--[[
    Ibis Publisher · ScheduleDialog.lua  v1.14
    Clean dialog with:
      - Caption field
      - Quality / size / sharpening controls
      - Add to Schedule and Post Now buttons
--]]

local LrApplication     = import 'LrApplication'
local LrBinding         = import 'LrBinding'
local LrDialogs         = import 'LrDialogs'
local LrExportSession   = import 'LrExportSession'
local LrFileUtils       = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrHttp            = import 'LrHttp'
local LrProgressScope   = import 'LrProgressScope'
local LrTasks           = import 'LrTasks'
local LrView            = import 'LrView'

local Database           = require 'Database'
local ScheduleCalculator = require 'ScheduleCalculator'

LrTasks.startAsyncTask(function()
LrFunctionContext.callWithContext('IbisDialog', function(context)

    if not LrFileUtils.exists(IbisDbPath) then
        LrDialogs.message('Ibis Publisher',
            'Please launch the Ibis Publisher web app first.\nGo to http://localhost:8765',
            'critical')
        return
    end

    local catalog = LrApplication.activeCatalog()
    local photos  = catalog:getTargetPhotos()

    if #photos == 0 then
        LrDialogs.message('Ibis Publisher', 'Please select at least one photo first.', 'info')
        return
    end

    -- ── Check for active schedule ───────────────────────────────
    local hasSchedule = Database.getActivePattern() ~= nil
    if not hasSchedule then
        LrDialogs.message('Ibis Publisher',
            'No posting schedule has been set up yet.\n\n'
            .. 'You can still post immediately, but to use "Add to Schedule" '
            .. 'you\'ll need to create a schedule first at:\n'
            .. 'http://localhost:8765/schedule',
            'warning')
    end

    -- ── Load export settings from DB ────────────────────────────
    local function getSetting(key, default)
        local rows = Database.query("SELECT value FROM settings WHERE key='" .. key .. "' LIMIT 1;")
        if #rows > 0 and rows[1][1] and rows[1][1] ~= '' then return rows[1][1] end
        return default
    end

    -- ── State ────────────────────────────────────────────────────
    local captions = {}
    for i = 1, #photos do captions[i] = '' end

    -- ── Export settings (read once from DB) ───────────────────────
    local quality     = tonumber(getSetting('export_quality', '90')) or 90
    local colorSpace  = getSetting('export_color_space', 'sRGB')
    local doResize    = getSetting('export_do_resize', '1') ~= '0'
    local dimension   = tonumber(getSetting('export_dimension', '2048')) or 2048
    local resolution  = tonumber(getSetting('export_resolution', '240')) or 240
    local dontEnlarge = getSetting('export_dont_enlarge', '1') ~= '0'

    local exportSettings = {
        LR_export_destinationType        = 'specificFolder',
        LR_export_destinationPathPrefix  = IbisExportDir,
        LR_export_useSubfolder           = false,
        LR_format                        = 'JPEG',
        LR_jpeg_quality                  = quality / 100,
        LR_size_doConstrain              = doResize,
        LR_size_maxWidth                 = dimension,
        LR_size_maxHeight                = dimension,
        LR_size_resizeType               = 'longEdge',
        LR_size_units                    = 'pixels',
        LR_size_doNotEnlarge             = dontEnlarge,
        LR_jpeg_useLimitSize             = false,
        LR_collisionHandling             = 'rename',
        LR_outputSharpeningOn            = false,
        LR_export_colorSpace             = colorSpace,
        LR_useWatermark                  = false,
        LR_removeLocationMetadata        = false,
        LR_metadata_keywordOptions       = 'lightroomHierarchical',
        LR_export_resolution             = resolution,
    }

    -- ── Export + queue ───────────────────────────────────────────
    local function doExport(postNow)

        -- Calculate slots
        local slots = {}
        if postNow then
            local nowStr = os.date('%Y-%m-%d %H:%M:00')
            for i = 1, #photos do slots[i] = nowStr end
        else
            local pattern = Database.getActivePattern()
            if not pattern then
                LrDialogs.message('Ibis Publisher',
                    'No schedule pattern set.\nConfigure one at http://localhost:8765',
                    'warning')
                return false
            end

            local url = 'http://localhost:8765/api/next-slots?count=' .. tostring(#photos)
            local body, headers = LrHttp.get(url, nil)
            if body then
                for slot in body:gmatch('"(%d%d%d%d%-%d%d%-%d%d %d%d:%d%d:%d%d)"') do
                    table.insert(slots, slot)
                end
            end

            if #slots == 0 then
                LrDialogs.message('Ibis Publisher',
                    'Could not calculate slots. Check your schedule pattern.',
                    'warning')
                return false
            end
        end

        local exported = 0
        local errors   = 0

        LrFunctionContext.callWithContext('IbisExport', function(exportContext)
            local progress = LrProgressScope({
                title           = string.format('Ibis Publisher: exporting %d photo(s)...', #photos),
                functionContext = exportContext,
            })

            for i, photo in ipairs(photos) do
                if progress:isCanceled() then break end
                progress:setCaption(string.format('%d of %d', i, #photos))

                local resolved = captions[i] or ''

                local session = LrExportSession({
                    photosToExport = { photo },
                    exportSettings = exportSettings,
                })

                local exportedPath = nil
                for _, rendition in session:renditions() do
                    local ok, pathOrErr = rendition:waitForRender()
                    if ok then
                        exportedPath = pathOrErr
                    else
                        errors = errors + 1
                    end
                end

                if exportedPath and slots[i] then
                    local ok, _ = Database.insertPost(slots[i], resolved, exportedPath, nil, nil)
                    if ok then exported = exported + 1 else errors = errors + 1 end
                end
            end

            progress:done()
        end)

        -- Confirmation message
        local action = postNow and 'queued for immediate posting' or 'added to schedule'
        local msg = string.format('%d photo(s) %s.', exported, action)
        if errors > 0 then msg = msg .. string.format('\n%d failed to export.', errors) end
        if not postNow and #slots > 0 and exported > 0 then
            msg = msg .. '\n\nFirst: ' .. (slots[1] or '')
            if exported > 1 then msg = msg .. '\nLast:  ' .. (slots[exported] or '') end
        end
        LrDialogs.message('Ibis Publisher', msg, 'info')
        return true
    end

    -- ── Show dialog ──────────────────────────────────────────────
    local function showDialog(idx)
        if idx > #photos then return end

        local photo  = photos[idx]
        local fname  = photo:getFormattedMetadata('fileName') or ''
        local navInfo = #photos > 1 and
            string.format('%d of %d  —  %s', idx, #photos, fname) or fname

        local props = LrBinding.makePropertyTable(context)
        props.caption = captions[idx]

        local f = LrView.osFactory()
        local isLast = (idx == #photos)

        local dialogRows = {
            bind_to_object = props,
            spacing = f:control_spacing(),
            width   = 520,

            f:row {
                f:static_text { title = '🦢  Ibis Publisher', font = '<system/bold>' },
                f:spacer { fill_horizontal = 1 },
                f:static_text { title = navInfo, font = '<system/small>' },
            },

            f:separator { fill_horizontal = 1 },

            f:row {
                fill_horizontal = 1,
                f:spacer { fill_horizontal = 1 },
                f:catalog_photo {
                    photo  = photo,
                    width  = 450,
                    height = 300,
                },
                f:spacer { fill_horizontal = 1 },
            },

            f:static_text { title = 'Caption:' },
            f:edit_field {
                value                 = LrView.bind 'caption',
                height_in_lines       = 4,
                fill_horizontal       = 1,
                allows_multiple_lines = true,
                placeholder_text      = 'Write a caption...',
            },
            f:separator { fill_horizontal = 1 },
        }

        if isLast then
            table.insert(dialogRows, f:push_button {
                title  = '⚡  Post Now',
                action = function()
                    captions[idx] = props.caption or ''
                    doExport(true)
                end,
            })
        end

        local contents = f:column(dialogRows)

        local result = LrDialogs.presentModalDialog {
            title      = 'Ibis Publisher',
            contents   = contents,
            actionVerb = isLast and '📅  Add to Schedule' or '▶  Next Photo',
            cancelVerb = 'Cancel',
        }

        if result == 'ok' then
            captions[idx] = props.caption or ''
            if isLast then
                doExport(false)
            else
                showDialog(idx + 1)
            end
        end
    end

    showDialog(1)

end)
end)
