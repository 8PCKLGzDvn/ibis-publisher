--[[
    Ibis Publisher · TokenResolver.lua  v1.14
    Resolves {token} placeholders in captions using Lightroom photo metadata.
--]]

local LrStringUtils = import 'LrStringUtils'

local M = {}

local function fmtAperture(v)
    if not v or v == '' then return '' end
    local n = tonumber(v)
    if not n then return tostring(v) end
    if n == math.floor(n) then return string.format('f/%d', n) end
    return string.format('f/%.1f', n)
end

local function fmtShutter(v)
    if not v or v == '' then return '' end
    local n = tonumber(v)
    if not n then return tostring(v) end
    if n >= 1 then return string.format('%ds', math.floor(n)) end
    return string.format('1/%ds', math.floor(1/n + 0.5))
end

local function fmtDate(dateStr)
    if not dateStr or dateStr == '' then return '' end
    local y, mo, d = dateStr:match('(%d%d%d%d):(%d%d):(%d%d)')
    if not y then return dateStr end
    local months = {
        'January','February','March','April','May','June',
        'July','August','September','October','November','December'
    }
    return string.format('%s %d, %s', months[tonumber(mo)] or mo, tonumber(d), y)
end

local function fmtRating(n)
    n = tonumber(n) or 0
    local s = ''
    for i = 1, n do s = s .. '★' end
    return s
end

local function safe(fn)
    local ok, val = pcall(fn)
    if ok and val then return tostring(val) end
    return ''
end

function M.resolve(photo, template)
    if not template or template == '' then return '' end

    -- Build resolver map — each value is a function called only if token is used
    local resolvers = {
        filename = function()
            local f = safe(function() return photo:getFormattedMetadata('fileName') end)
            return f:gsub('%.[^%.]+$', '')
        end,
        capture_date = function()
            local d = safe(function() return photo:getFormattedMetadata('dateTimeOriginal') end)
            if d == '' then return '' end
            local formatted = fmtDate(d)
            return formatted ~= '' and formatted or d
        end,
        capture_year = function()
            local d = safe(function() return photo:getFormattedMetadata('dateTimeOriginal') end)
            return d:match('(%d%d%d%d)') or ''
        end,
        camera = function()
            local make  = LrStringUtils.trimWhitespace(safe(function() return photo:getFormattedMetadata('cameraMake') end))
            local model = LrStringUtils.trimWhitespace(safe(function() return photo:getFormattedMetadata('cameraModel') end))
            if make ~= '' and model ~= '' then
                if model:find(make, 1, true) then return model end
                return make .. ' ' .. model
            end
            return model ~= '' and model or make
        end,
        lens = function()
            return LrStringUtils.trimWhitespace(safe(function() return photo:getFormattedMetadata('lens') end))
        end,
        focal_length = function()
            local fl = safe(function() return photo:getRawMetadata('focalLength') end)
            local n  = tonumber(fl)
            return n and string.format('%dmm', math.floor(n + 0.5)) or ''
        end,
        aperture = function()
            return fmtAperture(safe(function() return photo:getRawMetadata('aperture') end))
        end,
        shutter = function()
            return fmtShutter(safe(function() return photo:getRawMetadata('shutterSpeed') end))
        end,
        iso = function()
            return safe(function() return photo:getRawMetadata('isoSpeedRating') end)
        end,
        keyword = function()
            local ok, kws = pcall(function() return photo:getRawMetadata('keywords') end)
            if ok and kws and #kws > 0 then
                local ok2, name = pcall(function() return kws[1]:getName() end)
                if ok2 then return name end
            end
            return ''
        end,
        keywords = function()
            local ok, kws = pcall(function() return photo:getRawMetadata('keywords') end)
            if not ok or not kws then return '' end
            local names = {}
            for _, kw in ipairs(kws) do
                local ok2, name = pcall(function() return kw:getName() end)
                if ok2 and name then table.insert(names, name) end
            end
            return table.concat(names, ', ')
        end,
        location = function()
            local loc     = safe(function() return photo:getFormattedMetadata('location') end)
            local city    = safe(function() return photo:getFormattedMetadata('city') end)
            local country = safe(function() return photo:getFormattedMetadata('country') end)
            if loc ~= '' then return loc end
            if city ~= '' and country ~= '' then return city .. ', ' .. country end
            return city ~= '' and city or country
        end,
        rating = function()
            return fmtRating(safe(function() return photo:getRawMetadata('rating') end))
        end,
        collection = function()
            local ok, cols = pcall(function() return photo:getContainedCollections() end)
            if ok and cols and #cols > 0 then
                local ok2, name = pcall(function() return cols[1]:getName() end)
                if ok2 then return name end
            end
            return ''
        end,
    }

    -- Replace each {token} exactly once using gsub
    local result = template:gsub('{([%w_]+)}', function(token)
        local fn = resolvers[token]
        if fn then
            local ok, val = pcall(fn)
            if ok and val and val ~= '' then return val end
            return ''  -- token exists but no value — remove it
        end
        return '{' .. token .. '}'  -- unknown token — leave as-is
    end)

    return result
end

return M
