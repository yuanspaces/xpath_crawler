$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = "python"
$appEntry = Join-Path $projectRoot "app.py"
$distDir = Join-Path $projectRoot "dist"
$buildDir = Join-Path $projectRoot "build"
$specFile = Join-Path $projectRoot "XPathCrawler.spec"
$outputName = "XPathCrawler"
$outputDir = Join-Path $distDir $outputName

Write-Host "Installing/Upgrading PyInstaller..." -ForegroundColor Cyan
& $python -m pip install --upgrade pyinstaller | Out-Host

Write-Host "Cleaning previous build output..." -ForegroundColor Cyan
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
if (Test-Path $outputDir) { Remove-Item $outputDir -Recurse -Force }
if (Test-Path $specFile) { Remove-Item $specFile -Force }

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", $outputName,
    "--add-data", "templates;templates",
    "--add-data", "static;static",
    "--collect-submodules", "selenium",
    "--collect-submodules", "urllib3",
    "--hidden-import", "webdriver_manager",
    $appEntry
)

Write-Host "Building executable..." -ForegroundColor Cyan
& $python @pyinstallerArgs | Out-Host

if (-not (Test-Path $outputDir)) {
    throw "Build failed: output folder not found: $outputDir"
}

$configSource = Join-Path $projectRoot "saved_config.ini"
$configTarget = Join-Path $outputDir "saved_config.ini"
if (Test-Path $configSource) {
    Copy-Item $configSource $configTarget -Force
    Write-Host "Copied saved_config.ini to output." -ForegroundColor Green
}

Write-Host ""
Write-Host "Build completed." -ForegroundColor Green
Write-Host "Executable folder: $outputDir" -ForegroundColor Green
Write-Host "Run: $outputDir\\$outputName.exe" -ForegroundColor Green
