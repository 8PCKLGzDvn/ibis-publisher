--[[
    Ibis Publisher · PluginInit.lua
    Runs once when Lightroom loads the plugin.
    Sets up shared paths and verifies companion app database exists.
--]]

local LrPathUtils   = import 'LrPathUtils'
local LrFileUtils   = import 'LrFileUtils'
local LrApplication = import 'LrApplication'
local LrDialogs     = import 'LrDialogs'

-- ── Resolve OS-specific data directory ─────────────────────────
local function getDataDir()
    local home = LrPathUtils.getStandardFilePath('home')
    if WIN_ENV then
        local appdata = os.getenv('APPDATA') or (home .. '\\AppData\\Roaming')
        return appdata .. '\\IbisPublisher'
    else
        return home .. '/Library/Application Support/IbisPublisher'
    end
end

local function getExportDir()
    return getDataDir() .. (WIN_ENV and '\\exports' or '/exports')
end

-- ── Expose globals used by other plugin files ───────────────────
_G.IbisDataDir   = getDataDir()
_G.IbisExportDir = getExportDir()
_G.IbisDbPath    = IbisDataDir .. (WIN_ENV and '\\queue.db' or '/queue.db')
_G.IbisSqlite3   = WIN_ENV and 'sqlite3.exe' or 'sqlite3'
_G.WIN_ENV       = WIN_ENV

-- ── Create directories if needed ───────────────────────────────
local function ensureDir(path)
    if not LrFileUtils.exists(path) then
        LrFileUtils.createAllDirectories(path)
    end
end

ensureDir(IbisDataDir)
ensureDir(IbisExportDir)

-- ── Bootstrap DB if first run ──────────────────────────────────
local function dbExists()
    return LrFileUtils.exists(IbisDbPath) == 'file'
end

if not dbExists() then
    -- Companion app will create the DB on first launch.
    -- Plugin just ensures dirs exist; DB init is done by Python app.
    -- If the companion app hasn't been run yet, warn the user.
    LrDialogs.message(
        'Ibis Publisher — First Run',
        'Welcome to Ibis Publisher!\n\n' ..
        'Please launch the Ibis Publisher companion app first to complete setup.\n\n' ..
        'The companion app handles your Facebook connection and runs in the background to send your scheduled posts.',
        'info'
    )
end
