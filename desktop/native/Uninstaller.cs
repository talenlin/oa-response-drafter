using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("卸载 OA阅答")]
[assembly: AssemblyVersion("1.0.1.0")]
[assembly: AssemblyFileVersion("1.0.1.0")]

internal static class Uninstaller
{
    [STAThread]
    private static void Main()
    {
        if (MessageBox.Show("确定要卸载 OA阅答 v1.0.1 吗？\n\n本地案件存档和登录资料不会被删除。", "卸载 OA阅答", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
            return;

        string installDirectory = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string cleanup = Path.Combine(Path.GetTempPath(), "OA阅答-卸载-" + Guid.NewGuid().ToString("N") + ".cmd");
        File.WriteAllText(cleanup,
            "@echo off\r\n" +
            "timeout /t 2 /nobreak >nul\r\n" +
            "rmdir /s /q \"" + installDirectory + "\"\r\n" +
            "del /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\OA阅答.lnk\" 2>nul\r\n" +
            "del /q \"%USERPROFILE%\\Desktop\\OA阅答.lnk\" 2>nul\r\n" +
            "del /q \"%~f0\"\r\n",
            System.Text.Encoding.Default);
        Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\OA阅答", false);
        Process.Start(new ProcessStartInfo("cmd.exe", "/c \"" + cleanup + "\"") { CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden });
    }
}
