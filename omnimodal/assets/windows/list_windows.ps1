[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class WinList {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  public static List<string> Titles = new List<string>();
  public static bool Callback(IntPtr hWnd, IntPtr lParam) {
    if (IsWindowVisible(hWnd)) {
      var sb = new StringBuilder(512);
      GetWindowText(hWnd, sb, 512);
      if (sb.Length > 0 && !sb.ToString().Contains("Windows Shell Experience Host")) {
        Titles.Add(sb.ToString());
      }
    }
    return true;
  }
  public static string[] Run() {
    Titles.Clear();
    EnumWindows(Callback, IntPtr.Zero);
    return Titles.ToArray();
  }
}
"@
[WinList]::Run() | Sort-Object -Unique
