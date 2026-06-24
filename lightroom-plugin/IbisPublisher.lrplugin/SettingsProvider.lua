--[[
    Ibis Publisher · SettingsProvider.lua  v1.10
    Plugin Manager panel — shows connection status, export settings,
    and a working Open Queue Manager button.
--]]

local LrView      = import 'LrView'
local LrBinding   = import 'LrBinding'
local LrFileUtils = import 'LrFileUtils'
local LrHttp      = import 'LrHttp'
local LrDialogs   = import 'LrDialogs'
local LrShell     = import 'LrShell'

local Database = require 'Database'

local function readSetting(key)
    local rows = Database.query(
        string.format("SELECT value FROM settings WHERE key='%s' LIMIT 1;", key)
    )
    if #rows > 0 and rows[1][1] then return rows[1][1] end
    return ''
end

local function saveSetting(key, value)
    Database.exec(string.format(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('%s', '%s', datetime('now'));",
        key, tostring(value):gsub("'","''")
    ))
end

return {
    title = 'Ibis Publisher',

    sectionsForTopOfDialog = function(f, propertyTable)
        local dbOk     = LrFileUtils.exists(IbisDbPath) == 'file'
        local pageName = dbOk and readSetting('page_name') or ''
        local quality  = readSetting('export_quality')
        local maxDim   = readSetting('export_max_dimension')

        -- Load current values into property table
        propertyTable.exportQuality  = tonumber(quality) or 90
        propertyTable.exportMaxDim   = tonumber(maxDim) or 2048
        propertyTable.exportSharp    = readSetting('export_sharpening') ~= '0'
        propertyTable.exportWatermark = readSetting('export_watermark') == '1'
        propertyTable.exportColorSpace = readSetting('export_color_space') or 'sRGB'

        local statusText
        if not dbOk then
            statusText = 'Not connected — launch Ibis Publisher at http://localhost:8765'
        elseif pageName ~= '' then
            statusText = 'Connected to Facebook Page: ' .. pageName
        else
            statusText = 'Connected — no Facebook Page set yet'
        end

        return {
            -- ── Status section ──────────────────────────────────
            {
                title = 'Ibis Publisher — Status',
                f:column {
                    spacing = f:label_spacing(),

                    f:static_text {
                        title = statusText,
                        font  = '<system>',
                    },
                    f:static_text {
                        title = 'Database: ' .. IbisDbPath,
                        font  = '<system/small>',
                    },
                    f:static_text {
                        title = 'Exports: ' .. IbisExportDir,
                        font  = '<system/small>',
                    },
                    f:push_button {
                        title  = 'Open Queue Manager',
                        action = function()
                            -- Try to open browser to the web app
                            LrShell.openPathInShell('http://localhost:8765')
                        end,
                    },
                },
            },

            -- ── Export Settings section ─────────────────────────
            {
                title = 'Export Settings',
                f:column {
                    spacing = f:label_spacing(),

                    -- Quality slider
                    f:row {
                        f:static_text {
                            title = 'JPEG Quality:',
                            width = 120,
                            alignment = 'right',
                        },
                        f:slider {
                            value     = LrView.bind 'exportQuality',
                            min       = 50,
                            max       = 100,
                            integral  = true,
                            width     = 180,
                        },
                        f:static_text {
                            value = LrView.bind {
                                key = 'exportQuality',
                                transform = function(v) return tostring(v or 90) end
                            },
                            width = 30,
                        },
                    },

                    -- Max dimension
                    f:row {
                        f:static_text {
                            title = 'Max dimension (px):',
                            width = 120,
                            alignment = 'right',
                        },
                        f:edit_field {
                            value    = LrView.bind 'exportMaxDim',
                            width    = 80,
                            min      = 800,
                            max      = 8000,
                            numeral  = true,
                            integral = true,
                        },
                        f:static_text {
                            title = 'px (long edge)',
                            font  = '<system/small>',
                        },
                    },

                    -- Color space
                    f:row {
                        f:static_text {
                            title = 'Color space:',
                            width = 120,
                            alignment = 'right',
                        },
                        f:popup_menu {
                            value = LrView.bind 'exportColorSpace',
                            items = { 'sRGB', 'AdobeRGB' },
                            width = 120,
                        },
                    },

                    -- Sharpening
                    f:row {
                        f:static_text { title = '', width = 120 },
                        f:checkbox {
                            title = 'Output sharpening (screen)',
                            value = LrView.bind 'exportSharp',
                        },
                    },

                    -- Watermark
                    f:row {
                        f:static_text { title = '', width = 120 },
                        f:checkbox {
                            title = 'Apply watermark',
                            value = LrView.bind 'exportWatermark',
                        },
                    },

                    -- Save button
                    f:push_button {
                        title  = 'Save Export Settings',
                        action = function()
                            saveSetting('export_quality',     tostring(propertyTable.exportQuality or 90))
                            saveSetting('export_max_dimension', tostring(propertyTable.exportMaxDim or 2048))
                            saveSetting('export_color_space', propertyTable.exportColorSpace or 'sRGB')
                            saveSetting('export_sharpening',  propertyTable.exportSharp and '1' or '0')
                            saveSetting('export_watermark',   propertyTable.exportWatermark and '1' or '0')
                            LrDialogs.message('Ibis Publisher', 'Export settings saved.', 'info')
                        end,
                    },
                },
            },
        }
    end,
}
