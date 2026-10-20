<#
.SYNOPSIS
    构建 CampusLink 的 Windows 可执行文件。

.DESCRIPTION
    优先使用项目内的 .venv；不存在则退回系统 python。
    输出：dist\CampusLink.exe
    本脚本不修改系统设置、不写注册表、不安装服务。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Warning '未找到项目虚拟环境 .venv，改用系统 python。'
    $python = 'python'
}

Push-Location $root
try {
    if ($Clean) {
        foreach ($dir in @('build', 'dist')) {
            $target = Join-Path $root $dir
            if (Test-Path $target) { Remove-Item -Recurse -Force $target }
        }
    }

    Write-Output "使用解释器: $python"
    & $python -m PyInstaller --noconfirm --clean campuslink.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败。' }

    $exe = Join-Path $root 'dist\CampusLink.exe'
    if (-not (Test-Path $exe)) { throw "构建结束但未找到 $exe" }

    $size = (Get-Item $exe).Length
    Write-Output ('构建成功: ' + $exe + ' (' + [math]::Round($size / 1MB, 1) + ' MB)')
}
finally {
    Pop-Location
}
