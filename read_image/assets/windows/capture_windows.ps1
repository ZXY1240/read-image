[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class DpiAware {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
  public static void Enable() {
    try {
      SetProcessDpiAwarenessContext(new IntPtr(-4));
      return;
    } catch {}
    try {
      SetProcessDpiAwareness(2);
      return;
    } catch {}
    SetProcessDPIAware();
  }
}
public class WinCap {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  public static IntPtr Found = IntPtr.Zero;
  public static string Search = "";
  public struct RECT { public int Left, Top, Right, Bottom; }
  public static bool Callback(IntPtr hWnd, IntPtr lParam) {
    if (!IsWindowVisible(hWnd)) return true;
    var sb = new StringBuilder(512);
    GetWindowText(hWnd, sb, 512);
    if (sb.ToString().ToLower().Contains(Search.ToLower())) {
      Found = hWnd;
      return false;
    }
    return true;
  }
  public static IntPtr Find(string s) {
    Search = s;
    Found = IntPtr.Zero;
    EnumWindows(Callback, IntPtr.Zero);
    return Found;
  }
  public static RECT GetBounds(IntPtr h) {
    RECT r;
    GetWindowRect(h, out r);
    return r;
  }
}
"@
[DpiAware]::Enable()

$mode = $env:WINDOWS_CAPTURE_MODE
$outputPath = $env:WINDOWS_CAPTURE_OUTPUT
$windowTitle = $env:WINDOWS_CAPTURE_WINDOW

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Test-BlankBitmap {
  param([System.Drawing.Bitmap]$bitmap)
  $first = $null
  $samples = 0
  for ($y = 0; $y -lt $bitmap.Height; $y += 50) {
    for ($x = 0; $x -lt $bitmap.Width; $x += 50) {
      $color = $bitmap.GetPixel($x, $y)
      if ($samples -eq 0) {
        $first = $color
      } elseif ($color.ToArgb() -ne $first.ToArgb()) {
        return $false
      }
      $samples++
    }
  }
  return $samples -gt 0
}

$bounds = $null
if ($mode -eq "full") {
  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
} elseif ($mode -eq "primary") {
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
} elseif ($mode -eq "window") {
  if ([string]::IsNullOrWhiteSpace($windowTitle)) {
    throw "window mode requires a window title"
  }
  $hwnd = [WinCap]::Find($windowTitle)
  if ($hwnd -eq [IntPtr]::Zero) {
    throw "Window not found: $windowTitle"
  }
  $wasIconic = [WinCap]::IsIconic($hwnd)
  if ($wasIconic) {
    [WinCap]::ShowWindow($hwnd, 9) | Out-Null
    Start-Sleep -Milliseconds 500
  }
  $rect = [WinCap]::GetBounds($hwnd)
  $bounds = New-Object System.Drawing.Rectangle($rect.Left, $rect.Top, ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top))
} else {
  throw "Unsupported mode: $mode"
}

$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
try {
  if ($mode -eq "window") {
    $hdc = $g.GetHdc()
    $ok = $false
    try {
      $ok = [WinCap]::PrintWindow($hwnd, $hdc, 2)
      if (-not $ok) {
        $ok = [WinCap]::PrintWindow($hwnd, $hdc, 1)
      }
      if (-not $ok) {
        $ok = [WinCap]::PrintWindow($hwnd, $hdc, 0)
      }
      if ($ok -and (Test-BlankBitmap $bmp)) {
        $ok = $false
      }
    } finally {
      $g.ReleaseHdc($hdc)
    }
    if (-not $ok) {
      $g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
    }
  } else {
    $g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
  }
  $bmp.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
  Write-Output $outputPath
} finally {
  $g.Dispose()
  $bmp.Dispose()
  if ($mode -eq "window" -and $wasIconic) {
    Start-Sleep -Milliseconds 200
    [WinCap]::ShowWindow($hwnd, 6) | Out-Null
  }
}
