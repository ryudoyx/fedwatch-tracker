# FedWatch 跟踪器的 Windows 通知。daily_fetch.bat 每天早上调用：
#   -Kind digest -File <摘要文件>   抓取成功，弹出当天的变化（文件第一行是标题，其余是正文）
#   -Kind fetch_failed -Log <日志>  抓取出问题
# 故意不依赖 Python：Python 环境坏了的时候也要能报出来。
# 注意：本文件必须存成 UTF-8 带 BOM，否则 Windows PowerShell 5.1 会把中文读成乱码。
param(
    [string]$Kind = 'digest',
    [string]$File = '',
    [string]$Log = ''
)

$title = 'FedWatch 跟踪'
$text = ''
$icon = 'Info'

switch ($Kind) {
    'digest' {
        if (-not (Test-Path -LiteralPath $File)) { exit 0 }
        $lines = Get-Content -LiteralPath $File -Encoding UTF8
        if (-not $lines) { exit 0 }
        $title = $lines[0]
        $text = ($lines | Select-Object -Skip 1) -join "`n"
    }
    'fetch_failed' {
        $title = 'FedWatch 每日抓取有问题'
        $text = "今天的数据没抓全。偶尔一天没关系（下次会自动补），连续几天都这样请打开看板的「数据与方法」看原因。`n日志：$Log"
        $icon = 'Warning'
    }
    'test' {
        $title = 'FedWatch 跟踪 · 测试通知'
        $text = '每天早上的摘要通知已经配置好了。以后每天 8 点会弹出这样一条。'
    }
    default { $text = $Kind }
}

# 气泡通知有长度上限，超了 Windows 会自己截断，这里先截一刀免得截在半个字上
if ($title.Length -gt 60) { $title = $title.Substring(0, 60) }
if ($text.Length -gt 250) { $text = $text.Substring(0, 249) + '…' }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$ni = New-Object System.Windows.Forms.NotifyIcon
if ($icon -eq 'Warning') {
    $ni.Icon = [System.Drawing.SystemIcons]::Warning
    $ni.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
} else {
    $ni.Icon = [System.Drawing.SystemIcons]::Information
    $ni.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
}
$ni.BalloonTipTitle = $title
$ni.BalloonTipText = $text
$ni.Visible = $true
$ni.ShowBalloonTip(20000)
Start-Sleep -Seconds 20
$ni.Dispose()
