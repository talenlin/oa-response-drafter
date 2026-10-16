using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("OA阅答")]
[assembly: AssemblyProduct("OA阅答")]
[assembly: AssemblyCompany("OA阅答")]
[assembly: AssemblyVersion("1.0.1.0")]
[assembly: AssemblyFileVersion("1.0.1.0")]
[assembly: AssemblyInformationalVersion("1.0.1")]

internal static class Launcher
{
    private const string Url = "http://127.0.0.1:8787/";

    [STAThread]
    private static void Main()
    {
        try
        {
            Run();
        }
        catch (Exception error)
        {
            string details = error.GetType().FullName;
            try { details += ": " + error.Message; } catch { }
            try
            {
                string logDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OA阅答");
                Directory.CreateDirectory(logDirectory);
                File.AppendAllText(Path.Combine(logDirectory, "launcher-error.log"), DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss") + " " + details + Environment.NewLine);
            }
            catch { }
            MessageBox.Show("启动器异常：" + details, "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static void Run()
    {
        bool alreadyReady = IsReady();
        if (alreadyReady)
        {
            OpenBrowser();
            return;
        }

        string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string appDirectory = Path.Combine(baseDirectory, "app");
        string python = Path.Combine(appDirectory, "runtime", "python", "pythonw.exe");
        string entry = Path.Combine(appDirectory, "desktop", "run_app.py");
        if (!File.Exists(python) || !File.Exists(entry))
        {
            MessageBox.Show("应用文件不完整，请重新解压或重新安装 OA阅答。", "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var start = new ProcessStartInfo
        {
            FileName = python,
            Arguments = "-X utf8 " + Quote(entry),
            WorkingDirectory = appDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        Process server;
        try
        {
            server = Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show("无法启动本机服务：" + error.Message, "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        for (int attempt = 0; attempt < 120; attempt++)
        {
            if (IsReady())
            {
                OpenBrowser();
                server.WaitForExit();
                return;
            }
            if (server.HasExited)
            {
                MessageBox.Show("本机服务启动失败。请重新安装，或检查安全软件是否拦截了 OA阅答。", "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            Thread.Sleep(500);
        }

        MessageBox.Show("本机服务启动超时，请稍后重新打开 OA阅答。", "OA阅答", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static bool IsReady()
    {
        TcpClient client = null;
        try
        {
            client = new TcpClient();
            IAsyncResult connection = client.BeginConnect("127.0.0.1", 8787, null, null);
            if (!connection.AsyncWaitHandle.WaitOne(500)) return false;
            client.EndConnect(connection);
            return client.Connected;
        }
        catch
        {
            return false;
        }
        finally
        {
            if (client != null) client.Close();
        }
    }

    private static void OpenBrowser()
    {
        Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true });
    }

}
