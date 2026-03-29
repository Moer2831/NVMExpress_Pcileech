#!/usr/bin/env python3
"""
NVM_Express_Pcileech_FPGA_75T
discord: Moer_2831
community: https://discord.gg/sXcQhxa8qy

Extract NVMe BAR0 registers and MSI-X table from the reference NE-256 SSD via DMA.

Reads the BAR0 physical address from the extracted config space, then DMA-reads
the NVMe controller registers and MSI-X table.

Prerequisites: Run 01_capture_config_profile.py first.

Usage:
    python 02_capture_bar0_profile.py
    python 02_capture_bar0_profile.py --bar0-addr 0xFE600000   # Manual BAR0 address
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


def read_physical_memory(addr, size):
    """Read physical memory via available DMA method. Returns bytes or None."""
    # Method 1: memprocfs
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

    # Method 3: pcileech.exe
    try:
        tmpfile = os.path.join(tempfile.gettempdir(), "pcileech_bar0_dump.bin")
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


def get_bar0_from_config_space():
    """Read BAR0 address from previously extracted config space."""
    bin_path = os.path.join(BUILD_INPUTS_DIR, "config_space_4k.bin")
    if not os.path.exists(bin_path):
        return None, None

    with open(bin_path, "rb") as f:
        data = f.read()

    bar0_lo = struct.unpack_from("<I", data, 0x10)[0]
    bar0_hi = struct.unpack_from("<I", data, 0x14)[0]

    # Check if 64-bit BAR
    is_64bit = bool(bar0_lo & 0x4)
    is_prefetchable = bool(bar0_lo & 0x8)
    base_addr = bar0_lo & 0xFFFFFFF0
    if is_64bit:
        base_addr |= (bar0_hi << 32)

    bar_info = {
        "raw_low": f"0x{bar0_lo:08X}",
        "raw_high": f"0x{bar0_hi:08X}",
        "base_address": f"0x{base_addr:016X}" if is_64bit else f"0x{base_addr:08X}",
        "base_address_int": base_addr,
        "is_64bit": is_64bit,
        "is_prefetchable": is_prefetchable,
        "type": "Memory",
    }

    return base_addr, bar_info


def get_msix_info_from_config_space():
    """Read MSI-X capability info from previously extracted config space."""
    json_path = os.path.join(BUILD_INPUTS_DIR, "config_space_parsed.json")
    if not os.path.exists(json_path):
        return None

    with open(json_path) as f:
        parsed = json.load(f)

    for cap in parsed.get("capabilities", []):
        if cap.get("id_int") == 0x11:  # MSI-X
            return cap

    return None


def parse_nvme_cap(cap_lo, cap_hi):
    """Parse NVMe CAP register (64-bit)."""
    cap = (cap_hi << 32) | cap_lo
    return {
        "raw": f"0x{cap:016X}",
        "MQES": cap & 0xFFFF,
        "MQES_desc": f"{(cap & 0xFFFF) + 1} entries max",
        "CQR": bool(cap & (1 << 16)),
        "AMS": (cap >> 17) & 0x3,
        "TO": (cap >> 24) & 0xFF,
        "TO_desc": f"{((cap >> 24) & 0xFF) * 500}ms",
        "DSTRD": (cap >> 32) & 0xF,
        "DSTRD_desc": f"{4 << ((cap >> 32) & 0xF)} bytes",
        "NSSRS": bool(cap & (1 << 36)),
        "CSS": (cap >> 37) & 0xFF,
        "CSS_desc": "NVM" if ((cap >> 37) & 0xFF) & 1 else "other",
        "BPS": bool(cap & (1 << 45)),
        "MPSMIN": (cap >> 48) & 0xF,
        "MPSMIN_desc": f"{4 << ((cap >> 48) & 0xF)}KB",
        "MPSMAX": (cap >> 52) & 0xF,
        "MPSMAX_desc": f"{4 << ((cap >> 52) & 0xF)}KB",
        "PMRS": bool(cap & (1 << 56)),
        "CMBS": bool(cap & (1 << 57)),
    }


def parse_nvme_cc(cc):
    """Parse NVMe CC register."""
    return {
        "raw": f"0x{cc:08X}",
        "EN": bool(cc & 1),
        "CSS": (cc >> 4) & 0x7,
        "MPS": (cc >> 7) & 0xF,
        "MPS_desc": f"{4 << ((cc >> 7) & 0xF)}KB",
        "AMS": (cc >> 11) & 0x7,
        "SHN": (cc >> 14) & 0x3,
        "IOSQES": (cc >> 16) & 0xF,
        "IOSQES_desc": f"{1 << ((cc >> 16) & 0xF)} bytes",
        "IOCQES": (cc >> 20) & 0xF,
        "IOCQES_desc": f"{1 << ((cc >> 20) & 0xF)} bytes",
    }


def parse_nvme_csts(csts):
    """Parse NVMe CSTS register."""
    return {
        "raw": f"0x{csts:08X}",
        "RDY": bool(csts & 1),
        "CFS": bool(csts & (1 << 1)),
        "SHST": (csts >> 2) & 0x3,
        "SHST_desc": {0: "Normal", 1: "Shutdown processing", 2: "Shutdown complete"}.get((csts >> 2) & 0x3, "Reserved"),
        "NSSRO": bool(csts & (1 << 4)),
        "PP": bool(csts & (1 << 5)),
    }


def parse_nvme_registers(data):
    """Parse NVMe BAR0 controller registers."""
    regs = {}

    # CAP (0x00-0x07) - 64-bit
    cap_lo = struct.unpack_from("<I", data, 0x00)[0]
    cap_hi = struct.unpack_from("<I", data, 0x04)[0]
    regs["CAP"] = parse_nvme_cap(cap_lo, cap_hi)

    # VS (0x08)
    vs = struct.unpack_from("<I", data, 0x08)[0]
    regs["VS"] = {
        "raw": f"0x{vs:08X}",
        "major": (vs >> 16) & 0xFFFF,
        "minor": (vs >> 8) & 0xFF,
        "tertiary": vs & 0xFF,
        "version_string": f"{(vs >> 16) & 0xFFFF}.{(vs >> 8) & 0xFF}.{vs & 0xFF}",
    }

    # INTMS (0x0C)
    intms = struct.unpack_from("<I", data, 0x0C)[0]
    regs["INTMS"] = f"0x{intms:08X}"

    # INTMC (0x10)
    intmc = struct.unpack_from("<I", data, 0x10)[0]
    regs["INTMC"] = f"0x{intmc:08X}"

    # CC (0x14)
    cc = struct.unpack_from("<I", data, 0x14)[0]
    regs["CC"] = parse_nvme_cc(cc)

    # Reserved (0x18)
    regs["Reserved_0x18"] = f"0x{struct.unpack_from('<I', data, 0x18)[0]:08X}"

    # CSTS (0x1C)
    csts = struct.unpack_from("<I", data, 0x1C)[0]
    regs["CSTS"] = parse_nvme_csts(csts)

    # NSSR (0x20)
    nssr = struct.unpack_from("<I", data, 0x20)[0]
    regs["NSSR"] = f"0x{nssr:08X}"

    # AQA (0x24)
    aqa = struct.unpack_from("<I", data, 0x24)[0]
    regs["AQA"] = {
        "raw": f"0x{aqa:08X}",
        "ASQS": aqa & 0xFFF,
        "ASQS_desc": f"{(aqa & 0xFFF) + 1} entries",
        "ACQS": (aqa >> 16) & 0xFFF,
        "ACQS_desc": f"{((aqa >> 16) & 0xFFF) + 1} entries",
    }

    # ASQ (0x28-0x2F) - 64-bit
    asq_lo = struct.unpack_from("<I", data, 0x28)[0]
    asq_hi = struct.unpack_from("<I", data, 0x2C)[0]
    asq = (asq_hi << 32) | asq_lo
    regs["ASQ"] = f"0x{asq:016X}"

    # ACQ (0x30-0x37) - 64-bit
    acq_lo = struct.unpack_from("<I", data, 0x30)[0]
    acq_hi = struct.unpack_from("<I", data, 0x34)[0]
    acq = (acq_hi << 32) | acq_lo
    regs["ACQ"] = f"0x{acq:016X}"

    # CMBLOC (0x38)
    cmbloc = struct.unpack_from("<I", data, 0x38)[0]
    regs["CMBLOC"] = f"0x{cmbloc:08X}"

    # CMBSZ (0x3C)
    cmbsz = struct.unpack_from("<I", data, 0x3C)[0]
    regs["CMBSZ"] = f"0x{cmbsz:08X}"

    # Raw first 64 bytes as DWORDs
    raw = []
    for i in range(0, 64, 4):
        dw = struct.unpack_from("<I", data, i)[0]
        raw.append(f"0x{dw:08X}")
    regs["raw_dwords_0x00_0x3F"] = raw

    return regs


def parse_msix_table(data, table_offset, num_entries):
    """Parse MSI-X table entries from BAR0 data."""
    entries = []
    for i in range(num_entries):
        entry_offset = table_offset + (i * 16)
        if entry_offset + 16 > len(data):
            break
        msg_addr_lo = struct.unpack_from("<I", data, entry_offset)[0]
        msg_addr_hi = struct.unpack_from("<I", data, entry_offset + 4)[0]
        msg_data = struct.unpack_from("<I", data, entry_offset + 8)[0]
        vec_ctl = struct.unpack_from("<I", data, entry_offset + 12)[0]
        msg_addr = (msg_addr_hi << 32) | msg_addr_lo

        entries.append({
            "index": i,
            "msg_address": f"0x{msg_addr:016X}",
            "msg_data": f"0x{msg_data:08X}",
            "vector_control": f"0x{vec_ctl:08X}",
            "masked": bool(vec_ctl & 1),
        })

    return entries


def determine_bar0_size_from_config(config_data):
    """Try to determine BAR0 size. Returns size in bytes or None."""
    # Look for common SM2263 BAR0 sizes
    # The BAR address bits tell us the minimum: if BAR0 low = 0xXXXXC004,
    # the address bits set indicate alignment/size.
    # Without write-readback, we estimate from MSI-X layout:
    # If MSI-X PBA is at 0x3000, BAR0 must be at least 0x4000 (16KB)

    json_path = os.path.join(BUILD_INPUTS_DIR, "config_space_parsed.json")
    if os.path.exists(json_path):
        with open(json_path) as f:
            parsed = json.load(f)
        for cap in parsed.get("capabilities", []):
            if cap.get("id_int") == 0x11:  # MSI-X
                pba_offset = cap.get("PBA_offset_int", 0)
                # BAR0 must be at least PBA_offset + PBA_size
                min_size = pba_offset + 0x1000  # PBA + 4KB margin
                # Round up to power of 2
                size = 1
                while size < min_size:
                    size <<= 1
                return size

    # Default: SM2263 uses 16KB BAR0
    return 16 * 1024


def main():
    parser = argparse.ArgumentParser(description="Extract NVMe BAR0 registers via DMA")
    parser.add_argument("--bar0-addr", type=lambda x: int(x, 0), default=None,
                       help="Manual BAR0 address (auto-read from config space)")
    parser.add_argument("--read-size", type=lambda x: int(x, 0), default=None,
                       help="Bytes to read from BAR0 (auto-detect)")
    parser.add_argument("--output-dir", default=BUILD_INPUTS_DIR, help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 60)
    print("NVMe BAR0 Register Extraction via DMA")
    print("=" * 60)

    # Get BAR0 address
    bar0_addr = args.bar0_addr
    bar_info = None

    if bar0_addr is None:
        bar0_addr, bar_info = get_bar0_from_config_space()
        if bar0_addr is None or bar0_addr == 0:
            print("\nERROR: Cannot determine BAR0 address.")
            print("Either run 01_capture_config_profile.py first, or specify --bar0-addr")
            sys.exit(1)

    print(f"\nBAR0 address: 0x{bar0_addr:X}")
    if bar_info:
        print(f"  64-bit: {bar_info['is_64bit']}")
        print(f"  Prefetchable: {bar_info['is_prefetchable']}")

    # Determine read size
    config_bin_path = os.path.join(BUILD_INPUTS_DIR, "config_space_4k.bin")
    config_data = None
    if os.path.exists(config_bin_path):
        with open(config_bin_path, "rb") as f:
            config_data = f.read()

    read_size = args.read_size
    if read_size is None:
        read_size = determine_bar0_size_from_config(config_data)
        if read_size is None:
            read_size = 16384  # 16KB default for SM2263
    print(f"Read size: {read_size} bytes ({read_size // 1024}KB)")

    # Read BAR0 region
    print(f"\nReading {read_size} bytes from BAR0 at 0x{bar0_addr:X}...")
    bar0_data = read_physical_memory(bar0_addr, read_size)

    if bar0_data is None:
        print("\nERROR: Failed to read BAR0 region.")
        print("This likely means IOMMU (VT-d) is blocking peer MMIO reads.")
        print("Options:")
        print("  1. Temporarily disable VT-d in BIOS on target PC")
        print("  2. Use RW-Everything on target PC: Physical Memory -> address 0x{:X}".format(bar0_addr))
        sys.exit(1)

    # Check for all-FF (IOMMU block or device not responding)
    if all(b == 0xFF for b in bar0_data[:64]):
        print("\nWARNING: BAR0 data is all-FF. Device may not be responding or IOMMU is blocking.")
        print("Try disabling VT-d temporarily or use target PC tools.")
        # Continue to save the data anyway for analysis

    # Parse NVMe registers
    print("\nNVMe Controller Registers:")
    regs = parse_nvme_registers(bar0_data)

    cap = regs["CAP"]
    print(f"  CAP:  {cap['raw']}")
    print(f"    MQES={cap['MQES']} ({cap['MQES_desc']})")
    print(f"    CQR={cap['CQR']}, AMS={cap['AMS']}")
    print(f"    TO={cap['TO']} ({cap['TO_desc']})")
    print(f"    DSTRD={cap['DSTRD']} ({cap['DSTRD_desc']})")
    print(f"    NSSRS={cap['NSSRS']}")
    print(f"    CSS={cap['CSS']} ({cap['CSS_desc']})")
    print(f"    MPSMIN={cap['MPSMIN']} ({cap['MPSMIN_desc']}), MPSMAX={cap['MPSMAX']} ({cap['MPSMAX_desc']})")

    vs = regs["VS"]
    print(f"  VS:   {vs['raw']} (NVMe {vs['version_string']})")

    cc = regs["CC"]
    print(f"  CC:   {cc['raw']} (EN={cc['EN']}, CSS={cc['CSS']}, MPS={cc['MPS']})")

    csts = regs["CSTS"]
    print(f"  CSTS: {csts['raw']} (RDY={csts['RDY']}, CFS={csts['CFS']}, SHST={csts['SHST_desc']})")

    aqa = regs["AQA"]
    print(f"  AQA:  {aqa['raw']} (ASQS={aqa['ASQS_desc']}, ACQS={aqa['ACQS_desc']})")

    print(f"  ASQ:  {regs['ASQ']}")
    print(f"  ACQ:  {regs['ACQ']}")

    # Parse MSI-X table
    msix_info = get_msix_info_from_config_space()
    msix_parsed = None
    if msix_info and msix_info.get("table_BIR") == 0:  # Table in BAR0
        table_offset = msix_info["table_offset_int"]
        num_entries = msix_info["table_size"]
        pba_offset = msix_info["PBA_offset_int"]

        if table_offset + (num_entries * 16) <= len(bar0_data):
            print(f"\nMSI-X Table ({num_entries} entries at BAR0+0x{table_offset:X}):")
            entries = parse_msix_table(bar0_data, table_offset, num_entries)
            for entry in entries:
                mask_str = "MASKED" if entry["masked"] else "active"
                print(f"  [{entry['index']}] Addr={entry['msg_address']} Data={entry['msg_data']} ({mask_str})")

            # Parse PBA
            pba_size = ((num_entries + 63) // 64) * 8
            pba_data = None
            if pba_offset + pba_size <= len(bar0_data):
                pba_data = bar0_data[pba_offset:pba_offset + pba_size]
                print(f"\nMSI-X PBA ({pba_size} bytes at BAR0+0x{pba_offset:X}):")
                for i in range(0, len(pba_data), 8):
                    qw = struct.unpack_from("<Q", pba_data, i)[0]
                    print(f"  PBA[{i // 8}]: 0x{qw:016X}")

            msix_parsed = {
                "table_size": num_entries,
                "table_offset": f"0x{table_offset:X}",
                "table_BIR": msix_info["table_BIR"],
                "pba_offset": f"0x{pba_offset:X}",
                "pba_BIR": msix_info["PBA_BIR"],
                "entries": entries,
            }
        else:
            print(f"\nWARNING: MSI-X table extends beyond read data (need {table_offset + num_entries * 16} bytes)")
    elif msix_info:
        print(f"\nMSI-X table is in BAR{msix_info.get('table_BIR', '?')}, not BAR0 -- cannot read from this dump")
    else:
        print("\nNo MSI-X capability found in config space (device may use MSI instead)")

    # Estimate BAR0 size
    bar0_size = determine_bar0_size_from_config(config_data)
    if bar_info is None:
        bar_info = {}
    bar_info["estimated_size"] = bar0_size
    bar_info["estimated_size_desc"] = f"{bar0_size // 1024}KB"

    # Save outputs
    bin_path = os.path.join(args.output_dir, "nvme_bar0.bin")
    with open(bin_path, "wb") as f:
        f.write(bar0_data)
    print(f"\nSaved: {bin_path} ({len(bar0_data)} bytes)")

    regs_json_path = os.path.join(args.output_dir, "nvme_registers_parsed.json")
    with open(regs_json_path, "w") as f:
        json.dump(regs, f, indent=2)
    print(f"Saved: {regs_json_path}")

    bar_json_path = os.path.join(args.output_dir, "bar_info.json")
    with open(bar_json_path, "w") as f:
        json.dump(bar_info, f, indent=2)
    print(f"Saved: {bar_json_path}")

    if msix_parsed:
        msix_json_path = os.path.join(args.output_dir, "msix_parsed.json")
        with open(msix_json_path, "w") as f:
            json.dump(msix_parsed, f, indent=2)
        print(f"Saved: {msix_json_path}")

    print(f"\n{'=' * 60}")
    print("BAR0 extraction COMPLETE")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()


