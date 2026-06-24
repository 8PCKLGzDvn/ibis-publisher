--[[
    Ibis Publisher · IbisPublishProvider.lua
    Publish Service provider — gives us the full native Lightroom
    export UI (file settings, image sizing, sharpening, watermark, metadata).
    
    User sets up a Publish Service called "Ibis Publisher" with their
    preferred export settings. Then right-clicks photos → Publish,
    which triggers our scheduling dialog.
--]]

local LrApplication     = import 'LrApplication'
local LrBinding         = import 'LrBinding'
local LrDialogs         = import 'LrDialogs'
local LrExportSession   = import 'LrExportSession'
local LrFileUtils       = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrHttp            = import 'LrHttp'
local LrPathUtils       = import 'LrPathUtils'
local LrProgressScope   = import 'LrProgressScope'
local LrTasks           = import 'LrTasks'
local LrView            = import 'LrView'

local Database           = require 'Database'
local TokenResolver      = require 'TokenResolver'
local ScheduleCalculator = require 'ScheduleCalculator'

-- ── Provider definition ─────────────────────────────────────────
local publishServiceProvider = {}

publishServiceProvider.supportsIncrementalPublish = 'only'
publishServiceProvider.hideSections = { 'exportLocation' }
publishServiceProvider.allowFileFormats = { 'JPEG', 'PNG' }
publishServiceProvider.allowColorSpaces = { 'sRGB', 'AdobeRGB' }
publishServiceProvider.canExportVideo = false

publishServiceProvider.titleForPublishedCollection        = 'Facebook Schedule'
publishServiceProvider.titleForPublishedCollectionSet     = 'Facebook Schedules'
publishServiceProvider.titleForGoToPublishedCollection    = 'Go to Schedule'

-- ── Export destination ──────────────────────────────────────────
function publishServiceProvider.updateExportSettings(exportSettings)
    exportSettings.LR_export_destinationType       = 'specificFolder'
    exportSettings.LR_export_destinationPathPrefix = IbisExportDir
    exportSettings.LR_export_useSubfolder          = false
    exportSettings.LR_collisionHandling            = 'rename'
end

-- ── Sections for export dialog ──────────────────────────────────
-- Return empty table — we use all the native sections (file, sizing, sharpening, watermark)
function publishServiceProvider.sectionsForTopOfDialog(f, propertyTable)
    return {}
end

function publishServiceProvider.sectionsForBottomOfDialog(f, propertyTable)
    return {}
end

-- ── Process rendered photos ─────────────────────────────────────
-- This is called after Lightroom finishes exporting each photo.
-- We show a caption dialog then write to the queue.
function publishServiceProvider.processRenderedPhotos(functionContext, exportContext)

    local exportSession  = exportContext:getExportSession()
    local exportSettings = exportContext:getPropertyTable()
    local nPhotos        = exportSession:countRenditions()

    local progressScope = exportContext:configureProgress({
        title = string.format('Ibis Publisher: scheduling %d photo(s)...', nPhotos)
    })

    -- Collect all rendered paths and photos first
    local renderedItems = {}
    for i, rendition in exportSession:renditions() do
        local success, pathOrMessage = rendition:waitForRender()
        if progressScope:isCanceled() then break end
        if success then
            table.insert(renderedItems, {
                photo = rendition:getPhoto(),
                path  = pathOrMessage,
            })
        end
    end

    if #renderedItems == 0 then return end

    local function postToServer(jsonStr)
        local headers = {
            { field = 'Content-Type', value = 'application/json' },
        }
        local body, respHeaders = LrHttp.post('http://localhost:8765/api/schedule-post', jsonStr, headers)
        return body
    end

    local function jsonEncode(t)
        local parts = {}
        for k, v in pairs(t) do
            local val = tostring(v):gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r')
            table.insert(parts, '"' .. k .. '":"' .. val .. '"')
        end
        return '{' .. table.concat(parts, ',') .. '}'
    end

    -- Show scheduling dialog for each photo
    local function showCaptionDialog(idx)
        if idx > #renderedItems then return end

        local item  = renderedItems[idx]
        local photo = item.photo
        local path  = item.path
        local fname = photo:getFormattedMetadata('fileName') or ''
        local navInfo = #renderedItems > 1 and
            string.format('Photo %d of %d — %s', idx, #renderedItems, fname) or fname

        local captionText = ''
        local result = LrFunctionContext.callWithContext('captionInput', function(ctx)
            local props = LrBinding.makePropertyTable(ctx)
            props.caption = ''

            local f = LrView.osFactory()
            local contents = f:column {
                spacing = f:dialog_spacing(),
                width   = 520,
                bind_to_object = props,

                f:row {
                    f:static_text { title = '🦢  Ibis Publisher', font = '<system/bold>' },
                    f:spacer { fill_horizontal = 1 },
                    f:static_text { title = navInfo, font = '<system/small>' },
                },
                f:separator { fill_horizontal = 1 },
                f:static_text { title = 'Caption:' },
                f:edit_field {
                    value                 = LrView.bind 'caption',
                    height_in_lines       = 5,
                    fill_horizontal       = 1,
                    allows_multiple_lines = true,
                    placeholder_text      = 'Write a caption for this photo...',
                },
            }

            local r = LrDialogs.presentModalDialog {
                title      = 'Ibis Publisher — Schedule Post',
                contents   = contents,
                actionVerb = '📅  Add to Schedule',
                cancelVerb = 'Skip',
            }

            captionText = props.caption or ''
            return r
        end)

        if result == 'ok' then
            local caption = TokenResolver.resolve(photo, captionText)
            local slots = {}
            local body = LrHttp.get('http://localhost:8765/api/next-slots?count=1', nil)
            if body then
                for slot in body:gmatch('"(%d%d%d%d%-%d%d%-%d%d %d%d:%d%d:%d%d)"') do
                    table.insert(slots, slot)
                end
            end
            local slotTime = #slots > 0 and slots[1] or os.date('%Y-%m-%d %H:%M:00')
            local resp = postToServer(jsonEncode({
                scheduled_time = slotTime,
                caption        = caption,
                photo_path     = path,
            }))
            LrDialogs.message('Ibis Publisher',
                'Added to schedule for ' .. slotTime, 'info')
            showCaptionDialog(idx + 1)
        end
    end

    LrTasks.startAsyncTask(function()
        showCaptionDialog(1)
    end)
end

return publishServiceProvider
