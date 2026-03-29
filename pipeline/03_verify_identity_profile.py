#!/usr/bin/env python3
"""
NVM_Express_Pcileech_FPGA_75T
discord: Moer2831
community: https://discord.gg/sXcQhxa8qy

Parse and verify NVMe Identify Controller and Identify Namespace binary data.

Reads the identify_controller.bin and identify_namespace.bin files (extracted
from the real NE-256 reference device) and validates all critical fields.

Usage:
    python 03_verify_identity_profile.py
"""

import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_INPUTS_DIR = os.path.join(SCRIPT_DIR, "build_inputs")


def parse_identify_controller(data):
    """Parse NVMe Identify Controller data structure (4KB)."""
    if len(data) != 4096:
        print(f"ERROR: Identify Controller data is {len(data)} bytes, expected 4096")
        return None

    result = {}

    # Offset 0x00: PCI Vendor ID
    result["VID"] = struct.unpack_from("<H", data, 0)[0]
    # Offset 0x02: PCI Subsystem Vendor ID
    result["SSVID"] = struct.unpack_from("<H", data, 2)[0]
    # Offset 0x04: Serial Number (20 bytes ASCII)
    result["SN"] = data[4:24].decode("ascii", errors="replace").rstrip()
    # Offset 0x18: Model Number (40 bytes ASCII)
    result["MN"] = data[24:64].decode("ascii", errors="replace").rstrip()
    # Offset 0x40: Firmware Revision (8 bytes ASCII)
    result["FR"] = data[64:72].decode("ascii", errors="replace").rstrip()
    # Offset 0x48: Recommended Arbitration Burst
    result["RAB"] = data[72]
    # Offset 0x49: IEEE OUI (3 bytes)
    result["IEEE"] = f"{data[75]:02X}:{data[74]:02X}:{data[73]:02X}"
    # Offset 0x4C: CMIC
    result["CMIC"] = data[76]
    # Offset 0x4D: MDTS
    result["MDTS"] = data[77]
    result["MDTS_desc"] = f"{(1 << data[77]) * 4}KB" if data[77] else "unlimited"
    # Offset 0x4E: Controller ID
    result["CNTLID"] = struct.unpack_from("<H", data, 78)[0]
    # Offset 0x50: Version
    ver = struct.unpack_from("<I", data, 80)[0]
    result["VER"] = f"0x{ver:08X}"
    result["VER_string"] = f"{(ver >> 16) & 0xFFFF}.{(ver >> 8) & 0xFF}.{ver & 0xFF}"
    # Offset 0x54: RTD3 Resume Latency
    result["RTD3R"] = struct.unpack_from("<I", data, 84)[0]
    # Offset 0x58: RTD3 Entry Latency
    result["RTD3E"] = struct.unpack_from("<I", data, 88)[0]
    # Offset 0x5C: OAES
    result["OAES"] = struct.unpack_from("<I", data, 92)[0]
    # Offset 0x60: CTRATT
    result["CTRATT"] = struct.unpack_from("<I", data, 96)[0]

    # Offset 0x100: OACS
    result["OACS"] = struct.unpack_from("<H", data, 256)[0]
    result["OACS_desc"] = []
    if result["OACS"] & 0x01: result["OACS_desc"].append("SecuritySend/Recv")
    if result["OACS"] & 0x02: result["OACS_desc"].append("FormatNVM")
    if result["OACS"] & 0x04: result["OACS_desc"].append("FirmwareDownload")
    if result["OACS"] & 0x08: result["OACS_desc"].append("NamespaceMgmt")
    if result["OACS"] & 0x10: result["OACS_desc"].append("DeviceSelfTest")

    # Offset 0x102: ACL
    result["ACL"] = data[258]
    # Offset 0x103: AERL
    result["AERL"] = data[259]
    # Offset 0x104: FRMW
    result["FRMW"] = data[260]
    result["FRMW_slots"] = (data[260] >> 1) & 0x7
    result["FRMW_slot1_ro"] = bool(data[260] & 1)
    # Offset 0x105: LPA
    result["LPA"] = data[261]
    # Offset 0x106: ELPE
    result["ELPE"] = data[262]
    # Offset 0x107: NPSS (0-based)
    result["NPSS"] = data[263]
    # Offset 0x108: AVSCC
    result["AVSCC"] = data[264]
    # Offset 0x109: APSTA
    result["APSTA"] = data[265]

    # Offset 0x10C: WCTEMP
    result["WCTEMP"] = struct.unpack_from("<H", data, 268)[0]
    result["WCTEMP_celsius"] = result["WCTEMP"] - 273 if result["WCTEMP"] else 0
    # Offset 0x10E: CCTEMP
    result["CCTEMP"] = struct.unpack_from("<H", data, 270)[0]
    result["CCTEMP_celsius"] = result["CCTEMP"] - 273 if result["CCTEMP"] else 0

    # Offset 0x200: SQES
    result["SQES"] = data[512]
    result["SQES_min"] = 1 << (data[512] & 0xF)
    result["SQES_max"] = 1 << ((data[512] >> 4) & 0xF)
    # Offset 0x201: CQES
    result["CQES"] = data[513]
    result["CQES_min"] = 1 << (data[513] & 0xF)
    result["CQES_max"] = 1 << ((data[513] >> 4) & 0xF)
    # Offset 0x202: MAXCMD
    result["MAXCMD"] = struct.unpack_from("<H", data, 514)[0]
    # Offset 0x204: NN
    result["NN"] = struct.unpack_from("<I", data, 516)[0]
    # Offset 0x208: ONCS
    result["ONCS"] = struct.unpack_from("<H", data, 520)[0]
    # Offset 0x20A: FUSES
    result["FUSES"] = struct.unpack_from("<H", data, 522)[0]
    # Offset 0x20C: FNA
    result["FNA"] = data[524]
    # Offset 0x20E: VWC
    result["VWC"] = data[526]
    result["VWC_present"] = bool(data[526] & 1)
    # Offset 0x210: AWUN
    result["AWUN"] = struct.unpack_from("<H", data, 528)[0]
    # Offset 0x212: AWUPF
    result["AWUPF"] = struct.unpack_from("<H", data, 530)[0]

    # Offset 0x240: SGLS
    result["SGLS"] = struct.unpack_from("<I", data, 576)[0]

    # Power State Descriptors (offset 0x800, 32 bytes each)
    psds = []
    for i in range(result["NPSS"] + 1):
        psd_offset = 2048 + (i * 32)
        if psd_offset + 32 > len(data):
            break
        mp = struct.unpack_from("<H", data, psd_offset)[0]
        mps_flag = data[psd_offset + 3]
        enlat = struct.unpack_from("<I", data, psd_offset + 4)[0]
        exlat = struct.unpack_from("<I", data, psd_offset + 8)[0]
        rrt = data[psd_offset + 12] & 0x1F
        rrl = data[psd_offset + 13] & 0x1F
        rwt = data[psd_offset + 14] & 0x1F
        rwl = data[psd_offset + 15] & 0x1F
        idlp = struct.unpack_from("<H", data, psd_offset + 16)[0]
        ips = data[psd_offset + 18] & 0x3
        actp = struct.unpack_from("<H", data, psd_offset + 20)[0]
        apw = data[psd_offset + 22] & 0x7
        nops = bool(data[psd_offset + 3] & 0x02)

        scale = 0.01 if (mps_flag & 0x01) else 0.01  # centiwatts
        psds.append({
            "state": i,
            "max_power_cw": mp,
            "max_power_watts": mp * scale,
            "entry_latency_us": enlat,
            "exit_latency_us": exlat,
            "relative_read_throughput": rrt,
            "relative_read_latency": rrl,
            "relative_write_throughput": rwt,
            "relative_write_latency": rwl,
            "idle_power_cw": idlp,
            "active_power_cw": actp,
            "non_operational": nops,
        })
    result["power_states"] = psds

    return result


def parse_identify_namespace(data):
    """Parse NVMe Identify Namespace data structure (4KB)."""
    if len(data) != 4096:
        print(f"ERROR: Identify Namespace data is {len(data)} bytes, expected 4096")
        return None

    result = {}

    # Offset 0x00: NSZE (8 bytes)
    result["NSZE"] = struct.unpack_from("<Q", data, 0)[0]
    # Offset 0x08: NCAP (8 bytes)
    result["NCAP"] = struct.unpack_from("<Q", data, 8)[0]
    # Offset 0x10: NUSE (8 bytes)
    result["NUSE"] = struct.unpack_from("<Q", data, 16)[0]
    # Offset 0x18: NSFEAT
    result["NSFEAT"] = data[24]
    # Offset 0x19: NLBAF (0-based)
    result["NLBAF"] = data[25]
    # Offset 0x1A: FLBAS
    result["FLBAS"] = data[26]
    result["FLBAS_format_index"] = data[26] & 0xF
    result["FLBAS_metadata_in_extended"] = bool(data[26] & 0x10)
    # Offset 0x1B: MC
    result["MC"] = data[27]
    # Offset 0x1C: DPC
    result["DPC"] = data[28]
    # Offset 0x1D: DPS
    result["DPS"] = data[29]
    # Offset 0x1E: NMIC
    result["NMIC"] = data[30]
    # Offset 0x1F: RESCAP
    result["RESCAP"] = data[31]

    # Offset 0x20: FPI
    result["FPI"] = data[32]

    # Offset 0x65: NGUID (16 bytes)
    nguid = data[0x68:0x78]
    result["NGUID"] = nguid.hex()

    # Offset 0x78: EUI64 (8 bytes)
    eui64 = data[0x78:0x80]
    result["EUI64"] = eui64.hex()

    # LBA Formats (offset 0x80, 4 bytes each)
    lba_formats = []
    for i in range(result["NLBAF"] + 1):
        lbaf_offset = 0x80 + (i * 4)
        lbaf = struct.unpack_from("<I", data, lbaf_offset)[0]
        ms = lbaf & 0xFFFF
        lbads = (lbaf >> 16) & 0xFF
        rp = (lbaf >> 24) & 0x3
        lba_formats.append({
            "index": i,
            "metadata_size": ms,
            "lba_data_size_log2": lbads,
            "lba_data_size_bytes": 1 << lbads if lbads else 0,
            "relative_performance": rp,
            "rp_desc": {0: "Best", 1: "Better", 2: "Good", 3: "Degraded"}.get(rp, "?"),
        })
    result["LBA_formats"] = lba_formats

    # Compute capacity in human-readable
    active_format = result["FLBAS_format_index"]
    if active_format < len(lba_formats):
        sector_size = lba_formats[active_format]["lba_data_size_bytes"]
        result["LBASize"] = sector_size
        result["capacity_bytes"] = result["NSZE"] * sector_size
        result["capacity_GB"] = round(result["capacity_bytes"] / (1000 ** 3), 2)
        result["capacity_GiB"] = round(result["capacity_bytes"] / (1024 ** 3), 2)
    else:
        result["LBASize"] = 0
        result["capacity_bytes"] = 0

    return result


def validate(ctrl, ns):
    """Validate extracted identify data. Returns (pass_count, fail_count, warnings)."""
    passes = 0
    fails = 0
    warnings = []

    def check(name, condition, detail=""):
        nonlocal passes, fails
        status = "PASS" if condition else "FAIL"
        if condition:
            passes += 1
        else:
            fails += 1
        suffix = f" -- {detail}" if detail else ""
        print(f"  [{status}] {name}{suffix}")

    print("\nIdentify Controller Validation:")
    check("VID = 0x126F", ctrl["VID"] == 0x126F, f"got 0x{ctrl['VID']:04X}")
    check("SSVID = 0x126F", ctrl["SSVID"] == 0x126F, f"got 0x{ctrl['SSVID']:04X}")
    check("Model contains 'NE-256'", "NE-256" in ctrl["MN"], f"got '{ctrl['MN']}'")
    check("Serial non-empty", len(ctrl["SN"].strip()) > 0, f"got '{ctrl['SN']}'")
    check("FW revision non-empty", len(ctrl["FR"].strip()) > 0, f"got '{ctrl['FR']}'")
    check("MDTS > 0", ctrl["MDTS"] > 0, f"got {ctrl['MDTS']} ({ctrl['MDTS_desc']})")
    check("SQES = 0x66 (64B SQ entries)", ctrl["SQES"] == 0x66, f"got 0x{ctrl['SQES']:02X}")
    check("CQES = 0x44 (16B CQ entries)", ctrl["CQES"] == 0x44, f"got 0x{ctrl['CQES']:02X}")
    check("NN >= 1 (namespaces)", ctrl["NN"] >= 1, f"got {ctrl['NN']}")
    check("NPSS >= 0 (power states)", ctrl["NPSS"] >= 0, f"got {ctrl['NPSS'] + 1} states")
    check("NVMe version >= 1.0", ctrl["VER"] != "0x00000000", f"got {ctrl['VER_string']}")

    # stornvme-specific checks from driver analysis
    check("ACL >= 3 (abort cmd limit)", ctrl["ACL"] >= 3, f"got {ctrl['ACL']}")
    check("AERL >= 3 (async event limit)", ctrl["AERL"] >= 3, f"got {ctrl['AERL']}")

    if ctrl["VWC_present"]:
        warnings.append("VWC present -- firmware must handle Flush commands")

    print("\nIdentify Namespace Validation:")
    check("NSZE > 0", ns["NSZE"] > 0, f"got {ns['NSZE']} LBAs")
    check("NCAP > 0", ns["NCAP"] > 0, f"got {ns['NCAP']} LBAs")
    check("NLBAF >= 0", ns["NLBAF"] >= 0, f"got {ns['NLBAF'] + 1} formats")
    check("LBA format valid", ns["LBASize"] in (512, 4096), f"got {ns['LBASize']} bytes")
    check("Capacity ~256GB", 200 < ns.get("capacity_GB", 0) < 300, f"got {ns.get('capacity_GB', 0)} GB")

    return passes, fails, warnings


def main():
    print("=" * 60)
    print("NVMe Identify Data Verification")
    print("=" * 60)

    # Load identify controller
    ctrl_path = os.path.join(BUILD_INPUTS_DIR, "identify_controller.bin")
    if not os.path.exists(ctrl_path):
        print(f"ERROR: {ctrl_path} not found")
        sys.exit(1)

    with open(ctrl_path, "rb") as f:
        ctrl_data = f.read()
    print(f"\nLoaded: {ctrl_path} ({len(ctrl_data)} bytes)")

    ctrl = parse_identify_controller(ctrl_data)
    if ctrl is None:
        sys.exit(1)

    # Load identify namespace
    ns_path = os.path.join(BUILD_INPUTS_DIR, "identify_namespace.bin")
    if not os.path.exists(ns_path):
        print(f"ERROR: {ns_path} not found")
        sys.exit(1)

    with open(ns_path, "rb") as f:
        ns_data = f.read()
    print(f"Loaded: {ns_path} ({len(ns_data)} bytes)")

    ns = parse_identify_namespace(ns_data)
    if ns is None:
        sys.exit(1)

    # Display key fields
    print(f"\n--- Identify Controller ---")
    print(f"  VID:      0x{ctrl['VID']:04X}")
    print(f"  SSVID:    0x{ctrl['SSVID']:04X}")
    print(f"  Serial:   '{ctrl['SN']}'")
    print(f"  Model:    '{ctrl['MN']}'")
    print(f"  FW Rev:   '{ctrl['FR']}'")
    print(f"  MDTS:     {ctrl['MDTS']} ({ctrl['MDTS_desc']})")
    print(f"  CNTLID:   {ctrl['CNTLID']}")
    print(f"  Version:  {ctrl['VER_string']}")
    print(f"  OACS:     0x{ctrl['OACS']:04X} ({', '.join(ctrl['OACS_desc'])})")
    print(f"  NN:       {ctrl['NN']}")
    print(f"  SQES:     0x{ctrl['SQES']:02X} (min={ctrl['SQES_min']}B, max={ctrl['SQES_max']}B)")
    print(f"  CQES:     0x{ctrl['CQES']:02X} (min={ctrl['CQES_min']}B, max={ctrl['CQES_max']}B)")
    print(f"  VWC:      {'Present' if ctrl['VWC_present'] else 'Not present'}")
    print(f"  NPSS:     {ctrl['NPSS'] + 1} power states")
    print(f"  WCTEMP:   {ctrl['WCTEMP']}K ({ctrl['WCTEMP_celsius']}C)")
    print(f"  CCTEMP:   {ctrl['CCTEMP']}K ({ctrl['CCTEMP_celsius']}C)")

    if ctrl["power_states"]:
        print(f"\n  Power States:")
        for ps in ctrl["power_states"]:
            nops = " [NON-OP]" if ps["non_operational"] else ""
            print(f"    PS{ps['state']}: {ps['max_power_watts']:.2f}W, "
                  f"entry={ps['entry_latency_us']}us, exit={ps['exit_latency_us']}us{nops}")

    print(f"\n--- Identify Namespace (NSID=1) ---")
    print(f"  NSZE:     {ns['NSZE']} LBAs")
    print(f"  NCAP:     {ns['NCAP']} LBAs")
    print(f"  NUSE:     {ns['NUSE']} LBAs")
    print(f"  FLBAS:    0x{ns['FLBAS']:02X} (format {ns['FLBAS_format_index']})")
    print(f"  NLBAF:    {ns['NLBAF'] + 1} format(s)")
    print(f"  Capacity: {ns.get('capacity_GB', 0)} GB ({ns.get('capacity_GiB', 0)} GiB)")
    print(f"  Sector:   {ns['LBASize']} bytes")

    if ns["LBA_formats"]:
        print(f"\n  LBA Formats:")
        for fmt in ns["LBA_formats"]:
            active = " <-- ACTIVE" if fmt["index"] == ns["FLBAS_format_index"] else ""
            print(f"    LBAF{fmt['index']}: {fmt['lba_data_size_bytes']}B sector, "
                  f"MS={fmt['metadata_size']}, RP={fmt['rp_desc']}{active}")

    # Validate
    passes, fails, warnings = validate(ctrl, ns)

    if warnings:
        print(f"\nWarnings:")
        for w in warnings:
            print(f"  - {w}")

    print(f"\nResult: {passes} passed, {fails} failed")

    # Save parsed JSON
    ctrl_json_path = os.path.join(BUILD_INPUTS_DIR, "identify_controller_parsed.json")
    with open(ctrl_json_path, "w") as f:
        json.dump(ctrl, f, indent=2, default=str)
    print(f"\nSaved: {ctrl_json_path}")

    ns_json_path = os.path.join(BUILD_INPUTS_DIR, "identify_namespace_parsed.json")
    with open(ns_json_path, "w") as f:
        json.dump(ns, f, indent=2, default=str)
    print(f"Saved: {ns_json_path}")

    if fails > 0:
        print("\nWARNING: Some validations failed -- check identify data integrity")
        sys.exit(1)
    else:
        print("\nAll validations passed -- identify data is ready for COE generation")


if __name__ == "__main__":
    main()


