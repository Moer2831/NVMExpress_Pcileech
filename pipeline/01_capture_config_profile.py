#!/usr/bin/env python3
"""
NVM_Express_Pcileech_FPGA_75T
discord: Moer2831
community: https://discord.gg/sXcQhxa8qy

Extract full 4KB PCI Extended Config Space from the reference NE-256 NVMe SSD via ECAM DMA read.

Uses the PCILeech DMA device (via memprocfs/leechcore) to read the ECAM-mapped
configuration space of the reference NVMe controller.

Usage:
    python 01_capture_config_profile.py
    python 01_capture_config_profile.py --bus 5 --dev 0 --fun 0
    python 01_capture_config_profile.py --ecam-base 0xE0000000
    python 01_capture_config_profile.py --scan   # Scan all buses for VEN_126F/DEV_2263
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_INPUTS_DIR = os.path.join(SCRIPT_DIR, "build_inputs")

# Target device IDs
TARGET_VID = 0x126F   # Silicon Motion
TARGET_DID = 0x2263   # SM2263
TARGET_REV = 0x03

# Common ECAM base addresses for Intel platforms
ECAM_BASES = [0xE0000000, 0xC0000000, 0xF0000000, 0x80000000]

# Known BDF from previous extraction
DEFAULT_BUS = 5
DEFAULT_DEV = 0
DEFAULT_FUN = 0


def ecam_address(base, bus, dev, fun):
    """Compute ECAM physical address for a BDF."""
    return base + (bus << 20) | (dev << 15) | (fun << 12)


def read_physical_memory(addr, size):
    """Read physical memory via available DMA method. Returns bytes or None."""

    # Method 1: memprocfs Python API
    try:
        import memprocfs
        vmm = memprocfs.Vmm(['-device', 'fpga'])
        data = vmm.memory.read(addr, size)
        vmm.close()
        if data and len(data) == size:
            print(f"  [memprocfs] Read {size} bytes at 0x{addr:X}")
            return bytes(data)
    except Exception as e:
        print(f"  [memprocfs] Failed: {e}")

    # Method 2: leechcorepyc
    try:
        import leechcorepyc
        lc = leechcorepyc.LeechCore('-device fpga')
        data = lc.read(addr, size)
        lc.close()
        if data and len(data) == size:
            print(f"  [leechcore] Read {size} bytes at 0x{addr:X}")
            return bytes(data)
    except Exception as e:
        print(f"  [leechcore] Failed: {e}")

    # Method 3: pcileech.exe command line
    try:
        tmpfile = os.path.join(tempfile.gettempdir(), "pcileech_dump.bin")
        cmd = [
            "pcileech.exe", "dump",
            "-min", f"0x{addr:X}",
            "-max", f"0x{addr + size:X}",
            "-out", tmpfile,
            "-force"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if os.path.exists(tmpfile):
            with open(tmpfile, "rb") as f:
                data = f.read()
            os.unlink(tmpfile)
            if len(data) == size:
                print(f"  [pcileech.exe] Read {size} bytes at 0x{addr:X}")
                return data
    except Exception as e:
        print(f"  [pcileech.exe] Failed: {e}")

    return None


def find_ecam_base():
    """Try common ECAM bases to find one that has valid PCI devices."""
    print("Searching for ECAM base address...")
    for base in ECAM_BASES:
        data = read_physical_memory(base, 4)
        if data is None:
            continue
        vid = struct.unpack_from("<H", data, 0)[0]
        did = struct.unpack_from("<H", data, 2)[0]
        if vid != 0xFFFF and vid != 0x0000:
            print(f"  Found valid device at ECAM base 0x{base:X}: VID=0x{vid:04X} DID=0x{did:04X}")
            return base
    return None


def scan_for_device(ecam_base, target_vid=TARGET_VID, target_did=TARGET_DID):
    """Scan all buses/devices/functions for target VID/DID."""
    print(f"Scanning for VID=0x{target_vid:04X} DID=0x{target_did:04X}...")
    found = []
    for bus in range(256):
        for dev in range(32):
            addr = ecam_address(ecam_base, bus, dev, 0)
            data = read_physical_memory(addr, 4)
            if data is None:
                continue
            vid = struct.unpack_from("<H", data, 0)[0]
            did = struct.unpack_from("<H", data, 2)[0]
            if vid == 0xFFFF or vid == 0x0000:
                continue
            if vid == target_vid and did == target_did:
                print(f"  FOUND at BDF {bus:02X}:{dev:02X}.0")
                found.append((bus, dev, 0))
            # Check multi-function
            header_type = data[3] if len(data) > 3 else 0
            if header_type & 0x80:
                for fun in range(1, 8):
                    addr = ecam_address(ecam_base, bus, dev, fun)
                    data = read_physical_memory(addr, 4)
                    if data is None:
                        continue
                    vid = struct.unpack_from("<H", data, 0)[0]
                    did = struct.unpack_from("<H", data, 2)[0]
                    if vid == target_vid and did == target_did:
                        print(f"  FOUND at BDF {bus:02X}:{dev:02X}.{fun}")
                        found.append((bus, dev, fun))
    return found


PCI_CAP_NAMES = {
    0x01: "Power Management",
    0x02: "AGP",
    0x03: "VPD",
    0x04: "Slot ID",
    0x05: "MSI",
    0x06: "CompactPCI Hot Swap",
    0x07: "PCI-X",
    0x08: "HyperTransport",
    0x09: "Vendor Specific",
    0x0A: "Debug Port",
    0x0B: "CompactPCI Central Resource",
    0x0C: "PCI Hot-Plug",
    0x0D: "PCI Bridge Subsystem VID",
    0x0E: "AGP 8x",
    0x0F: "Secure Device",
    0x10: "PCI Express",
    0x11: "MSI-X",
    0x12: "SATA Data/Index Configuration",
    0x13: "AF (Advanced Features)",
    0x14: "Enhanced Allocation",
    0x15: "FPB (Flattening Portal Bridge)",
}

PCIE_EXT_CAP_NAMES = {
    0x0001: "AER (Advanced Error Reporting)",
    0x0002: "VC (Virtual Channel)",
    0x0003: "DSN (Device Serial Number)",
    0x0004: "Power Budgeting",
    0x0005: "RCLD (Root Complex Link Declaration)",
    0x0006: "RCILC (Root Complex Internal Link Control)",
    0x0007: "RCES (Root Complex Event Collector)",
    0x0008: "MFVC (Multi-Function Virtual Channel)",
    0x0009: "VC (Virtual Channel) 9",
    0x000A: "RCRB",
    0x000B: "Vendor Specific Extended",
    0x000C: "CAC (Configuration Access Correlation)",
    0x000D: "ACS (Access Control Services)",
    0x000E: "ARI (Alternative Routing-ID)",
    0x000F: "ATS (Address Translation Services)",
    0x0010: "SR-IOV",
    0x0011: "MR-IOV",
    0x0012: "Multicast",
    0x0013: "PRI (Page Request Interface)",
    0x0015: "Resizable BAR",
    0x0016: "DPA (Dynamic Power Allocation)",
    0x0017: "TPH Requester",
    0x0018: "LTR (Latency Tolerance Reporting)",
    0x0019: "Secondary PCIe",
    0x001A: "PMUX (Protocol Multiplexing)",
    0x001B: "PASID",
    0x001C: "LNR (LN Requester)",
    0x001D: "DPC (Downstream Port Containment)",
    0x001E: "L1 PM Substates",
    0x001F: "PTM (Precision Time Measurement)",
    0x0020: "PCI Express over M-PHY",
    0x0021: "FRS Queuing",
    0x0022: "Readiness Time Reporting",
    0x0023: "Designated Vendor-Specific",
    0x0024: "VF Resizable BAR",
    0x0025: "Data Link Feature",
    0x0026: "Physical Layer 16.0 GT/s",
    0x0027: "Lane Margining at Receiver",
    0x0028: "Hierarchy ID",
}


def parse_pci_capabilities(config_data):
    """Walk PCI capability linked list and parse each capability."""
    caps = []
    cap_ptr = config_data[0x34]
    visited = set()

    while cap_ptr and cap_ptr != 0xFF and cap_ptr not in visited:
        visited.add(cap_ptr)
        if cap_ptr + 2 > len(config_data):
            break
        cap_id = config_data[cap_ptr]
        next_ptr = config_data[cap_ptr + 1]
        cap_name = PCI_CAP_NAMES.get(cap_id, f"Unknown(0x{cap_id:02X})")

        cap_info = {
            "offset": f"0x{cap_ptr:02X}",
            "offset_int": cap_ptr,
            "id": f"0x{cap_id:02X}",
            "id_int": cap_id,
            "name": cap_name,
            "next": f"0x{next_ptr:02X}",
        }

        # Parse capability-specific fields
        if cap_id == 0x01:  # PM
            pmc = struct.unpack_from("<H", config_data, cap_ptr + 2)[0]
            pmcsr = struct.unpack_from("<H", config_data, cap_ptr + 4)[0]
            cap_info["PMC"] = f"0x{pmc:04X}"
            cap_info["PMCSR"] = f"0x{pmcsr:04X}"
            cap_info["PM_version"] = pmc & 0x7
            cap_info["D1_support"] = bool(pmc & (1 << 9))
            cap_info["D2_support"] = bool(pmc & (1 << 10))
            cap_info["size_bytes"] = 8

        elif cap_id == 0x05:  # MSI
            mc = struct.unpack_from("<H", config_data, cap_ptr + 2)[0]
            cap_info["MC"] = f"0x{mc:04X}"
            cap_info["MSI_enable"] = bool(mc & 1)
            cap_info["multiple_msg_capable"] = (mc >> 1) & 0x7
            cap_info["multiple_msg_enable"] = (mc >> 4) & 0x7
            cap_info["64bit_capable"] = bool(mc & (1 << 7))
            cap_info["per_vector_masking"] = bool(mc & (1 << 8))
            if mc & (1 << 7):  # 64-bit
                cap_info["size_bytes"] = 24 if mc & (1 << 8) else 14
            else:
                cap_info["size_bytes"] = 16 if mc & (1 << 8) else 10

        elif cap_id == 0x10:  # PCIe
            pcie_flags = struct.unpack_from("<H", config_data, cap_ptr + 2)[0]
            dev_cap = struct.unpack_from("<I", config_data, cap_ptr + 4)[0]
            dev_ctl = struct.unpack_from("<H", config_data, cap_ptr + 8)[0]
            dev_sta = struct.unpack_from("<H", config_data, cap_ptr + 10)[0]
            link_cap = struct.unpack_from("<I", config_data, cap_ptr + 12)[0]
            link_ctl = struct.unpack_from("<H", config_data, cap_ptr + 16)[0]
            link_sta = struct.unpack_from("<H", config_data, cap_ptr + 18)[0]
            cap_info["PCIe_cap_version"] = pcie_flags & 0xF
            cap_info["device_port_type"] = (pcie_flags >> 4) & 0xF
            cap_info["DevCap"] = f"0x{dev_cap:08X}"
            cap_info["DevCtl"] = f"0x{dev_ctl:04X}"
            cap_info["DevSta"] = f"0x{dev_sta:04X}"
            cap_info["LinkCap"] = f"0x{link_cap:08X}"
            cap_info["LinkCtl"] = f"0x{link_ctl:04X}"
            cap_info["LinkSta"] = f"0x{link_sta:04X}"
            cap_info["max_link_speed"] = link_cap & 0xF
            cap_info["max_link_width"] = (link_cap >> 4) & 0x3F
            cap_info["current_link_speed"] = link_sta & 0xF
            cap_info["current_link_width"] = (link_sta >> 4) & 0x3F
            cap_info["max_payload_size_supported"] = 128 << (dev_cap & 0x7)
            # PCIe cap is 60 bytes (v2)
            cap_info["size_bytes"] = 60

        elif cap_id == 0x11:  # MSI-X
            mc = struct.unpack_from("<H", config_data, cap_ptr + 2)[0]
            table_offset_bir = struct.unpack_from("<I", config_data, cap_ptr + 4)[0]
            pba_offset_bir = struct.unpack_from("<I", config_data, cap_ptr + 8)[0]
            cap_info["MC"] = f"0x{mc:04X}"
            cap_info["table_size"] = (mc & 0x7FF) + 1
            cap_info["function_mask"] = bool(mc & (1 << 14))
            cap_info["msix_enable"] = bool(mc & (1 << 15))
            cap_info["table_BIR"] = table_offset_bir & 0x7
            cap_info["table_offset"] = f"0x{table_offset_bir & 0xFFFFFFF8:X}"
            cap_info["table_offset_int"] = table_offset_bir & 0xFFFFFFF8
            cap_info["PBA_BIR"] = pba_offset_bir & 0x7
            cap_info["PBA_offset"] = f"0x{pba_offset_bir & 0xFFFFFFF8:X}"
            cap_info["PBA_offset_int"] = pba_offset_bir & 0xFFFFFFF8
            cap_info["size_bytes"] = 12

        caps.append(cap_info)
        cap_ptr = next_ptr

    return caps


def parse_pcie_ext_capabilities(config_data):
    """Walk PCIe Extended Capability linked list (offset 0x100+)."""
    ext_caps = []
    offset = 0x100
    visited = set()

    while offset >= 0x100 and offset < 0x1000 and offset not in visited:
        visited.add(offset)
        if offset + 4 > len(config_data):
            break
        header = struct.unpack_from("<I", config_data, offset)[0]
        if header == 0 or header == 0xFFFFFFFF:
            break

        cap_id = header & 0xFFFF
        cap_ver = (header >> 16) & 0xF
        next_offset = (header >> 20) & 0xFFC

        cap_name = PCIE_EXT_CAP_NAMES.get(cap_id, f"Unknown(0x{cap_id:04X})")

        ext_cap = {
            "offset": f"0x{offset:03X}",
            "offset_int": offset,
            "id": f"0x{cap_id:04X}",
            "id_int": cap_id,
            "name": cap_name,
            "version": cap_ver,
            "next_offset": f"0x{next_offset:03X}",
            "header_raw": f"0x{header:08X}",
        }

        # Parse specific extended capabilities
        if cap_id == 0x0001:  # AER
            ues = struct.unpack_from("<I", config_data, offset + 4)[0]
            uem = struct.unpack_from("<I", config_data, offset + 8)[0]
            uesev = struct.unpack_from("<I", config_data, offset + 12)[0]
            ces = struct.unpack_from("<I", config_data, offset + 16)[0]
            cem = struct.unpack_from("<I", config_data, offset + 20)[0]
            ext_cap["UncorrErrSta"] = f"0x{ues:08X}"
            ext_cap["UncorrErrMask"] = f"0x{uem:08X}"
            ext_cap["UncorrErrSev"] = f"0x{uesev:08X}"
            ext_cap["CorrErrSta"] = f"0x{ces:08X}"
            ext_cap["CorrErrMask"] = f"0x{cem:08X}"
            ext_cap["size_bytes"] = 48

        elif cap_id == 0x0003:  # DSN
            dsn_lo = struct.unpack_from("<I", config_data, offset + 4)[0]
            dsn_hi = struct.unpack_from("<I", config_data, offset + 8)[0]
            dsn = (dsn_hi << 32) | dsn_lo
            ext_cap["DSN"] = f"0x{dsn:016X}"
            ext_cap["DSN_low"] = f"0x{dsn_lo:08X}"
            ext_cap["DSN_high"] = f"0x{dsn_hi:08X}"
            ext_cap["size_bytes"] = 12

        elif cap_id == 0x000D:  # ACS
            acs_cap = struct.unpack_from("<H", config_data, offset + 4)[0]
            acs_ctl = struct.unpack_from("<H", config_data, offset + 6)[0]
            ext_cap["ACS_cap"] = f"0x{acs_cap:04X}"
            ext_cap["ACS_ctl"] = f"0x{acs_ctl:04X}"
            ext_cap["size_bytes"] = 8

        elif cap_id == 0x001E:  # L1 PM Substates
            l1ss_cap = struct.unpack_from("<I", config_data, offset + 4)[0]
            l1ss_ctl1 = struct.unpack_from("<I", config_data, offset + 8)[0]
            l1ss_ctl2 = struct.unpack_from("<I", config_data, offset + 12)[0]
            ext_cap["L1SS_cap"] = f"0x{l1ss_cap:08X}"
            ext_cap["L1SS_ctl1"] = f"0x{l1ss_ctl1:08X}"
            ext_cap["L1SS_ctl2"] = f"0x{l1ss_ctl2:08X}"
            ext_cap["size_bytes"] = 16

        ext_caps.append(ext_cap)
        offset = next_offset
        if offset == 0:
            break

    return ext_caps


def parse_config_space(config_data):
    """Parse full 4KB PCI config space into a structured dict."""
    result = {}

    # Standard header (Type 0)
    result["vendor_id"] = f"0x{struct.unpack_from('<H', config_data, 0)[0]:04X}"
    result["device_id"] = f"0x{struct.unpack_from('<H', config_data, 2)[0]:04X}"
    result["command"] = f"0x{struct.unpack_from('<H', config_data, 4)[0]:04X}"
    result["status"] = f"0x{struct.unpack_from('<H', config_data, 6)[0]:04X}"
    result["revision_id"] = f"0x{config_data[8]:02X}"
    result["prog_if"] = f"0x{config_data[9]:02X}"
    result["sub_class"] = f"0x{config_data[10]:02X}"
    result["base_class"] = f"0x{config_data[11]:02X}"
    result["class_code"] = f"0x{config_data[11]:02X}{config_data[10]:02X}{config_data[9]:02X}"
    result["cache_line_size"] = config_data[12]
    result["latency_timer"] = config_data[13]
    result["header_type"] = f"0x{config_data[14]:02X}"
    result["bist"] = f"0x{config_data[15]:02X}"

    # BARs
    bars = []
    for i in range(6):
        bar_val = struct.unpack_from("<I", config_data, 0x10 + i * 4)[0]
        bar_info = {"raw": f"0x{bar_val:08X}"}
        if bar_val & 1:
            bar_info["type"] = "IO"
            bar_info["address"] = f"0x{bar_val & 0xFFFFFFFC:08X}"
        else:
            bar_info["type"] = "Memory"
            bar_info["64bit"] = bool(bar_val & 0x4)
            bar_info["prefetchable"] = bool(bar_val & 0x8)
            bar_info["address"] = f"0x{bar_val & 0xFFFFFFF0:08X}"
        bars.append(bar_info)

    # Combine 64-bit BAR pairs
    if bars[0].get("64bit"):
        bar1_val = struct.unpack_from("<I", config_data, 0x14)[0]
        bar0_val = struct.unpack_from("<I", config_data, 0x10)[0]
        full_addr = ((bar1_val << 32) | (bar0_val & 0xFFFFFFF0))
        bars[0]["full_address"] = f"0x{full_addr:016X}"
        bars[0]["full_address_int"] = full_addr

    result["BARs"] = bars

    result["subsystem_vendor_id"] = f"0x{struct.unpack_from('<H', config_data, 0x2C)[0]:04X}"
    result["subsystem_id"] = f"0x{struct.unpack_from('<H', config_data, 0x2E)[0]:04X}"
    result["expansion_rom"] = f"0x{struct.unpack_from('<I', config_data, 0x30)[0]:08X}"
    result["capabilities_ptr"] = f"0x{config_data[0x34]:02X}"
    result["interrupt_line"] = config_data[0x3C]
    result["interrupt_pin"] = config_data[0x3D]
    result["min_grant"] = config_data[0x3E]
    result["max_latency"] = config_data[0x3F]

    # Parse capability chain
    result["capabilities"] = parse_pci_capabilities(config_data)

    # Parse extended capabilities
    result["extended_capabilities"] = parse_pcie_ext_capabilities(config_data)

    # Raw DWORDs for reference
    raw_dwords = []
    for i in range(0, min(256, len(config_data)), 4):
        dw = struct.unpack_from("<I", config_data, i)[0]
        raw_dwords.append(f"0x{dw:08X}")
    result["raw_header_dwords"] = raw_dwords

    return result


def hexdump(data, base_addr=0):
    """Generate hex dump string."""
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base_addr + offset:04X}: {hex_part:<48s} {ascii_part}")
    return "\n".join(lines)


def parse_only(bin_path, output_dir):
    """Parse an already-extracted config space binary (from target PC)."""
    print("=" * 60)
    print("PCI Config Space Parser (from pre-extracted binary)")
    print("=" * 60)

    with open(bin_path, "rb") as f:
        config_data = f.read()

    print(f"\nLoaded: {bin_path} ({len(config_data)} bytes)")

    if len(config_data) < 256:
        print(f"ERROR: File too small ({len(config_data)} bytes, need at least 256)")
        sys.exit(1)

    # Pad to 4KB if needed (some tools only dump 256 bytes)
    if len(config_data) < 4096:
        print(f"WARNING: Only {len(config_data)} bytes, padding to 4096 with zeros")
        config_data = config_data + b'\x00' * (4096 - len(config_data))

    vid = struct.unpack_from("<H", config_data, 0)[0]
    did = struct.unpack_from("<H", config_data, 2)[0]
    rev = config_data[8]
    class_code = (config_data[11] << 16) | (config_data[10] << 8) | config_data[9]

    print(f"\nValidation:")
    print(f"  Vendor ID:  0x{vid:04X} {'OK' if vid == TARGET_VID else 'UNEXPECTED'}")
    print(f"  Device ID:  0x{did:04X} {'OK' if did == TARGET_DID else 'UNEXPECTED'}")
    print(f"  Revision:   0x{rev:02X}   {'OK' if rev == TARGET_REV else 'UNEXPECTED'}")
    print(f"  Class Code: 0x{class_code:06X} {'OK (NVMe)' if class_code == 0x010802 else 'UNEXPECTED'}")

    if vid == 0xFFFF:
        print("\nERROR: Data is all-FF -- invalid dump")
        sys.exit(1)

    parsed = parse_config_space(config_data)
    parsed["extraction_info"] = {"source": bin_path, "method": "pre-extracted binary"}

    print(f"\nCapability chain (starting at {parsed['capabilities_ptr']}):")
    for cap in parsed["capabilities"]:
        print(f"  [{cap['offset']}] {cap['name']} (ID={cap['id']}, next={cap['next']})")
        if "table_size" in cap:
            print(f"         MSI-X: {cap['table_size']} vectors, table@BAR{cap['table_BIR']}+{cap['table_offset']}")
        if "max_link_speed" in cap:
            speeds = {1: "2.5GT/s", 2: "5GT/s", 3: "8GT/s", 4: "16GT/s"}
            print(f"         Link: x{cap['max_link_width']} {speeds.get(cap['max_link_speed'], '?')}")

    print(f"\nExtended capabilities:")
    for ext in parsed["extended_capabilities"]:
        print(f"  [{ext['offset']}] {ext['name']} (v{ext['version']}, next={ext['next_offset']})")
        if "DSN" in ext:
            print(f"         DSN: {ext['DSN']}")

    # Save the canonical copy + parsed JSON
    canonical = os.path.join(output_dir, "config_space_4k.bin")
    if os.path.abspath(bin_path) != os.path.abspath(canonical):
        with open(canonical, "wb") as f:
            f.write(config_data)
        print(f"\nSaved: {canonical}")

    json_path = os.path.join(output_dir, "config_space_parsed.json")
    with open(json_path, "w") as f:
        json.dump(parsed, f, indent=2)
    print(f"Saved: {json_path}")

    hexdump_path = os.path.join(output_dir, "config_space_hexdump.txt")
    with open(hexdump_path, "w") as f:
        f.write(f"PCI Config Space - VID:0x{vid:04X} DID:0x{did:04X}\n")
        f.write("=" * 60 + "\n\n")
        f.write(hexdump(config_data))
    print(f"Saved: {hexdump_path}")

    print(f"\n{'=' * 60}")
    print("Config space parsing COMPLETE")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Extract PCI config space via ECAM DMA")
    parser.add_argument("--bus", type=int, default=DEFAULT_BUS, help=f"PCI bus (default: {DEFAULT_BUS})")
    parser.add_argument("--dev", type=int, default=DEFAULT_DEV, help=f"PCI device (default: {DEFAULT_DEV})")
    parser.add_argument("--fun", type=int, default=DEFAULT_FUN, help=f"PCI function (default: {DEFAULT_FUN})")
    parser.add_argument("--ecam-base", type=lambda x: int(x, 0), default=None, help="ECAM base address (auto-detect)")
    parser.add_argument("--scan", action="store_true", help="Scan all buses for target device")
    parser.add_argument("--parse", type=str, default=None, help="Parse existing binary (skip DMA, e.g. from target PC)")
    parser.add_argument("--output-dir", default=BUILD_INPUTS_DIR, help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Parse-only mode (for pre-extracted binaries from target PC)
    if args.parse:
        parse_only(args.parse, args.output_dir)
        return

    print("=" * 60)
    print("PCI Config Space Extraction via ECAM DMA")
    print("=" * 60)

    # Find ECAM base
    ecam_base = args.ecam_base
    if ecam_base is None:
        ecam_base = find_ecam_base()
        if ecam_base is None:
            print("\nERROR: Could not find ECAM base address.")
            print("Try specifying it manually: --ecam-base 0xE0000000")
            print("Or check if DMA device is connected and working.")
            print("\nALTERNATIVE: Extract on target PC, then parse here:")
            print("  python 01_capture_config_profile.py --parse build_inputs/config_space_4k.bin")
            sys.exit(1)

    print(f"\nECAM base: 0x{ecam_base:X}")

    # Scan mode
    if args.scan:
        found = scan_for_device(ecam_base)
        if not found:
            print("Device not found! Check if NVMe is installed and powered.")
            sys.exit(1)
        bus, dev, fun = found[0]
    else:
        bus, dev, fun = args.bus, args.dev, args.fun

    # Compute config space address
    cfg_addr = ecam_address(ecam_base, bus, dev, fun)
    print(f"BDF: {bus:02X}:{dev:02X}.{fun}")
    print(f"Config space address: 0x{cfg_addr:X}")

    # Read full 4KB config space
    print(f"\nReading 4096 bytes from 0x{cfg_addr:X}...")
    config_data = read_physical_memory(cfg_addr, 4096)

    if config_data is None:
        print("\nERROR: Failed to read config space.")
        print("Possible causes:")
        print("  - DMA device not connected")
        print("  - Wrong ECAM base address")
        print("  - IOMMU blocking access")
        print("\nFallback: Use RW-Everything on target PC to dump config space")
        sys.exit(1)

    # Validate
    vid = struct.unpack_from("<H", config_data, 0)[0]
    did = struct.unpack_from("<H", config_data, 2)[0]
    rev = config_data[8]
    class_code = (config_data[11] << 16) | (config_data[10] << 8) | config_data[9]

    print(f"\nValidation:")
    print(f"  Vendor ID:  0x{vid:04X} {'OK' if vid == TARGET_VID else 'UNEXPECTED (expect 0x126F)'}")
    print(f"  Device ID:  0x{did:04X} {'OK' if did == TARGET_DID else 'UNEXPECTED (expect 0x2263)'}")
    print(f"  Revision:   0x{rev:02X}   {'OK' if rev == TARGET_REV else 'UNEXPECTED (expect 0x03)'}")
    print(f"  Class Code: 0x{class_code:06X} {'OK (NVMe)' if class_code == 0x010802 else 'UNEXPECTED'}")

    if vid == 0xFFFF:
        print("\nERROR: Read all-FF -- device not present or IOMMU blocking access")
        sys.exit(1)
    if vid != TARGET_VID or did != TARGET_DID:
        print("\nWARNING: Device IDs don't match expected NE-256 (VEN_126F/DEV_2263)")
        print("Continuing anyway -- double-check BDF number")

    # Parse
    parsed = parse_config_space(config_data)
    parsed["extraction_info"] = {
        "ecam_base": f"0x{ecam_base:X}",
        "bdf": f"{bus:02X}:{dev:02X}.{fun}",
        "config_address": f"0x{cfg_addr:X}",
        "bus": bus,
        "device": dev,
        "function": fun,
    }

    # Print capability chain
    print(f"\nCapability chain (starting at {parsed['capabilities_ptr']}):")
    for cap in parsed["capabilities"]:
        print(f"  [{cap['offset']}] {cap['name']} (ID={cap['id']}, next={cap['next']})")
        if "table_size" in cap:
            print(f"         MSI-X: {cap['table_size']} vectors, table@BAR{cap['table_BIR']}+{cap['table_offset']}")
        if "max_link_speed" in cap:
            speeds = {1: "2.5GT/s", 2: "5GT/s", 3: "8GT/s", 4: "16GT/s"}
            print(f"         Link: x{cap['max_link_width']} {speeds.get(cap['max_link_speed'], '?')}")
            print(f"         Negotiated: x{cap['current_link_width']} {speeds.get(cap['current_link_speed'], '?')}")

    print(f"\nExtended capabilities:")
    for ext in parsed["extended_capabilities"]:
        print(f"  [{ext['offset']}] {ext['name']} (v{ext['version']}, next={ext['next_offset']})")
        if "DSN" in ext:
            print(f"         DSN: {ext['DSN']}")

    # Save outputs
    bin_path = os.path.join(args.output_dir, "config_space_4k.bin")
    with open(bin_path, "wb") as f:
        f.write(config_data)
    print(f"\nSaved: {bin_path} ({len(config_data)} bytes)")

    json_path = os.path.join(args.output_dir, "config_space_parsed.json")
    with open(json_path, "w") as f:
        json.dump(parsed, f, indent=2)
    print(f"Saved: {json_path}")

    hexdump_path = os.path.join(args.output_dir, "config_space_hexdump.txt")
    with open(hexdump_path, "w") as f:
        f.write(f"PCI Config Space - BDF {bus:02X}:{dev:02X}.{fun}\n")
        f.write(f"ECAM Address: 0x{cfg_addr:X}\n")
        f.write(f"VID: 0x{vid:04X}  DID: 0x{did:04X}\n")
        f.write("=" * 60 + "\n\n")
        f.write(hexdump(config_data))
    print(f"Saved: {hexdump_path}")

    # Save BDF info
    bdf_path = os.path.join(args.output_dir, "bdf_info.json")
    with open(bdf_path, "w") as f:
        json.dump({
            "bus": bus,
            "device": dev,
            "function": fun,
            "bdf": f"{bus:02X}:{dev:02X}.{fun}",
            "ecam_base": f"0x{ecam_base:X}",
            "config_address": f"0x{cfg_addr:X}",
        }, f, indent=2)
    print(f"Saved: {bdf_path}")

    print(f"\n{'=' * 60}")
    print("Config space extraction COMPLETE")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()


