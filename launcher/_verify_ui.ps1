Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies 'System.Drawing','System.Windows.Forms' -TypeDefinition @'
using System;
using System.Text;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  public struct RECT { public int Left, Top, Right, Bottom; }
  public static IntPtr FindChild(IntPtr parent, string text) {
    IntPtr found = IntPtr.Zero;
    EnumChildWindows(parent, delegate(IntPtr h, IntPtr l) {
      var sb = new StringBuilder(256);
      GetWindowText(h, sb, 256);
      if (sb.ToString() == text) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  public static bool Click(IntPtr h) {
    if (h == IntPtr.Zero) return false;
    SendMessage(h, 0x00F5, IntPtr.Zero, IntPtr.Zero);
    return true;
  }
  public static string Capture(IntPtr h, string path) {
    RECT r;
    if (!GetWindowRect(h, out r)) return "no-rect";
    int w = r.Right - r.Left, ht = r.Bottom - r.Top;
    if (w <= 0 || ht <= 0) return "bad-rect";
    using (var bmp = new Bitmap(w, ht))
    using (var g = Graphics.FromImage(bmp)) {
      g.CopyFromScreen(r.Left, r.Top, 0, 0, new Size(w, ht));
      bmp.Save(path, ImageFormat.Png);
    }
    return path + " -> " + w + "x" + ht;
  }
}
'@

[Cap]::SetProcessDPIAware() | Out-Null

$dir = Join-Path $env:TEMP 'stockpool-shots'
if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
New-Item -ItemType Directory -Path $dir | Out-Null

Get-Process -Name '股票池追踪系统' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
$p = Start-Process -FilePath 'c:\Users\Administrator\Desktop\stockanaly\股票池追踪系统.exe' -PassThru
Start-Sleep -Seconds 12
$p.Refresh()
$h = $p.MainWindowHandle
"窗口句柄：$h"
"--- 服务控制台 ---"
[Cap]::Capture($h, "$dir\1-service.png")
foreach ($name in @('个股查询', '运行日志', '设置')) {
  $b = [Cap]::FindChild($h, $name)
  "点击 $name : $([Cap]::Click($b))"
  Start-Sleep -Seconds 2
  [Cap]::Capture($h, "$dir\2-$name.png")
}
$b = [Cap]::FindChild($h, '服务控制台')
[Cap]::Click($b) | Out-Null
Get-ChildItem $dir | Select-Object Name, Length | Format-Table -AutoSize | Out-String
