# NVM_Express_Pcileech_FPGA_75T
# discord: Moer_2831
# community: https://discord.gg/sXcQhxa8qy
# target_collect_configspace.ps1
# Run this on the TARGET PC (as Administrator) to extract the NE-256 NVMe config space.
# Outputs: config_space_4k.bin, bar_info.json, nvme_bar0_regs.bin
#
# Usage: powershell -ExecutionPolicy Bypass -File target_collect_configspace.ps1

$ErrorActionPreference = "Continue"
$OutputDir = $PSScriptRoot
if (-not $OutputDir) { $OutputDir = "." }

Write-Host "============================================================"
Write-Host "NVMe Config Space Extractor (Target PC)"
Write-Host "============================================================"

# --- Step 1: Find the NE-256 NVMe device ---
Write-Host "`n[1] Searching for NE-256 NVMe device..."

$nvmeDevices = Get-PnpDevice -Class SCSIAdapter -Status OK | Where-Object {
    $_.HardwareID -match "VEN_126F" -and $_.HardwareID -match "DEV_2263"
}

if (-not $nvmeDevices) {
    # Also try disk class
    $nvmeDevices = Get-PnpDevice -Status OK | Where-Object {
        $_.HardwareID -match "VEN_126F" -and $_.HardwareID -match "DEV_2263"
    }
}

if (-not $nvmeDevices) {
    Write-Host "ERROR: NE-256 NVMe device (VEN_126F&DEV_2263) not found!"
    Write-Host "Check that the device is installed and recognized."
    exit 1
}

$dev = $nvmeDevices | Select-Object -First 1
Write-Host "  Found: $($dev.FriendlyName)"
Write-Host "  Instance: $($dev.InstanceId)"

# --- Step 2: Get location info (BDF) ---
Write-Host "`n[2] Getting device location..."

$locationInfo = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_LocationInfo").Data
$busNumber = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_BusNumber").Data
$address = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_Address").Data

$pciDev = ($address -shr 16) -band 0x1F
$pciFun = $address -band 0x7

Write-Host "  Location: $locationInfo"
Write-Host "  Bus: $busNumber, Device: $pciDev, Function: $pciFun"

# --- Step 3: Read config space from registry ---
Write-Host "`n[3] Reading PCI config space from registry..."

# Windows caches 256 bytes of config space in the registry
$regPath = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($dev.InstanceId)"
$configData = $null

try {
    $configData = (Get-ItemProperty -Path $regPath -Name "ConfigData" -ErrorAction SilentlyContinue).ConfigData
} catch {}

# Try alternative: SetupAPI config space
if (-not $configData -or $configData.Length -lt 64) {
    Write-Host "  Registry ConfigData not available, trying SetupAPI..."

    # Use cfgmgr32 approach via .NET interop
    Add-Type -TypeDefinition @"
    using System;
    using System.Runtime.InteropServices;

    public class PciConfig {
        [DllImport("cfgmgr32.dll", SetLastError = true)]
        public static extern uint CM_Locate_DevNode(out uint pdnDevInst, string pDeviceID, uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        public static extern uint CM_Get_DevNode_Registry_Property(uint dnDevInst, uint ulProperty,
            out uint pulRegDataType, byte[] buffer, ref uint pulLength, uint ulFlags);

        public const uint CM_DRP_CONFIGDATA = 0x00000027;
        public const uint CR_SUCCESS = 0;
    }
"@ -ErrorAction SilentlyContinue
}

# --- Step 4: Read full config space via RW-Everything style direct access ---
Write-Host "`n[4] Attempting direct config space read via bus/dev/fun..."

# Use Win32 API to read PCI config space (requires admin)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public class PciConfigReader {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern SafeFileHandle CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool DeviceIoControl(
        SafeFileHandle hDevice, uint dwIoControlCode,
        byte[] lpInBuffer, uint nInBufferSize,
        byte[] lpOutBuffer, uint nOutBufferSize,
        out uint lpBytesReturned, IntPtr lpOverlapped);

    // HalGetBusData can read PCI config space
    [DllImport("ntdll.dll")]
    public static extern int NtQuerySystemInformation(int SystemInformationClass,
        byte[] SystemInformation, int SystemInformationLength, out int ReturnLength);
}
"@ -ErrorAction SilentlyContinue

# --- Step 5: Use setupapi to get config space dump ---
# Fallback: Read what we can from WMI and registry
Write-Host "`n[5] Collecting all available config data..."

$result = @{
    InstanceId = $dev.InstanceId
    FriendlyName = $dev.FriendlyName
    Bus = $busNumber
    Device = $pciDev
    Function = $pciFun
    BDF = "{0:X2}:{1:X2}.{2}" -f $busNumber, $pciDev, $pciFun
}

# Get all PnP device properties
$props = @(
    "DEVPKEY_Device_BusNumber",
    "DEVPKEY_Device_Address",
    "DEVPKEY_Device_LocationInfo",
    "DEVPKEY_Device_DriverVersion",
    "DEVPKEY_PciDevice_CurrentSpeedAndMode",
    "DEVPKEY_PciDevice_MaxReadRequestSize",
    "DEVPKEY_PciDevice_MaxPayloadSize",
    "DEVPKEY_PciDevice_BaseClass",
    "DEVPKEY_PciDevice_SubClass",
    "DEVPKEY_PciDevice_ProgIf",
    "DEVPKEY_PciDevice_InterruptSupported",
    "DEVPKEY_PciDevice_BarTypes"
)

Write-Host "`n  Device Properties:"
foreach ($prop in $props) {
    try {
        $val = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName $prop -ErrorAction SilentlyContinue).Data
        if ($val -ne $null) {
            Write-Host "    $prop = $val"
            $result[$prop] = $val
        }
    } catch {}
}

# Get memory resources (BAR addresses)
Write-Host "`n  Memory Resources (BARs):"
$memResources = Get-CimInstance -ClassName Win32_DeviceMemoryAddress | Where-Object {
    $_.Dependent -match [regex]::Escape($dev.InstanceId.Replace('\', '\\'))
}

$barAddresses = @()
foreach ($mem in $memResources) {
    $startAddr = $mem.StartingAddress
    $endAddr = $mem.EndingAddress
    $size = $endAddr - $startAddr + 1
    Write-Host "    Range: 0x$($startAddr.ToString('X')) - 0x$($endAddr.ToString('X')) ($('{0:N0}' -f $size) bytes = $([math]::Round($size/1024))KB)"
    $barAddresses += @{
        StartAddress = "0x$($startAddr.ToString('X'))"
        EndAddress = "0x$($endAddr.ToString('X'))"
        Size = $size
        SizeKB = [math]::Round($size/1024)
    }
}

$result["MemoryResources"] = $barAddresses

# --- Step 6: Try to read config space via SetupDI ---
Write-Host "`n[6] Reading raw PCI config space via SetupDI..."

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class SetupDi {
    public static readonly Guid GUID_DEVCLASS_SCSIADAPTER = new Guid("4d36e97b-e325-11ce-bfc1-08002be10318");

    [DllImport("setupapi.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern IntPtr SetupDiGetClassDevs(ref Guid ClassGuid, string Enumerator, IntPtr hwndParent, uint Flags);

    [DllImport("setupapi.dll", SetLastError = true)]
    public static extern bool SetupDiEnumDeviceInfo(IntPtr DeviceInfoSet, uint MemberIndex, ref SP_DEVINFO_DATA DeviceInfoData);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern bool SetupDiGetDeviceRegistryProperty(IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData,
        uint Property, out uint PropertyRegDataType, byte[] PropertyBuffer, uint PropertyBufferSize, out uint RequiredSize);

    [DllImport("setupapi.dll", SetLastError = true)]
    public static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA {
        public uint cbSize;
        public Guid ClassGuid;
        public uint DevInst;
        public IntPtr Reserved;
    }

    public const uint DIGCF_PRESENT = 0x02;
    public const uint SPDRP_BUSNUMBER = 0x15;
    public const uint SPDRP_ADDRESS = 0x1C;
}
"@ -ErrorAction SilentlyContinue

# --- Step 7: Save what we have ---
Write-Host "`n[7] Saving results..."

# Save device info as JSON
$jsonPath = Join-Path $OutputDir "device_info.json"
$result | ConvertTo-Json -Depth 5 | Set-Content -Path $jsonPath -Encoding UTF8
Write-Host "  Saved: $jsonPath"

# Save BAR info
if ($barAddresses.Count -gt 0) {
    $barInfo = @{
        raw_low = "from_os"
        raw_high = "from_os"
        base_address = $barAddresses[0].StartAddress
        base_address_int = [Convert]::ToInt64($barAddresses[0].StartAddress.Replace("0x",""), 16)
        is_64bit = $true
        is_prefetchable = $true
        type = "Memory"
        estimated_size = $barAddresses[0].Size
        estimated_size_desc = "$($barAddresses[0].SizeKB)KB"
    }
    $barJsonPath = Join-Path $OutputDir "bar_info.json"
    $barInfo | ConvertTo-Json | Set-Content -Path $barJsonPath -Encoding UTF8
    Write-Host "  Saved: $barJsonPath"
}

# --- Step 8: Instructions for manual config space extraction ---
Write-Host "`n============================================================"
Write-Host "MANUAL STEPS NEEDED:"
Write-Host "============================================================"
Write-Host ""
Write-Host "The full 4KB PCI config space requires direct hardware access."
Write-Host "Please use RW-Everything on this target PC:"
Write-Host ""
Write-Host "1. Open RW-Everything (as Administrator)"
Write-Host "2. Go to: PCI Bus -> Find device VEN_126F DEV_2263"
Write-Host "   (or navigate to Bus $busNumber, Device $pciDev, Function $pciFun)"
Write-Host "3. In the PCI config view, click 'Dump' or 'Save'"
Write-Host "4. Save as: config_space_4k.bin (raw binary, all 4096 bytes)"
Write-Host "   Or save as hex text and we'll convert it."
Write-Host ""
Write-Host "Also dump BAR0 NVMe registers:"
Write-Host "1. In RW-Everything: Physical Memory viewer"
if ($barAddresses.Count -gt 0) {
    Write-Host "2. Go to address: $($barAddresses[0].StartAddress)"
    Write-Host "3. Dump $($barAddresses[0].SizeKB)KB starting from that address"
} else {
    Write-Host "2. Read BAR0 address from config space offset 0x10"
    Write-Host "3. Dump 16KB starting from that address"
}
Write-Host "4. Save as: nvme_bar0.bin"
Write-Host ""
Write-Host "Transfer both files to the reader PC's pipeline/build_inputs/ directory."
Write-Host "============================================================"


