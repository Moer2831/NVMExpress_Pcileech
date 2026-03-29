# NVM_Express_Pcileech_FPGA_75T
# discord: Moer2831
# community: https://discord.gg/sXcQhxa8qy
# target_collect_profile.ps1
# ============================================================
# Run on TARGET PC as Administrator
# Extracts full PCI config space + BAR0 NVMe registers from NE-256
# Transfer output files to reader PC: pipeline/build_inputs/
# ============================================================
# Usage: powershell -ExecutionPolicy Bypass -File target_collect_profile.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$OutputDir = $PSScriptRoot
if (-not $OutputDir) { $OutputDir = "." }

function Write-Status($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

Write-Host "============================================================" -ForegroundColor White
Write-Host " NE-256 NVMe Full Data Extraction (Target PC)" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor White

# ---- Find the NVMe device ----
Write-Host "`n[1] Finding NE-256 NVMe device..."

$dev = Get-PnpDevice -Status OK | Where-Object {
    $_.HardwareID -match "VEN_126F" -and $_.HardwareID -match "DEV_2263"
} | Select-Object -First 1

if (-not $dev) {
    Write-Err "NE-256 NVMe (VEN_126F&DEV_2263) not found!"
    exit 1
}
Write-Ok "$($dev.FriendlyName) -- $($dev.InstanceId)"

# ---- Get BDF ----
$busNum = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_BusNumber" -ErrorAction SilentlyContinue).Data
$addr = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_Address" -ErrorAction SilentlyContinue).Data
$pciDev = if ($addr) { ($addr -shr 16) -band 0x1F } else { 0 }
$pciFun = if ($addr) { $addr -band 0x7 } else { 0 }
Write-Ok "BDF = ${busNum}:${pciDev}.${pciFun}"

# ---- Read config space via SetupAPI / cfgmgr32 ----
Write-Host "`n[2] Reading PCI config space (4KB)..."

# Method: Use the undocumented but reliable GetBusData approach
Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class PciCfg
{
    // Use NtDeviceIoControlFile to PCIBUS driver for config space read
    // Alternatively, read from mapped ECAM if available

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern SafeFileHandle CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool DeviceIoControl(
        SafeFileHandle hDevice, uint dwIoControlCode,
        byte[] lpInBuffer, int nInBufferSize,
        byte[] lpOutBuffer, int nOutBufferSize,
        out int lpBytesReturned, IntPtr lpOverlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);

    // HalGetBusData for PCI config space
    [DllImport("hal.dll", EntryPoint = "HalGetBusDataByOffset")]
    public static extern uint HalGetBusDataByOffset(
        int BusDataType, uint BusNumber, uint SlotNumber,
        byte[] Buffer, uint Offset, uint Length);

    public const int PCIConfiguration = 5;

    public static uint MakeSlotNumber(uint dev, uint fun)
    {
        return (dev & 0x1F) | ((fun & 0x7) << 5);
    }

    public static byte[] ReadConfigSpace(uint bus, uint dev, uint fun, uint offset, uint length)
    {
        byte[] buffer = new byte[length];
        uint slot = (dev << 0) | (fun << 5);  // PCI_SLOT_NUMBER: DeviceNumber | FunctionNumber<<5

        // On Windows, we can try reading from mapped config space via physical memory
        // But first try the WDF PCI bus interface approach
        return buffer;
    }
}
"@ -ErrorAction SilentlyContinue

# Primary method: Read ECAM from physical memory via RW-Everything style driver
# Most reliable: read from the PCI config space registry cache
Write-Host "  Attempting MCFG/ECAM-based config space read..."

# Find MCFG table to get ECAM base address
$mcfgBase = $null
try {
    # MCFG is an ACPI table. Search for it in firmware tables.
    $sig = [System.Text.Encoding]::ASCII.GetBytes("MCFG")

    # Read ACPI MCFG table via firmware table API
    Add-Type -TypeDefinition @"
    using System;
    using System.Runtime.InteropServices;

    public static class FirmwareTable
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint EnumSystemFirmwareTables(uint FirmwareTableProviderSignature, byte[] pFirmwareTableBuffer, uint BufferSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint GetSystemFirmwareTable(uint FirmwareTableProviderSignature, uint FirmwareTableID, byte[] pFirmwareTableBuffer, uint BufferSize);
    }
"@ -ErrorAction SilentlyContinue

    # ACPI provider = 'ACPI' = 0x41435049
    $acpiProvider = 0x41435049

    # Get size needed
    $needed = [FirmwareTable]::GetSystemFirmwareTable($acpiProvider, 0x4746434D, $null, 0)  # 'MCFG' reversed
    if ($needed -gt 0) {
        $mcfgBuf = New-Object byte[] $needed
        $read = [FirmwareTable]::GetSystemFirmwareTable($acpiProvider, 0x4746434D, $mcfgBuf, $needed)
        if ($read -gt 44) {
            # MCFG table: offset 44 = first entry, 8 bytes = base address
            $mcfgBase = [BitConverter]::ToUInt64($mcfgBuf, 44)
            Write-Ok "MCFG ECAM base: 0x$($mcfgBase.ToString('X'))"
            $startBus = $mcfgBuf[54]
            $endBus = $mcfgBuf[55]
            Write-Status "Bus range: $startBus - $endBus"
        }
    }
} catch {
    Write-Warn "MCFG read failed: $_"
}

# Calculate ECAM address for our device
$ecamAddr = $null
if ($mcfgBase) {
    $ecamAddr = $mcfgBase + ([uint64]$busNum -shl 20) + ([uint64]$pciDev -shl 15) + ([uint64]$pciFun -shl 12)
    Write-Status "Config space ECAM address: 0x$($ecamAddr.ToString('X'))"
}

# Now read config space from ECAM physical address
$configData = $null

if ($ecamAddr) {
    Write-Host "  Reading 4KB from ECAM address 0x$($ecamAddr.ToString('X'))..."

    # Use SetupAPI/DeviceIoControl to read physical memory
    # Try \\.\PhysicalMemory or RwDrv approach
    try {
        # Method: Map physical memory via kernel driver (requires admin)
        # Most Windows systems don't allow direct PhysicalMemory access from usermode
        # But RW-Everything's driver (RwDrv.sys) does if it's installed

        $rwDrvPath = "\\.\RwDrv"
        $hDev = [PciCfg]::CreateFile($rwDrvPath, 0xC0000000, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)

        if (-not $hDev.IsInvalid) {
            Write-Ok "RwDrv driver found! Reading config space..."

            # RwDrv IOCTL for physical memory read:
            # IOCTL_RWDRV_READ_PHYS = 0x222808
            $ioctl = 0x222808

            # Input: 8 bytes address + 4 bytes size
            $inBuf = New-Object byte[] 16
            [BitConverter]::GetBytes([uint64]$ecamAddr).CopyTo($inBuf, 0)
            [BitConverter]::GetBytes([uint32]4096).CopyTo($inBuf, 8)

            $outBuf = New-Object byte[] 4096
            $bytesRet = 0

            $ok = [PciCfg]::DeviceIoControl($hDev, $ioctl, $inBuf, $inBuf.Length, $outBuf, $outBuf.Length, [ref]$bytesRet, [IntPtr]::Zero)

            if ($ok -and $bytesRet -ge 4) {
                $vid = [BitConverter]::ToUInt16($outBuf, 0)
                $did = [BitConverter]::ToUInt16($outBuf, 2)

                if ($vid -eq 0x126F -and $did -eq 0x2263) {
                    $configData = $outBuf
                    Write-Ok "Config space read via RwDrv: VID=0x$($vid.ToString('X4')) DID=0x$($did.ToString('X4'))"
                } else {
                    Write-Warn "VID/DID mismatch: 0x$($vid.ToString('X4'))/0x$($did.ToString('X4'))"
                }
            } else {
                $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                Write-Warn "RwDrv IOCTL failed (error $err), trying alternative IOCTL..."

                # Try alternative IOCTL format
                $ioctl2 = 0x222004  # Some RwDrv versions use this
                $inBuf2 = New-Object byte[] 12
                [BitConverter]::GetBytes([uint64]$ecamAddr).CopyTo($inBuf2, 0)
                [BitConverter]::GetBytes([uint32]4096).CopyTo($inBuf2, 8)

                $ok2 = [PciCfg]::DeviceIoControl($hDev, $ioctl2, $inBuf2, $inBuf2.Length, $outBuf, $outBuf.Length, [ref]$bytesRet, [IntPtr]::Zero)
                if ($ok2 -and $bytesRet -ge 4) {
                    $vid = [BitConverter]::ToUInt16($outBuf, 0)
                    $did = [BitConverter]::ToUInt16($outBuf, 2)
                    if ($vid -eq 0x126F) {
                        $configData = $outBuf
                        Write-Ok "Config space read via RwDrv (alt IOCTL): VID=0x$($vid.ToString('X4'))"
                    }
                }
            }
            $hDev.Close()
        } else {
            Write-Warn "RwDrv driver not available. Trying alternative methods..."
        }
    } catch {
        Write-Warn "RwDrv method failed: $_"
    }
}

# Fallback: If RwDrv failed, instruct user to use RW-Everything GUI
if (-not $configData) {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Yellow
    Write-Host "  MANUAL EXTRACTION NEEDED:" -ForegroundColor Yellow
    Write-Host "  ============================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Open RW-Everything (as Admin) and do:" -ForegroundColor White
    Write-Host ""
    Write-Host "  CONFIG SPACE:" -ForegroundColor Cyan
    Write-Host "    1. Menu: Access -> PCI Device" -ForegroundColor White
    Write-Host "    2. Select device: Bus=$busNum Dev=$pciDev Fun=$pciFun" -ForegroundColor White
    Write-Host "       (VEN_126F DEV_2263 - NE-256 NVMe)" -ForegroundColor Gray
    Write-Host "    3. Click 'Extended' to show full 4KB config space" -ForegroundColor White
    Write-Host "    4. File -> Save -> Binary" -ForegroundColor White
    Write-Host "    5. Save as: config_space_4k.bin" -ForegroundColor White
    Write-Host ""

    if ($ecamAddr) {
        Write-Host "  ALTERNATIVE - Physical Memory Dump:" -ForegroundColor Cyan
        Write-Host "    1. Menu: Access -> Physical Memory" -ForegroundColor White
        Write-Host "    2. Address: 0x$($ecamAddr.ToString('X'))" -ForegroundColor White
        Write-Host "    3. Length: 4096 (0x1000)" -ForegroundColor White
        Write-Host "    4. Save as: config_space_4k.bin" -ForegroundColor White
        Write-Host ""
    }
}

# ---- Get BAR0 address and read NVMe registers ----
Write-Host "`n[3] Getting BAR0 address for NVMe registers..."

$bar0Addr = $null
$bar0Size = $null

# Get from config space if we have it
if ($configData) {
    $bar0Lo = [BitConverter]::ToUInt32($configData, 0x10)
    $bar0Hi = [BitConverter]::ToUInt32($configData, 0x14)
    $bar0Addr = ([uint64]$bar0Hi -shl 32) -bor ([uint64]($bar0Lo -band 0xFFFFFFF0))
    Write-Ok "BAR0 from config space: 0x$($bar0Addr.ToString('X'))"
}

# Also get from OS memory resource allocation
try {
    $memRes = Get-CimInstance Win32_AllocatedResource -ErrorAction SilentlyContinue |
        Where-Object { $_.Dependent -match "126F" -or $_.Dependent -match "2263" }
} catch {}

$osBarAddr = $null
try {
    $memAddrs = Get-CimInstance Win32_DeviceMemoryAddress -ErrorAction SilentlyContinue |
        Where-Object { $_.Dependent -match [regex]::Escape($dev.InstanceId.Replace('\','\\')) }

    if ($memAddrs) {
        foreach ($m in $memAddrs) {
            $start = $m.StartingAddress
            $end = $m.EndingAddress
            $sz = $end - $start + 1
            Write-Status "BAR: 0x$($start.ToString('X')) - 0x$($end.ToString('X')) ($([math]::Round($sz/1024))KB)"
            if (-not $osBarAddr) {
                $osBarAddr = $start
                $bar0Size = $sz
            }
        }
    }
} catch {
    Write-Warn "Could not query memory resources: $_"
}

if (-not $bar0Addr -and $osBarAddr) {
    $bar0Addr = $osBarAddr
    Write-Ok "BAR0 from OS: 0x$($bar0Addr.ToString('X'))"
}

# Try to read BAR0 NVMe registers via RwDrv
$bar0Data = $null
$readSize = if ($bar0Size) { [math]::Min($bar0Size, 16384) } else { 16384 }

if ($bar0Addr) {
    try {
        $hDev = [PciCfg]::CreateFile("\\.\RwDrv", 0xC0000000, 3, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)
        if (-not $hDev.IsInvalid) {
            $ioctl = 0x222808
            $inBuf = New-Object byte[] 16
            [BitConverter]::GetBytes([uint64]$bar0Addr).CopyTo($inBuf, 0)
            [BitConverter]::GetBytes([uint32]$readSize).CopyTo($inBuf, 8)

            $outBuf = New-Object byte[] $readSize
            $bytesRet = 0
            $ok = [PciCfg]::DeviceIoControl($hDev, $ioctl, $inBuf, $inBuf.Length, $outBuf, $outBuf.Length, [ref]$bytesRet, [IntPtr]::Zero)

            if ($ok -and $bytesRet -ge 64) {
                $bar0Data = $outBuf[0..($bytesRet-1)]
                $capLo = [BitConverter]::ToUInt32($bar0Data, 0)
                $vs = [BitConverter]::ToUInt32($bar0Data, 8)
                Write-Ok "BAR0 read: $bytesRet bytes, CAP_LO=0x$($capLo.ToString('X8')) VS=0x$($vs.ToString('X8'))"
            }
            $hDev.Close()
        }
    } catch {
        Write-Warn "BAR0 RwDrv read failed: $_"
    }

    if (-not $bar0Data) {
        Write-Host ""
        Write-Host "  BAR0 MANUAL EXTRACTION:" -ForegroundColor Yellow
        Write-Host "    1. RW-Everything: Access -> Physical Memory" -ForegroundColor White
        Write-Host "    2. Address: 0x$($bar0Addr.ToString('X'))" -ForegroundColor White
        Write-Host "    3. Length: $readSize (0x$($readSize.ToString('X')))" -ForegroundColor White
        Write-Host "    4. Save as: nvme_bar0.bin" -ForegroundColor White
    }
}

# ---- Save outputs ----
Write-Host "`n[4] Saving results..."

if ($configData) {
    $cfgPath = Join-Path $OutputDir "config_space_4k.bin"
    [System.IO.File]::WriteAllBytes($cfgPath, $configData)
    Write-Ok "Saved: $cfgPath (4096 bytes)"

    # Also save hex dump for human inspection
    $hexPath = Join-Path $OutputDir "config_space_hexdump.txt"
    $lines = @("PCI Config Space - NE-256 NVMe (BDF ${busNum}:${pciDev}.${pciFun})")
    for ($i = 0; $i -lt $configData.Length; $i += 16) {
        $hex = ($configData[$i..([math]::Min($i+15, $configData.Length-1))] | ForEach-Object { $_.ToString("X2") }) -join " "
        $lines += "{0:X4}: {1}" -f $i, $hex
    }
    $lines | Set-Content -Path $hexPath
    Write-Ok "Saved: $hexPath"
}

if ($bar0Data) {
    $barPath = Join-Path $OutputDir "nvme_bar0.bin"
    [System.IO.File]::WriteAllBytes($barPath, [byte[]]$bar0Data)
    Write-Ok "Saved: $barPath ($($bar0Data.Length) bytes)"
}

# Save device info JSON
$info = @{
    InstanceId = $dev.InstanceId
    FriendlyName = $dev.FriendlyName
    Bus = $busNum
    Device = $pciDev
    Function = $pciFun
    BDF = "{0:X2}:{1:X2}.{2}" -f $busNum, $pciDev, $pciFun
    ECAM_Address = if ($ecamAddr) { "0x$($ecamAddr.ToString('X'))" } else { $null }
    BAR0_Address = if ($bar0Addr) { "0x$($bar0Addr.ToString('X'))" } else { $null }
    BAR0_Size = $bar0Size
    ConfigExtracted = ($null -ne $configData)
    BAR0Extracted = ($null -ne $bar0Data)
}
$infoPath = Join-Path $OutputDir "device_info.json"
$info | ConvertTo-Json -Depth 3 | Set-Content -Path $infoPath -Encoding UTF8
Write-Ok "Saved: $infoPath"

# ---- Summary ----
Write-Host "`n============================================================" -ForegroundColor White
Write-Host " EXTRACTION SUMMARY" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor White
Write-Host "  Config space 4KB: $(if ($configData) { 'EXTRACTED' } else { 'MANUAL NEEDED (see above)' })" -ForegroundColor $(if ($configData) { 'Green' } else { 'Yellow' })
Write-Host "  BAR0 NVMe regs:   $(if ($bar0Data) { 'EXTRACTED' } else { 'MANUAL NEEDED (see above)' })" -ForegroundColor $(if ($bar0Data) { 'Green' } else { 'Yellow' })
Write-Host ""
Write-Host "  Transfer these files to reader PC:" -ForegroundColor White
Write-Host "    -> pipeline/build_inputs/config_space_4k.bin" -ForegroundColor Cyan
Write-Host "    -> pipeline/build_inputs/nvme_bar0.bin" -ForegroundColor Cyan
Write-Host "    -> pipeline/build_inputs/device_info.json" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Then on reader PC run:" -ForegroundColor White
Write-Host "    python 01_capture_config_profile.py   (to parse)" -ForegroundColor Gray
Write-Host "    python 04_build_init_images.py           (to generate COE)" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor White


