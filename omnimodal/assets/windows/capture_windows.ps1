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
  [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
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
    if (!GetWindowRect(h, out r)) {
      int err = Marshal.GetLastWin32Error();
      throw new Exception("GetWindowRect failed (Win32 error " + err + ") for hwnd " + h);
    }
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
  # 采样步长可经环境变量 WINDOWS_CAPTURE_SAMPLE_STEP 覆盖（默认 50），非法值回退默认
  $sampleStep = 50
  $rawStep = $env:WINDOWS_CAPTURE_SAMPLE_STEP
  if ($rawStep) {
    $parsed = 0
    if ([int]::TryParse($rawStep, [ref]$parsed) -and $parsed -ge 1) {
      $sampleStep = $parsed
    }
  }
  for ($y = 0; $y -lt $bitmap.Height; $y += $sampleStep) {
    for ($x = 0; $x -lt $bitmap.Width; $x += $sampleStep) {
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
    # ShowWindow 返回"窗口之前是否可见"，非成败标志；记录以便排查
    $prevVisible = [WinCap]::ShowWindow($hwnd, 9)
    Write-Warning "[CAPTURE-DEBUG] ShowWindow(SW_RESTORE=9) hwnd=$hwnd prevVisible=$prevVisible"
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
      Write-Warning "[CAPTURE-DEBUG] PrintWindow flag=2 ok=$ok"
      if (-not $ok) {
        $ok = [WinCap]::PrintWindow($hwnd, $hdc, 1)
        Write-Warning "[CAPTURE-DEBUG] PrintWindow flag=1 ok=$ok"
      }
      if (-not $ok) {
        $ok = [WinCap]::PrintWindow($hwnd, $hdc, 0)
        Write-Warning "[CAPTURE-DEBUG] PrintWindow flag=0 ok=$ok"
      }
      if ($ok -and (Test-BlankBitmap $bmp)) {
        $ok = $false
        Write-Warning "[CAPTURE-DEBUG] PrintWindow result blank, need fallback"
      }
    } finally {
      $g.ReleaseHdc($hdc)
    }
    if (-not $ok) {
      Write-Warning "[CAPTURE-DEBUG] Falling back to CopyFromScreen"
      try {
        $g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
      } catch {
        throw "CopyFromScreen fallback failed: $($_.Exception.Message)"
      }
    }
  } else {
    try {
      $g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
    } catch {
      throw "CopyFromScreen failed ($mode): $($_.Exception.Message)"
    }
  }
  try {
    $bmp.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
  } catch {
    throw "Bitmap.Save failed: $outputPath : $($_.Exception.Message)"
  }
  Write-Output $outputPath
} finally {
  $g.Dispose()
  $bmp.Dispose()
  if ($mode -eq "window" -and $wasIconic) {
    Start-Sleep -Milliseconds 200
    $prevVisible = [WinCap]::ShowWindow($hwnd, 6)
    Write-Warning "[CAPTURE-DEBUG] ShowWindow(SW_MINIMIZE=6) hwnd=$hwnd prevVisible=$prevVisible"
  }
}
