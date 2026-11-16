#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 macOS 原生独立桌面应用: BabyTrack Studio.app
=================================================
使用 Swift + Cocoa + WebKit (WKWebView) 编译原生 macOS 可执行程序，
自带定制高清 .icns 图标，双击即可启动，自动拉起本地 Python 引擎并支持原生文件选择器与导出。
"""

import os
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "LatentVision Studio.app"
APP_PATH = os.path.join(BASE_DIR, APP_NAME)
CONTENTS_DIR = os.path.join(APP_PATH, "Contents")
MACOS_DIR = os.path.join(CONTENTS_DIR, "MacOS")
RESOURCES_DIR = os.path.join(CONTENTS_DIR, "Resources")


def create_app_icon(resources_dir: str) -> None:
    """绘制 Apple VisionOS × TouchDesigner 风格的高清 LATENT VISION 应用图标并打包为 AppIcon.icns"""
    from docs.generate_app_icon import create_latent_vision_icon
    png_tmp = os.path.join(resources_dir, "LatentVision_Icon.png")
    icns_path = os.path.join(resources_dir, "AppIcon.icns")
    create_latent_vision_icon(png_tmp, icns_path)
    if os.path.exists(png_tmp):
        os.remove(png_tmp)


OBJC_SOURCE = r"""
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

@interface AppDelegate : NSObject <NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate>
@property (strong, nonatomic) NSWindow *window;
@property (strong, nonatomic) WKWebView *webView;
@property (strong, nonatomic) NSTask *backendTask;
@property (strong, nonatomic) NSURL *serverURL;
@property (strong, nonatomic) NSString *projectDir;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.serverURL = [NSURL URLWithString:@"http://127.0.0.1:5050"];
    self.projectDir = @"/Users/snivk/Documents/google/baby_track";
    [self setupMainMenu];

    NSRect screenRect = [[NSScreen mainScreen] visibleFrame];
    CGFloat winW = MIN(1480.0, screenRect.size.width * 0.92);
    CGFloat winH = MIN(900.0, screenRect.size.height * 0.90);
    NSRect winRect = NSMakeRect(
        screenRect.origin.x + (screenRect.size.width - winW) / 2.0,
        screenRect.origin.y + (screenRect.size.height - winH) / 2.0,
        winW,
        winH
    );

    NSWindowStyleMask mask = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable |
                             NSWindowStyleMaskFullSizeContentView;
    self.window = [[NSWindow alloc] initWithContentRect:winRect
                                              styleMask:mask
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"BabyTrack Studio";
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    self.window.backgroundColor = [NSColor colorWithCalibratedRed:0.035 green:0.035 blue:0.045 alpha:1.0];
    self.window.titlebarAppearsTransparent = YES;
    self.window.minSize = NSMakeSize(1024, 680);

    WKWebViewConfiguration *config = [[WKWebViewConfiguration alloc] init];
    config.mediaTypesRequiringUserActionForPlayback = WKAudiovisualMediaTypeNone;

    self.webView = [[WKWebView alloc] initWithFrame:self.window.contentView.bounds configuration:config];
    self.webView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.webView.UIDelegate = self;
    self.webView.navigationDelegate = self;
    [self.webView setValue:@(NO) forKey:@"drawsBackground"];

    [self.window.contentView addSubview:self.webView];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];

    [self ensureBackendAndLoad];
}

- (void)setupMainMenu {
    NSMenu *mainMenu = [[NSMenu alloc] init];

    NSMenuItem *appMenuItem = [[NSMenuItem alloc] init];
    [mainMenu addItem:appMenuItem];
    NSMenu *appMenu = [[NSMenu alloc] init];
    [appMenu addItemWithTitle:@"Reload Studio" action:@selector(reloadPage) keyEquivalent:@"r"];
    [appMenu addItemWithTitle:@"Open Outputs Folder in Finder" action:@selector(openOutputsFolder) keyEquivalent:@"o"];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Quit BabyTrack Studio" action:@selector(terminate:) keyEquivalent:@"q"];
    appMenuItem.submenu = appMenu;

    NSMenuItem *editMenuItem = [[NSMenuItem alloc] init];
    [mainMenu addItem:editMenuItem];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    editMenuItem.submenu = editMenu;

    [NSApp setMainMenu:mainMenu];
}

- (void)reloadPage {
    [self.webView reload];
}

- (void)openOutputsFolder {
    NSString *outPath = [self.projectDir stringByAppendingPathComponent:@"outputs"];
    [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:outPath]];
}

- (void)ensureBackendAndLoad {
    [self checkServerReady:^(BOOL ready) {
        if (ready) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self.webView loadRequest:[NSURLRequest requestWithURL:self.serverURL]];
            });
        } else {
            [self startBackendProcess];
            [self pollUntilReady:40];
        }
    }];
}

- (void)checkServerReady:(void (^)(BOOL))completion {
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:self.serverURL];
    req.timeoutInterval = 0.6;
    [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        NSHTTPURLResponse *http = (NSHTTPURLResponse *)resp;
        if (http && http.statusCode == 200) {
            completion(YES);
        } else {
            completion(NO);
        }
    }] resume];
}

- (void)startBackendProcess {
    NSTask *task = [[NSTask alloc] init];
    NSString *pyPath = [[NSFileManager defaultManager] fileExistsAtPath:@"/opt/anaconda3/bin/python3"]
        ? @"/opt/anaconda3/bin/python3"
        : @"/usr/bin/python3";
    task.executableURL = [NSURL fileURLWithPath:pyPath];
    task.arguments = @[[self.projectDir stringByAppendingPathComponent:@"app.py"]];
    task.currentDirectoryURL = [NSURL fileURLWithPath:self.projectDir];
    task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    task.standardError = [NSFileHandle fileHandleWithNullDevice];
    NSError *err = nil;
    [task launchAndReturnError:&err];
    if (!err) {
        self.backendTask = task;
    }
}

- (void)pollUntilReady:(NSInteger)attemptsLeft {
    if (attemptsLeft <= 0) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [self.webView loadRequest:[NSURLRequest requestWithURL:self.serverURL]];
        });
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.2 * NSEC_PER_SEC)), dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
        [self checkServerReady:^(BOOL ready) {
            if (ready) {
                dispatch_async(dispatch_get_main_queue(), ^{
                    [self.webView loadRequest:[NSURLRequest requestWithURL:self.serverURL]];
                });
            } else {
                [self pollUntilReady:attemptsLeft - 1];
            }
        }];
    });
}

// 支持 HTML <input type="file"> 弹出 macOS 原生文件选择窗口
- (void)webView:(WKWebView *)webView
runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
initiatedByFrame:(WKFrameInfo *)frame
completionHandler:(void (^)(NSArray<NSURL *> * _Nullable))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.message = @"选择要导入的视频或音频文件";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result == NSModalResponseOK) {
            completionHandler(panel.URLs);
        } else {
            completionHandler(nil);
        }
    }];
}

// 自动授权 WKWebView 麦克风/内录声卡与摄像头采集权限
- (void)webView:(WKWebView *)webView
requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)origin
initiatedByFrame:(WKFrameInfo *)frame
type:(WKMediaCaptureType)type
decisionHandler:(void (^)(WKPermissionDecision))decisionHandler API_AVAILABLE(macos(12.0)) {
    decisionHandler(WKPermissionDecisionGrant);
}

// 拦截 /download/<filename> 链接，直接在 Finder 的 ~/Downloads 或 outputs 中高亮显示文件
- (void)webView:(WKWebView *)webView
decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction
decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = navigationAction.request.URL;
    if (url && [url.path hasPrefix:@"/download/"]) {
        NSString *filename = url.lastPathComponent;
        NSString *dlPath = [NSHomeDirectory() stringByAppendingPathComponent:[@"Downloads/" stringByAppendingString:filename]];
        NSString *outPath = [self.projectDir stringByAppendingPathComponent:[@"outputs/" stringByAppendingString:filename]];
        if ([[NSFileManager defaultManager] fileExistsAtPath:dlPath]) {
            [[NSWorkspace sharedWorkspace] activateFileViewerSelectingURLs:@[[NSURL fileURLWithPath:dlPath]]];
        } else if ([[NSFileManager defaultManager] fileExistsAtPath:outPath]) {
            [[NSWorkspace sharedWorkspace] activateFileViewerSelectingURLs:@[[NSURL fileURLWithPath:outPath]]];
        }
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
    decisionHandler(WKNavigationActionPolicyAllow);
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    if (self.backendTask && self.backendTask.isRunning) {
        [self.backendTask terminate];
    }
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app run];
    }
    return 0;
}
"""

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>BabyTrack Studio</string>
    <key>CFBundleExecutable</key>
    <string>BabyTrackStudio</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>cc.artkit.babytrack.studio</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>BabyTrack Studio</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0.0</string>
    <key>CFBundleVersion</key>
    <string>200</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
    <key>NSCameraUsageDescription</key>
    <string>BabyTrack Studio 需要访问摄像头以进行实时视觉追踪。</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>BabyTrack Studio 需要访问音频输入以实时捕获电脑播放的音乐节奏。</string>
</dict>
</plist>
"""


def build_app() -> None:
    if os.path.exists(APP_PATH):
        shutil.rmtree(APP_PATH)
    os.makedirs(MACOS_DIR, exist_ok=True)
    os.makedirs(RESOURCES_DIR, exist_ok=True)

    with open(os.path.join(CONTENTS_DIR, "Info.plist"), "w", encoding="utf-8") as f:
        f.write(INFO_PLIST)

    create_app_icon(RESOURCES_DIR)

    objc_file = os.path.join(BASE_DIR, "_macos_app_main.m")
    with open(objc_file, "w", encoding="utf-8") as f:
        f.write(OBJC_SOURCE)

    bin_path = os.path.join(MACOS_DIR, "BabyTrackStudio")
    subprocess.run(
        ["/usr/bin/clang", "-fobjc-arc", "-O2", objc_file, "-framework", "Cocoa", "-framework", "WebKit", "-o", bin_path],
        check=True,
    )
    os.remove(objc_file)
    os.chmod(bin_path, 0o755)
    print(f"✅ 成功构建 macOS 原生应用: {APP_PATH}")


if __name__ == "__main__":
    build_app()
