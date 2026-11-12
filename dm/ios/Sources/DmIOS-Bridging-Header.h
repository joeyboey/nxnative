// The C and Objective-C functions Launcher.swift, GameMenu.swift and LauncherSettings.swift call (CloudSaves.h, DataBookmark.h, DmGameMenu.h, save_sync.h's report before Play, the screen's rate). project.yml points SWIFT_OBJC_BRIDGING_HEADER here.
#import "CloudSaves.h"
#import "DataBookmark.h"
#import "DmGameMenu.h"
#include "save_sync.h"
int front_screen_max_fps(void);   // front.h, front_ios.m: the Frame rate row offers 120 only on a 120 Hz screen (LauncherSettings.swift offeredOptions)

// main.m: set when the game data found is an old shard archive (romfs.idx), which is no longer read. The sentence names the
// action (re-send the game data as romfs.img from the launcher), or NULL. The launcher's Game data section shows it.
const char *dm_old_archive_message(void);
