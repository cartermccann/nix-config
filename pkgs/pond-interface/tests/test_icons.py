"""Real desktop-entry lookup: iconless Electron protocol handlers must not
hide the application's packaged icon. Uses only temporary desktop entries."""
from pathlib import Path
import unittest
import qmlharness


@unittest.skipUnless(qmlharness.QS, 'set QS for desktop-entry/icon tests')
class AppIcons(unittest.TestCase):
    def test_protocol_entry_falls_back_but_explicit_local_icon_wins(self):
        for local_icon in ('', 'custom'):
            with self.subTest(local_icon=local_icon):
                harness = qmlharness.Harness()
                self.addCleanup(harness.close)
                data = Path(harness.tmp.name) / 'data'
                apps = data / 'applications'
                apps.mkdir(parents=True)
                (apps / 't3code.desktop').write_text('[Desktop Entry]\nType=Application\nName=T3 Code\n'
                    'Exec=true\nIcon=t3code\nStartupWMClass=com.t3tools.T3Code\n')
                (apps / 'com.t3tools.T3Code.desktop').write_text('[Desktop Entry]\nType=Application\n'
                    'Name=T3 Code protocol handler\nExec=true %U\nNoDisplay=true\n'
                    'MimeType=x-scheme-handler/t3code;\n' + ('Icon=custom\n' if local_icon else ''))
                icons = data / 'icons/hicolor/scalable/apps'
                icons.mkdir(parents=True)
                for name in ('t3code', 'custom'):
                    (icons / (name + '.svg')).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" '
                        'height="32"><rect width="32" height="32" fill="white"/></svg>')
                source = (qmlharness.OVERLAY_SHELL / 'NiriMsg.qml').read_text()
                functions = source[source.index('  function _appInfo('):source.index('  property var _rows:')]
                harness.write('shell.qml', '''import QtQuick
import Quickshell
ShellRoot {
 property var _appInfoCache: ({})
 Component.onCompleted: console.log("Entries initializing", DesktopEntries.applications.values.length)
''' + functions + '''
 Image { id: icon; sourceSize.width: 32; sourceSize.height: 32 }
 Timer { interval: 700; running: true; onTriggered: {
  console.log("RESULT blank", JSON.stringify(_appInfo("").iconName));
  const info = _appInfo("com.t3tools.T3Code");
  console.log("RESULT info", JSON.stringify(info));
  icon.source = info.iconSource;
  finish.start();
 }}
 Timer { id: finish; interval: 200; onTriggered: {
  console.log("RESULT ready", JSON.stringify(icon.status === Image.Ready));
  console.log("RESULT DONE", "true"); Qt.quit();
 }}
}''')
                results, log = harness.run(env={'XDG_DATA_HOME': str(data), 'XDG_DATA_DIRS': str(data)})
                self.assertEqual(results['info'][0]['iconName'], local_icon or 't3code', log)
                self.assertEqual(results['blank'], [''], log)
                self.assertEqual(results['ready'], [True], log)
                self.assertNotIn('Could not load icon', log)

    def test_notifications_resolve_zen_and_bundle_the_generic_fallback(self):
        harness = qmlharness.Harness()
        self.addCleanup(harness.close)
        data = Path(harness.tmp.name) / 'data'
        apps = data / 'applications'
        apps.mkdir(parents=True)
        (apps / 'zen-beta.desktop').write_text('[Desktop Entry]\nType=Application\nName=Zen Browser (Beta)\n'
            'Exec=true\nIcon=zen-browser\nStartupWMClass=zen-beta\n')
        icons = data / 'icons/hicolor/scalable/apps'
        icons.mkdir(parents=True)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="12" cy="12" r="8" fill="white"/></svg>'
        (icons / 'zen-browser.svg').write_text(svg)
        harness.write('assets/bell.svg', svg)
        source = (qmlharness.OVERLAY_SHELL / 'NiriMsg.qml').read_text()
        app_functions = source[source.index('  function _appInfo('):source.index('  property var _rows:')]
        harness.write('NiriMsg.qml', 'pragma Singleton\nimport QtQuick\nimport Quickshell\n'
            'Singleton { property var _appInfoCache: ({})\n' + app_functions + '\n}')
        source = (qmlharness.OVERLAY_SHELL / 'notifications/State.qml').read_text()
        functions = source[source.index('  function availableIcon('):source.index('  function title(')]
        harness.write('notifications/State.qml', 'pragma Singleton\nimport QtQuick\nimport Quickshell\n'
            'import ".." as Shell\nSingleton {\n' + functions + '\n}')
        harness.write('shell.qml', '''import QtQuick
import Quickshell
import "notifications" as Notifications
ShellRoot {
 Component.onCompleted: console.log("Entries initializing", DesktopEntries.applications.values.length)
 Image { id: icon; sourceSize.width:24; sourceSize.height:24 }
 Timer { interval:700; running:true; onTriggered: {
  const source=Notifications.State.iconSource({appName:"Zen", desktopEntry:"zen", appIcon:""});
  console.log("RESULT zen", JSON.stringify(source));
  icon.source=source; zenLoaded.start();
 }}
 Timer { id:zenLoaded; interval:200; onTriggered: {
  console.log("RESULT ready", JSON.stringify(icon.status === Image.Ready));
  console.log("RESULT invalidRaw", JSON.stringify(Notifications.State.iconSource({appName:"Zen",desktopEntry:"zen",appIcon:"does-not-exist"})));
  const source=Notifications.State.iconSource({appName:"Unknown application",appIcon:"/nonexistent/icon.png"});
  console.log("RESULT fallback", JSON.stringify(source));
  icon.source=source; fallbackLoaded.start();
 }}
 Timer { id:fallbackLoaded; interval:200; onTriggered: {
  console.log("RESULT ready", JSON.stringify(icon.status === Image.Ready));
  console.log("RESULT DONE", "true"); Qt.quit();
 }}
}''')
        results, log = harness.run(env={'XDG_DATA_HOME': str(data), 'XDG_DATA_DIRS': str(data)})
        self.assertIn('zen-browser', results['zen'][0], log)
        self.assertEqual(results['zen'], results['invalidRaw'], log)
        self.assertTrue(results['fallback'][0].endswith('/assets/bell.svg'), log)
        self.assertEqual(results['ready'], [True, True], log)
        self.assertNotIn('Could not load icon', log)
