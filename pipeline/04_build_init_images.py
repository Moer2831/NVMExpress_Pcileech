#!/usr/bin/env python3
"""
NVM_Express_Pcileech_FPGA_75T
discord: Moer_2831
community: https://discord.gg/sXcQhxa8qy

Generate memory init images for NVMe NE-256 emulation from real reference captures.

Unlike the sibling project's generate_nvme_coe.py which uses default capability layouts,
this script uses the actual 4KB config space binary extracted from the real device.

Prerequisites:
    - 01_capture_config_profile.py (produces config_space_4k.bin)
    - 02_capture_bar0_profile.py (produces nvme_bar0.bin)
    - 03_verify_identity_profile.py (validates identify data)

Usage:
    python 04_build_init_images.py
    python 04_build_init_images.py --output-dir ../NVM_Express_Pcileech_FPGA_75T/ip
"""

import argparse
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_INPUTS_DIR = os.path.join(SCRIPT_DIR, "build_inputs")
DEFAULT_OUTPUT = os.path.join(SCRIPT_DIR, "..", "NVM_Express_Pcileech_FPGA_75T", "ip")


def write_coe(filepath, dwords, radix=16):
    """Write Xilinx COE file from list of 32-bit DWORDs."""
    with open(filepath, "w") as f:
        f.write(f"memory_initialization_radix={radix};\n")
        f.write("memory_initialization_vector=\n\n")
        for i in range(0, len(dwords), 4):
            chunk = dwords[i:i + 4]
            line = ",".join(f"{dw:08x}" for dw in chunk)
            if i + 4 >= len(dwords):
                line += "\n;"
            else:
                line += ","
            f.write(line + "\n")
            if (i + 4) % 64 == 0 and i + 4 < len(dwords):
                f.write("\n")
    print(f"  Written: {filepath} ({len(dwords)} DWORDs)")


def write_hex(filepath, dwords):
    """Write $readmemh compatible hex file."""
    with open(filepath, "w") as f:
        for dw in dwords:
            f.write(f"{dw:08x}\n")
    print(f"  Written: {filepath} ({len(dwords)} DWORDs, $readmemh)")


# =============================================================================
# Config Space COE - From real extracted binary
# =============================================================================

def generate_cfgspace_coe(output_dir):
    """Generate nvmexp_cfgspace.coe from a real extracted config space binary."""
    bin_path = os.path.join(BUILD_INPUTS_DIR, "config_space_4k.bin")

    if not os.path.exists(bin_path):
        print(f"ERROR: {bin_path} not found. Run 01_capture_config_profile.py first.")
        return False

    with open(bin_path, "rb") as f:
        raw = f.read()

    if len(raw) != 4096:
        print(f"ERROR: Config space binary is {len(raw)} bytes, expected 4096")
        return False

    # Convert to 1024 DWORDs
    dwords = []
    for i in range(0, 4096, 4):
        dw = struct.unpack_from("<I", raw, i)[0]
        dwords.append(dw)

    # Modifications for FPGA shadow config space:

    # DW1 (Command/Status): Clear command register bits that host will set
    # Keep status bits, clear command to 0 (host enables MemSpace + BusMaster)
    status = (dwords[1] >> 16) & 0xFFFF
    # Set capabilities list bit in status (bit 4) to indicate cap pointer is valid
    status |= (1 << 4)
    dwords[1] = (status << 16) | 0x0000  # Command = 0, host will enable

    # DW4 (BAR0 low): Keep type bits, zero address bits
    # BAR0 type bits: [0]=memory, [2:1]=type (10=64-bit), [3]=prefetchable
    bar0_type_bits = dwords[4] & 0xF
    dwords[4] = bar0_type_bits  # Address zeroed, type preserved

    # DW5 (BAR0 high): Zero for 64-bit BAR upper address
    dwords[5] = 0x00000000

    # DW15 (Interrupt): Keep interrupt pin, clear interrupt line (host assigns)
    int_pin = (dwords[15] >> 8) & 0xFF
    dwords[15] = (int_pin << 8)  # Int line = 0, host assigns

    # Fill unused extended config space entries with address-echo debug pattern
    # (only for DWORDs that are already zero beyond the last extended capability)
    json_path = os.path.join(BUILD_INPUTS_DIR, "config_space_parsed.json")
    last_ext_cap_end = 0x100  # Start of extended config
    if os.path.exists(json_path):
        with open(json_path) as f:
            parsed = json.load(f)
        for ext_cap in parsed.get("extended_capabilities", []):
            cap_offset = ext_cap.get("offset_int", 0)
            cap_size = ext_cap.get("size_bytes", 4)
            end = cap_offset + cap_size
            if end > last_ext_cap_end:
                last_ext_cap_end = end

    # Round up to DWORD boundary
    last_ext_dw = (last_ext_cap_end + 3) // 4

    # Fill remaining zeros with debug pattern (only if the real device had zeros there)
    for i in range(last_ext_dw, 1024):
        if dwords[i] == 0x00000000:
            dwords[i] = 0xFFFFF000 | (i * 4)

    # Also fill any zero gaps in standard config space (0x40-0xFF range) that aren't
    # part of a capability structure with the debug pattern
    cap_ranges = set()
    if os.path.exists(json_path):
        with open(json_path) as f:
            parsed = json.load(f)
        for cap in parsed.get("capabilities", []):
            cap_offset = cap.get("offset_int", 0)
            cap_size = cap.get("size_bytes", 4)
            for byte_off in range(cap_offset, cap_offset + cap_size):
                cap_ranges.add(byte_off // 4)

    for i in range(16, 64):  # DW16-63 = offsets 0x40-0xFF
        if i not in cap_ranges and dwords[i] == 0x00000000:
            dwords[i] = 0xFFFFF000 | (i * 4)

    # Validate
    vid = dwords[0] & 0xFFFF
    did = (dwords[0] >> 16) & 0xFFFF
    print(f"  Config space: VID=0x{vid:04X} DID=0x{did:04X}")

    write_coe(os.path.join(output_dir, "nvmexp_cfgspace.coe"), dwords)
    return True


# =============================================================================
# Write Mask COE - Derived from REAL capability chain layout
# =============================================================================

def generate_writemask_coe(output_dir):
    """Generate nvmexp_cfgspace_mask.coe from the real config space capability layout."""
    masks = [0x00000000] * 1024  # Default: read-only

    # Load parsed config space to find actual capability offsets
    json_path = os.path.join(BUILD_INPUTS_DIR, "config_space_parsed.json")
    bin_path = os.path.join(BUILD_INPUTS_DIR, "config_space_4k.bin")
    bar_info_path = os.path.join(BUILD_INPUTS_DIR, "bar_info.json")

    if not os.path.exists(json_path):
        print(f"WARNING: {json_path} not found, using generic writemasks")

    parsed = {}
    if os.path.exists(json_path):
        with open(json_path) as f:
            parsed = json.load(f)

    # --- Standard Header Writemasks ---

    # DW1: Command register (low 16 bits): bits 0,1,2,6,8,10 writable
    masks[1] = 0x00000547

    # DW3: Cache Line Size (byte 0) writable
    masks[3] = 0x000000FF

    # DW4: BAR0 - address bits above BAR size writable
    bar0_size = 16 * 1024  # Default 16KB
    if os.path.exists(bar_info_path):
        with open(bar_info_path) as f:
            bi = json.load(f)
        bar0_size = bi.get("estimated_size", bar0_size)

    bar0_mask = ~(bar0_size - 1) & 0xFFFFFFFF
    masks[4] = bar0_mask & 0xFFFFFFF0  # Keep type bits (3:0) read-only

    # DW5: BAR0 upper 32 bits (all writable for 64-bit BAR)
    masks[5] = 0xFFFFFFFF

    # DW15: Interrupt Line (byte 0) writable
    masks[15] = 0x000000FF

    # --- Capability-specific writemasks from REAL layout ---
    for cap in parsed.get("capabilities", []):
        cap_id = cap.get("id_int", 0)
        cap_offset = cap.get("offset_int", 0)
        cap_dw = cap_offset // 4

        if cap_id == 0x01:  # PM
            # PM Cap header: read-only
            # PMCSR (cap+4): PME_Status(W1C bit 15), PME_En(bit 8), PowerState(bits 1:0)
            masks[cap_dw + 1] = 0x00008103

        elif cap_id == 0x05:  # MSI
            # MSI Control: MSI Enable bit (bit 16 of DW)
            masks[cap_dw] = 0x00010000
            # Message Address (cap+4): DWORD-aligned
            masks[cap_dw + 1] = 0xFFFFFFFC
            # Message Upper Address (cap+8) if 64-bit
            if cap.get("64bit_capable"):
                masks[cap_dw + 2] = 0xFFFFFFFF
                # Message Data (cap+12)
                masks[cap_dw + 3] = 0x0000FFFF
            else:
                # Message Data (cap+8)
                masks[cap_dw + 2] = 0x0000FFFF

        elif cap_id == 0x10:  # PCIe
            # PCIe cap header: read-only
            # Device Control (cap+8): writable control bits
            masks[cap_dw + 2] = 0x0000FFF0
            # Link Control (cap+16): writable bits
            masks[cap_dw + 4] = 0x00000FFF
            # Device Control 2 (cap+40 = cap_dw+10)
            masks[cap_dw + 10] = 0x0000FFFF

        elif cap_id == 0x11:  # MSI-X
            # MSI-X Control: Enable(bit 31) + Function Mask(bit 30) writable
            masks[cap_dw] = 0xC0000000
            # Table offset/BIR and PBA offset/BIR: read-only

    # --- Extended Config Space (0x100+) ---
    for ext_cap in parsed.get("extended_capabilities", []):
        ext_id = ext_cap.get("id_int", 0)
        ext_offset = ext_cap.get("offset_int", 0)
        ext_dw = ext_offset // 4

        if ext_id == 0x0001:  # AER
            # Header: read-only
            # Uncorrectable Error Status (cap+4): W1C
            masks[ext_dw + 1] = 0xFFFFFFFF
            # Uncorrectable Error Mask (cap+8): writable
            masks[ext_dw + 2] = 0xFFFFFFFF
            # Uncorrectable Error Severity (cap+12): writable
            masks[ext_dw + 3] = 0xFFFFFFFF
            # Correctable Error Status (cap+16): W1C
            masks[ext_dw + 4] = 0xFFFFFFFF
            # Correctable Error Mask (cap+20): writable
            masks[ext_dw + 5] = 0xFFFFFFFF
            # Advanced Error Capabilities (cap+24): writable
            masks[ext_dw + 6] = 0x000000FF

        elif ext_id == 0x0003:  # DSN
            # Entirely read-only (serial number)
            pass

        elif ext_id == 0x001E:  # L1 PM Substates
            # L1SS Control 1 (cap+8): writable
            masks[ext_dw + 2] = 0xFFFFFFFF
            # L1SS Control 2 (cap+12): writable
            masks[ext_dw + 3] = 0xFFFFFFFF

    # Remaining extended config space: make writable for shadow flexibility
    for i in range(256, 1024):
        if masks[i] == 0x00000000:
            masks[i] = 0xFFFFFFFF

    write_coe(os.path.join(output_dir, "nvmexp_cfgspace_mask.coe"), masks)
    return True


# =============================================================================
# BAR0 COE - From REAL NVMe register dump
# =============================================================================

def generate_bar0_coe(output_dir):
    """Generate nvmexp_bar0.coe from a real NVMe BAR0 register dump."""
    dwords = [0] * 1024  # 4KB

    bin_path = os.path.join(BUILD_INPUTS_DIR, "nvme_bar0.bin")
    json_path = os.path.join(BUILD_INPUTS_DIR, "nvme_registers_parsed.json")

    if os.path.exists(bin_path):
        # Use real BAR0 data for the first 64 bytes (NVMe controller registers)
        with open(bin_path, "rb") as f:
            bar0_raw = f.read()

        # Copy first 64 bytes (16 DWORDs) of NVMe registers
        for i in range(min(16, len(bar0_raw) // 4)):
            dwords[i] = struct.unpack_from("<I", bar0_raw, i * 4)[0]

        print(f"  BAR0: Loaded real register values from {bin_path}")

        # Override CC and CSTS to power-on defaults (disabled/not ready)
        # The firmware will manage the CC.EN -> CSTS.RDY transition
        dwords[5] = 0x00000000   # CC = 0 (controller disabled)
        dwords[7] = 0x00000000   # CSTS = 0 (not ready)
        dwords[8] = 0x00000000   # NSSR = 0
        dwords[9] = 0x00000000   # AQA = 0 (host sets this)
        dwords[10] = 0x00000000  # ASQ low = 0 (host sets this)
        dwords[11] = 0x00000000  # ASQ high = 0
        dwords[12] = 0x00000000  # ACQ low = 0 (host sets this)
        dwords[13] = 0x00000000  # ACQ high = 0

    elif os.path.exists(json_path):
        # Fallback: reconstruct from parsed JSON
        with open(json_path) as f:
            regs = json.load(f)

        cap_raw = regs.get("CAP", {}).get("raw", "0x0")
        cap = int(cap_raw, 16)
        dwords[0] = cap & 0xFFFFFFFF
        dwords[1] = (cap >> 32) & 0xFFFFFFFF

        vs_raw = regs.get("VS", {}).get("raw", "0x0")
        dwords[2] = int(vs_raw, 16)

        print(f"  BAR0: Reconstructed from parsed JSON")

    else:
        # Last resort: SM2263 defaults
        print(f"  WARNING: No BAR0 data found, using SM2263 defaults")
        # CAP: MQES=4095, CQR=1, TO=40(20s), CSS=NVM, MPSMIN=0(4KB), MPSMAX=2(16KB)
        dwords[0] = 0x28010FFF  # CAP low
        dwords[1] = 0x00200020  # CAP high
        dwords[2] = 0x00010300  # VS = 1.3.0

    # Print what we're writing
    cap = (dwords[1] << 32) | dwords[0]
    vs = dwords[2]
    print(f"  CAP:  0x{cap:016X} (MQES={cap & 0xFFFF}, TO={((cap >> 24) & 0xFF) * 500}ms)")
    print(f"  VS:   0x{vs:08X} (NVMe {(vs >> 16) & 0xFFFF}.{(vs >> 8) & 0xFF}.{vs & 0xFF})")
    print(f"  CC:   0x{dwords[5]:08X} (disabled)")
    print(f"  CSTS: 0x{dwords[7]:08X} (not ready)")

    write_coe(os.path.join(output_dir, "nvmexp_bar0.coe"), dwords)
    return True


# =============================================================================
# Identify COE files - From raw binaries
# =============================================================================

def generate_identify_coe(output_dir):
    """Generate NVMe Identify Controller and Namespace COE files."""
    success = True

    for name, filename in [("Controller", "identify_controller.bin"),
                            ("Namespace", "identify_namespace.bin")]:
        bin_path = os.path.join(BUILD_INPUTS_DIR, filename)
        if not os.path.exists(bin_path):
            print(f"  WARNING: {bin_path} not found, skipping Identify {name} COE")
            success = False
            continue

        with open(bin_path, "rb") as f:
            raw = f.read()

        if len(raw) != 4096:
            print(f"  WARNING: {filename} is {len(raw)} bytes, expected 4096")
            success = False
            continue

        dwords = []
        for i in range(0, 4096, 4):
            dw = struct.unpack_from("<I", raw, i)[0]
            dwords.append(dw)

        coe_name = f"nvme_identify_{'ctrl' if 'controller' in filename else 'ns'}"
        write_coe(os.path.join(output_dir, f"{coe_name}.coe"), dwords)
        write_hex(os.path.join(output_dir, f"{coe_name}.hex"), dwords)

    return success


# =============================================================================
# Main
# =============================================================================

def main():
    global BUILD_INPUTS_DIR

    parser = argparse.ArgumentParser(description="Generate NVMe init images from real build inputs")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT, help="Output directory for COE files")
    parser.add_argument("--build-inputs-dir", default=BUILD_INPUTS_DIR, help="Build inputs directory")
    args = parser.parse_args()

    BUILD_INPUTS_DIR = os.path.abspath(args.build_inputs_dir)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("NVMe Init Image Generator (from real build inputs)")
    print("=" * 60)
    print(f"Build inputs: {BUILD_INPUTS_DIR}")
    print(f"Output:     {output_dir}")

    # Check what data is available
    has_config = os.path.exists(os.path.join(BUILD_INPUTS_DIR, "config_space_4k.bin"))
    has_bar0 = os.path.exists(os.path.join(BUILD_INPUTS_DIR, "nvme_bar0.bin"))
    has_id_ctrl = os.path.exists(os.path.join(BUILD_INPUTS_DIR, "identify_controller.bin"))
    has_id_ns = os.path.exists(os.path.join(BUILD_INPUTS_DIR, "identify_namespace.bin"))

    print(f"\nData availability:")
    print(f"  Config space 4KB: {'YES' if has_config else 'NO -- run 01_capture_config_profile.py'}")
    print(f"  BAR0 registers:   {'YES' if has_bar0 else 'NO -- run 02_capture_bar0_profile.py (will use defaults)'}")
    print(f"  Identify Ctrl:    {'YES' if has_id_ctrl else 'NO'}")
    print(f"  Identify NS:      {'YES' if has_id_ns else 'NO'}")

    if not has_config:
        print("\nERROR: Config space binary is required. Run extraction first.")
        print("The whole point is to use REAL data, not defaults.")
        sys.exit(1)

    # Generate
    results = {}
    print("\n--- Generating COE files ---")

    print("\n[1/4] Config Space (nvmexp_cfgspace.coe)")
    results["cfgspace"] = generate_cfgspace_coe(output_dir)

    print("\n[2/4] Write Mask (nvmexp_cfgspace_mask.coe)")
    results["writemask"] = generate_writemask_coe(output_dir)

    print("\n[3/4] BAR0 NVMe Registers (nvmexp_bar0.coe)")
    results["bar0"] = generate_bar0_coe(output_dir)

    print("\n[4/4] Identify Data (nvme_identify_ctrl/ns.coe)")
    results["identify"] = generate_identify_coe(output_dir)

    # Summary
    print(f"\n{'=' * 60}")
    print("Generation Summary:")
    all_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "FAILED/WARNING"
        print(f"  {name}: {status}")
        if not ok:
            all_ok = False

    print(f"\nOutput files:")
    for f in sorted(os.listdir(output_dir)):
        if f.endswith((".coe", ".hex")):
            fpath = os.path.join(output_dir, f)
            print(f"  {f} ({os.path.getsize(fpath)} bytes)")

    if all_ok:
        print("\nAll init images generated successfully from real build inputs.")
    else:
        print("\nSome files had warnings -- check output above.")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()


