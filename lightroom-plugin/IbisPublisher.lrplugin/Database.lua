--[[
    Ibis Publisher · Database.lua
    All SQLite read/write operations from the Lightroom plugin side.
    Communicates via sqlite3 CLI (bundled with macOS; installed with companion app on Windows).
--]]

local LrTasks = import 'LrTasks'

local M = {}

-- ── Execute a SQL statement (write) ────────────────────────────
function M.exec(sql)
    -- Escape single quotes in SQL
    local escaped = sql:gsub("'", "''")
    local cmd = string.format('%s "%s" "%s"',
        IbisSqlite3, IbisDbPath, sql)
    local handle = io.popen(cmd, 'r')
    if handle then
        local result = handle:read('*a')
        handle:close()
        return true, result
    end
    return false, 'sqlite3 not found or DB missing'
end

-- ── Execute a query and return rows ────────────────────────────
function M.query(sql)
    local cmd = string.format('%s -separator "|||" "%s" "%s"',
        IbisSqlite3, IbisDbPath, sql)
    local handle = io.popen(cmd, 'r')
    if not handle then return {} end

    local rows = {}
    for line in handle:lines() do
        local cols = {}
        for col in (line .. '|||'):gmatch('(.-)||%|') do
            table.insert(cols, col)
        end
        table.insert(rows, cols)
    end
    handle:close()
    return rows
end

-- ── Escape a string value for SQL ──────────────────────────────
function M.escape(s)
    if s == nil then return 'NULL' end
    return "'" .. tostring(s):gsub("'", "''") .. "'"
end

-- ── Insert a scheduled post ─────────────────────────────────────
function M.insertPost(scheduledTime, caption, photoPath, photoPath2, patternId)
    local sql = string.format(
        "INSERT INTO posts (scheduled_time, caption, photo_path, photo_path_2, schedule_pattern_id) " ..
        "VALUES (%s, %s, %s, %s, %s);",
        M.escape(scheduledTime),
        M.escape(caption),
        M.escape(photoPath),
        photoPath2 and M.escape(photoPath2) or 'NULL',
        patternId and tostring(patternId) or 'NULL'
    )
    return M.exec(sql)
end

-- ── Load caption templates ──────────────────────────────────────
function M.getCaptionTemplates()
    local rows = M.query("SELECT id, name, body FROM caption_templates ORDER BY name;")
    local templates = {}
    for _, row in ipairs(rows) do
        table.insert(templates, { id=row[1], name=row[2], body=row[3] })
    end
    return templates
end

-- ── Save a caption template ─────────────────────────────────────
function M.saveCaptionTemplate(name, body)
    local sql = string.format(
        "INSERT OR REPLACE INTO caption_templates (name, body) VALUES (%s, %s);",
        M.escape(name), M.escape(body)
    )
    return M.exec(sql)
end

-- ── Load schedule patterns ──────────────────────────────────────
function M.getSchedulePatterns()
    local rows = M.query(
        "SELECT id, name, pattern_type, days_of_week, times_of_day, interval_minutes, is_active " ..
        "FROM schedule_patterns ORDER BY name;"
    )
    local patterns = {}
    for _, row in ipairs(rows) do
        table.insert(patterns, {
            id             = row[1],
            name           = row[2],
            pattern_type   = row[3],
            days_of_week   = row[4],
            times_of_day   = row[5],
            interval_minutes = row[6],
            is_active      = row[7] == '1'
        })
    end
    return patterns
end

-- ── Get active pattern ──────────────────────────────────────────
function M.getActivePattern()
    local rows = M.query(
        "SELECT id, name, pattern_type, days_of_week, times_of_day, interval_minutes " ..
        "FROM schedule_patterns WHERE is_active=1 LIMIT 1;"
    )
    if #rows == 0 then return nil end
    local r = rows[1]
    return {
        id             = r[1],
        name           = r[2],
        pattern_type   = r[3],
        days_of_week   = r[4],
        times_of_day   = r[5],
        interval_minutes = r[6]
    }
end

-- ── Get latest scheduled post time ─────────────────────────────
function M.getLatestScheduledTime()
    local rows = M.query(
        "SELECT MAX(scheduled_time) FROM posts WHERE status IN ('scheduled','retrying');"
    )
    if #rows > 0 and rows[1][1] and rows[1][1] ~= '' then
        return rows[1][1]
    end
    return nil
end

-- ── Get queued post count ───────────────────────────────────────
function M.getQueueCount()
    local rows = M.query("SELECT COUNT(*) FROM posts WHERE status='scheduled';")
    if #rows > 0 then return tonumber(rows[1][1]) or 0 end
    return 0
end

return M
