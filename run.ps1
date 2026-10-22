param(
    [string]$Qemu = "qemu-system-x86_64",
    [string]$UsbImage,
    [string]$TftpRoot,
    [string]$ArtifactDirectory,
    [ValidateSet("virtio-net-pci", "e1000")][string]$NetworkAdapter = "virtio-net-pci",
    [switch]$Fresh,
    [switch]$NoSecureBoot
)
$ErrorActionPreference = "Stop"
$artifactDir = if ($ArtifactDirectory) { $ArtifactDirectory } else {
    Join-Path (Split-Path -Parent $PSScriptRoot) "out\carbon\boot"
}
$templateFlash = Join-Path $artifactDir "flash-trusted.img"
$flash = Join-Path $artifactDir "flash-interactive.img"
$firmware = Join-Path $artifactDir "OVMF_CODE_4M.secboot.fd"
$templateVars = Join-Path $artifactDir "OVMF_VARS-trusted.fd"
$sessionVars = Join-Path $artifactDir "OVMF_VARS-interactive.fd"
if ($NoSecureBoot) {
    $templateFlash = Join-Path $artifactDir "flash-optional-firmware.img"
    $templateVars = Join-Path $artifactDir "OVMF_VARS-optional-firmware.fd"
    $flash = Join-Path $artifactDir "flash-interactive-nosb.img"
    $sessionVars = Join-Path $artifactDir "OVMF_VARS-interactive-nosb.fd"
}
foreach ($file in @($templateFlash, $firmware, $templateVars)) {
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Missing $file. Run .\boot\build.ps1 first."
    }
}
if ($Fresh -or -not (Test-Path -LiteralPath $sessionVars)) {
    Copy-Item -LiteralPath $templateVars -Destination $sessionVars -Force
}
if ($Fresh -or -not (Test-Path -LiteralPath $flash)) {
    Copy-Item -LiteralPath $templateFlash -Destination $flash -Force
}
Write-Host "115200 serial console. Ctrl-C interrupts autoboot; Ctrl-A B sends BREAK; Ctrl-A X exits."
Write-Host "Changes persist in flash-interactive.img. Use -Fresh to reset this VM to the exported image."
$qemuArgs = @(
    "-machine", "q35,smm=on", "-accel", "tcg", "-cpu", "max", "-m", "512",
    "-global", "driver=cfi.pflash01,property=secure,value=on",
    "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=$firmware",
    "-drive", "if=pflash,format=raw,unit=1,file=$sessionVars",
    "-drive", "if=virtio,format=raw,file=$flash",
    "-display", "none", "-serial", "mon:stdio"
)
if ($UsbImage) {
    $usbPath = (Resolve-Path -LiteralPath $UsbImage).Path
    $qemuArgs += @("-device", "qemu-xhci", "-drive", "if=none,id=recovery,format=raw,readonly=on,file=$usbPath",
                  "-device", "usb-storage,drive=recovery")
}
if ($TftpRoot) {
    $tftpPath = (Resolve-Path -LiteralPath $TftpRoot).Path.Replace("\", "/")
    $qemuArgs += @("-netdev", "user,id=recovery-net,tftp=$tftpPath",
                  "-device", "$NetworkAdapter,netdev=recovery-net")
} else {
    $qemuArgs += @("-net", "none")
}
& $Qemu @qemuArgs
if ($LASTEXITCODE -ne 0) { throw "QEMU exited with code $LASTEXITCODE" }
