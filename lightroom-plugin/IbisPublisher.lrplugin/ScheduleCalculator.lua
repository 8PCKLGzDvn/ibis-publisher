--[[
    Ibis Publisher · ScheduleCalculator.lua
    Given a schedule pattern and an anchor time, calculates the
    next N available posting slots.

    Pattern types:
      weekly   – specific days of week at specific times
      daily    – every day at specific times
      interval – every N minutes
      custom   – not calculated here; handled by companion app
--]]

local M = {}

-- Day-of-week name → wday number (Lua: 1=Sun, 2=Mon ... 7=Sat)
local DAY_WDAY = {
    SUN=1, MON=2, TUE=3, WED=4, THU=5, FRI=6, SAT=7
}

-- ── Parse JSON array of strings ─────────────────────────────────
-- Minimal parser; handles ["MON","WED","FRI"] and ["08:00","19:00"]
local function parseJsonArray(s)
    if not s or s == '' then return {} end
    local items = {}
    for item in s:gmatch('"([^"]+)"') do
        table.insert(items, item)
    end
    return items
end

-- ── Parse "HH:MM" to { hour, min } ─────────────────────────────
local function parseTime(t)
    local h, m = t:match('(%d+):(%d+)')
    return { hour = tonumber(h) or 0, min = tonumber(m) or 0 }
end

-- ── os.time from date table ──────────────────────────────────────
local function toTimestamp(t)
    return os.time({
        year=t.year, month=t.month, day=t.day,
        hour=t.hour, min=t.min, sec=0
    })
end

-- ── Parse ISO datetime string "YYYY-MM-DD HH:MM:SS" ────────────
local function parseIso(s)
    if not s then return os.time() end
    local Y,Mo,D,H,Mi = s:match('(%d+)-(%d+)-(%d+) (%d+):(%d+)')
    if Y then
        return os.time({year=tonumber(Y),month=tonumber(Mo),day=tonumber(D),
                        hour=tonumber(H),min=tonumber(Mi),sec=0})
    end
    return os.time()
end

-- ── Format timestamp as SQLite datetime string ───────────────────
local function fmtIso(ts)
    return os.date('%Y-%m-%d %H:%M:00', ts)
end

-- ── Advance timestamp by 1 day ───────────────────────────────────
local function addDays(ts, n)
    return ts + (n or 1) * 86400
end

-- ── Advance timestamp to next occurrence of wday ────────────────
local function nextWday(ts, targetWday)
    local current = tonumber(os.date('%w', ts)) + 1  -- Lua wday
    local diff = (targetWday - current + 7) % 7
    if diff == 0 then diff = 7 end
    return ts + diff * 86400
end

-- ── Main: compute N slots ────────────────────────────────────────
-- pattern: table from Database.getSchedulePatterns()
-- anchorTime: SQLite datetime string (start searching after this)
-- count: number of slots to generate
-- Returns: array of SQLite datetime strings
function M.computeSlots(pattern, anchorTime, count)
    local slots = {}
    if not pattern or count <= 0 then return slots end

    local anchorTs = anchorTime and parseIso(anchorTime) or os.time()
    -- Start 1 minute after anchor so we don't reuse same slot
    anchorTs = anchorTs + 60

    if pattern.pattern_type == 'interval' then
        -- Every N minutes
        local interval = tonumber(pattern.interval_minutes) or 240
        local ts = anchorTs + interval * 60
        for i = 1, count do
            table.insert(slots, fmtIso(ts))
            ts = ts + interval * 60
        end

    elseif pattern.pattern_type == 'daily' or pattern.pattern_type == 'weekly' then
        local days  = parseJsonArray(pattern.days_of_week)
        local times = parseJsonArray(pattern.times_of_day)

        if #days == 0 or #times == 0 then return slots end

        -- Build sorted wday numbers for the pattern
        local targetWdays = {}
        for _, d in ipairs(days) do
            table.insert(targetWdays, DAY_WDAY[d] or 2)
        end
        table.sort(targetWdays)

        -- Build sorted time objects
        local parsedTimes = {}
        for _, t in ipairs(times) do
            table.insert(parsedTimes, parseTime(t))
        end
        table.sort(parsedTimes, function(a,b)
            return a.hour*60+a.min < b.hour*60+b.min
        end)

        -- Walk forward day by day, picking matching days and times
        local cursor = anchorTs
        local found  = 0
        local maxIter = count * 60  -- safety cap

        for _ = 1, maxIter do
            local curWday = tonumber(os.date('%w', cursor)) + 1
            local isMatchDay = false
            for _, wd in ipairs(targetWdays) do
                if wd == curWday then isMatchDay = true; break end
            end

            if isMatchDay then
                local dateT = os.date('*t', cursor)
                for _, pt in ipairs(parsedTimes) do
                    local slotTs = toTimestamp({
                        year=dateT.year, month=dateT.month, day=dateT.day,
                        hour=pt.hour, min=pt.min
                    })
                    if slotTs > anchorTs then
                        table.insert(slots, fmtIso(slotTs))
                        found = found + 1
                        if found >= count then break end
                    end
                end
            end

            if found >= count then break end
            cursor = addDays(cursor, 1)
        end
    end

    return slots
end

-- ── Check for 25-posts-per-day rate limit ───────────────────────
-- Returns: true if safe, false + offending date string if exceeds limit
function M.checkRateLimit(slots)
    local dayCounts = {}
    for _, slot in ipairs(slots) do
        local day = slot:sub(1, 10)
        dayCounts[day] = (dayCounts[day] or 0) + 1
        if dayCounts[day] > 25 then
            return false, day
        end
    end
    return true, nil
end

return M
