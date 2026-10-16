using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("OA阅答 安装程序")]
[assembly: AssemblyProduct("OA阅答")]
[assembly: AssemblyVersion("1.0.1.0")]
[assembly: AssemblyFileVersion("1.0.1.0")]

internal static class Installer
{
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("OAYUEDA1");

    [STAThread]
    private static void Main()
    {
        string target = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "OA阅答");
        if (MessageBox.Show("将 OA阅答 v1.0.1 安装到：\n" + target + "\n\n继续安装吗？", "安装 OA阅答", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
            return;

        string temporaryZip = Path.Combine(Path.GetTempPath(), "OA阅答-" + Guid.NewGuid().ToString("N") + ".zip");
        string staging = target + ".installing";
        try
        {
            ExtractPayload(temporaryZip);
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
            ZipFile.ExtractToDirectory(temporaryZip, staging);
            if (Directory.Exists(target)) Directory.Delete(target, true);
            Directory.Move(staging, target);
            CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "OA阅答.lnk"), Path.Combine(target, "OA阅答.exe"));
            CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "OA阅答.lnk"), Path.Combine(target, "OA阅答.exe"));
            RegisterUninstall(target);
            MessageBox.Show("OA阅答 v1.0.1 已安装完成。", "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Information);
            Process.Start(new ProcessStartInfo(Path.Combine(target, "OA阅答.exe")) { UseShellExecute = true, WorkingDirectory = target });
        }
        catch (Exception error)
        {
            MessageBox.Show("安装失败：" + error.Message, "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            try { if (File.Exists(temporaryZip)) File.Delete(temporaryZip); } catch { }
            try { if (Directory.Exists(staging)) Directory.Delete(staging, true); } catch { }
        }
    }

    private static void ExtractPayload(string destination)
    {
        string self = Assembly.GetExecutingAssembly().Location;
        using (var input = File.OpenRead(self))
        {
            if (input.Length < 16) throw new InvalidDataException("安装包缺少应用数据。");
            input.Seek(-16, SeekOrigin.End);
            var lengthBytes = new byte[8];
            var magic = new byte[8];
            input.Read(lengthBytes, 0, 8);
            input.Read(magic, 0, 8);
            if (Encoding.ASCII.GetString(magic) != "OAYUEDA1") throw new InvalidDataException("安装包数据校验失败。");
            long payloadLength = BitConverter.ToInt64(lengthBytes, 0);
            if (payloadLength <= 0 || payloadLength > input.Length - 16) throw new InvalidDataException("安装包数据长度无效。");
            input.Seek(input.Length - 16 - payloadLength, SeekOrigin.Begin);
            using (var output = File.Create(destination))
            {
                var buffer = new byte[1024 * 1024];
                long remaining = payloadLength;
                while (remaining > 0)
                {
                    int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                    if (read <= 0) throw new EndOfStreamException();
                    output.Write(buffer, 0, read);
                    remaining -= read;
                }
            }
        }
    }

    private static void CreateShortcut(string shortcutPath, string executable)
    {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        dynamic shell = Activator.CreateInstance(shellType);
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = executable;
        shortcut.WorkingDirectory = Path.GetDirectoryName(executable);
        shortcut.IconLocation = executable + ",0";
        shortcut.Save();
    }

    private static void RegisterUninstall(string target)
    {
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\OA阅答"))
        {
            key.SetValue("DisplayName", "OA阅答");
            key.SetValue("DisplayVersion", "1.0.1");
            key.SetValue("Publisher", "OA阅答");
            key.SetValue("InstallLocation", target);
            key.SetValue("DisplayIcon", Path.Combine(target, "OA阅答.exe"));
            key.SetValue("UninstallString", "\"" + Path.Combine(target, "卸载OA阅答.exe") + "\"");
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        }
    }
}
