--[[
    Ibis Publisher · Info.lua
    Plugin-only approach — no Publish Service.
    Full export quality controls in the scheduling dialog.
--]]

return {
    LrSdkVersion        = 6.0,
    LrSdkMinimumVersion = 6.0,

    LrToolkitIdentifier = 'com.ibispublisher.lrplugin',
    LrPluginName        = 'Ibis Publisher',
    LrPluginInfoUrl     = 'https://github.com/ibispublisher',

    VERSION = { major=1, minor=17, revision=0, build=1 },

    LrInitPlugin         = 'PluginInit.lua',
    LrPluginInfoProvider = 'SettingsProvider.lua',

    LrExportMenuItems = {
        {
            title = 'Schedule for Facebook...',
            file  = 'ScheduleDialog.lua',
        },
    },
}
