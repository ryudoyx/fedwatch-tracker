# FedWatch 跟踪器的 Windows 通知。daily_fetch.bat 抓取失败时调用。
# 故意不依赖 Python：Python 环境坏了的时候也要能报出来。
# 注意：本文件必须存成 UTF-8 带 BOM，否则 Windows PowerShell 5.1 会把中文读成乱码。
param(
    [string]$Kind = 'fetch_failed',
    [string]$Log = ''
)

$messages = @{
    fetch_failed = @(
        'FedWatch 每日抓取有问题',
        "今天的数据没抓全。偶尔一天没关系（下次会自动补），连续几天都这样请打开看板的「数据与方法」看原因。`n日志：$Log"
    )
    test = @(
        'FedWatch 跟踪 · 测试通知',
        '每日抓取失败提醒已经配置好了。以后抓取出错会弹出这样的通知。'
    )
}
$title, $text = $messages[$Kind]
if (-not $title) { $title = 'FedWatch 跟踪'; $text = $Kind }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Warning
$icon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
$icon.BalloonTipTitle = $title
$icon.BalloonTipText = $text
$icon.Visible = $true
$icon.ShowBalloonTip(15000)
Start-Sleep -Seconds 15
$icon.Dispose()
