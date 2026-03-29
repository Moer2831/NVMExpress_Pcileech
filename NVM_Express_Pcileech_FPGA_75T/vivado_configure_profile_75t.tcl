# NVM_Express_Pcileech_FPGA_75T
# discord: Moer2831
# community: https://discord.gg/sXcQhxa8qy
# vivado_configure_profile_75t.tcl
# ============================================================
# Reconfigures pcie_7x_0 IP core for NVMe NE-256 emulation.
# Run from Vivado Tcl console AFTER generating the project:
#   source vivado_configure_profile_75t.tcl
# ============================================================

puts "============================================================"
puts " Reconfiguring PCIe IP Core for NVMe NE-256 Emulation"
puts "============================================================"

# Get the IP instance
set ip [get_ips pcie_7x_0]
if {$ip eq ""} {
    puts "ERROR: pcie_7x_0 IP not found in project. Generate project first."
    return
}

puts "Found IP: $ip"
puts "Current Vendor ID: [get_property CONFIG.Vendor_ID $ip]"
puts "Current Device ID: [get_property CONFIG.Device_ID $ip]"

# ---- Device Identification ----
# Match real NE-256 NVMe SSD (SM2263 controller)
set_property CONFIG.Vendor_ID                   {126F}   $ip
set_property CONFIG.Device_ID                   {2263}   $ip
set_property CONFIG.Revision_ID                 {03}     $ip
set_property CONFIG.Subsystem_Vendor_ID         {126F}   $ip
set_property CONFIG.Subsystem_ID                {2263}   $ip

# ---- Class Code (NVMe) ----
set_property CONFIG.Class_Code_Base             {01}     $ip
set_property CONFIG.Class_Code_Sub              {08}     $ip
set_property CONFIG.Class_Code_Interface        {02}     $ip
set_property CONFIG.Base_Class_Menu             {Mass_storage_controller} $ip
set_property CONFIG.Sub_Class_Interface_Menu    {Non-Volatile_memory_controller} $ip

# ---- BAR0: 16KB, 64-bit, non-prefetchable ----
# Must match NVMe register space layout:
#   0x0000-0x0FFF: NVMe controller registers
#   0x1000-0x1FFF: Doorbell registers
#   0x2000-0x20FF: MSI-X table (16 entries x 16 bytes)
#   0x2100-0x210F: MSI-X PBA
#   0x2200-0x3FFF: Reserved
set_property CONFIG.Bar0_Enabled                {true}   $ip
set_property CONFIG.Bar0_Type                   {Memory} $ip
set_property CONFIG.Bar0_64bit                  {true}   $ip
set_property CONFIG.Bar0_Prefetchable           {false}  $ip
set_property CONFIG.Bar0_Scale                  {Kilobytes} $ip
set_property CONFIG.Bar0_Size                   {16}     $ip

# ---- Disable all other BARs ----
set_property CONFIG.Bar1_Enabled                {false}  $ip
set_property CONFIG.Bar2_Enabled                {false}  $ip
set_property CONFIG.Bar3_Enabled                {false}  $ip
set_property CONFIG.Bar4_Enabled                {false}  $ip
set_property CONFIG.Bar5_Enabled                {false}  $ip
set_property CONFIG.Expansion_Rom_Enabled       {false}  $ip

# ---- MSI-X: 16 entries in BAR0 ----
set_property CONFIG.MSIx_Enabled                {true}   $ip
set_property CONFIG.MSIx_Table_Size             {10}     $ip ;# 16 entries (hex 0x10)
set_property CONFIG.MSIx_Table_Offset           {2000}   $ip ;# BAR0 + 0x2000
set_property CONFIG.MSIx_Table_BIR              {BAR_0}  $ip
set_property CONFIG.MSIx_PBA_Offset             {2100}   $ip ;# BAR0 + 0x2100
set_property CONFIG.MSIx_PBA_BIR               {BAR_0}  $ip

# ---- Keep MSI as fallback ----
set_property CONFIG.MSI_Enabled                 {true}   $ip
set_property CONFIG.MSI_64b                     {true}   $ip
set_property CONFIG.Multiple_Message_Capable    {1_vector} $ip

# ---- Legacy interrupt ----
set_property CONFIG.IntX_Generation             {true}   $ip
set_property CONFIG.Legacy_Interrupt            {INTA}   $ip

# ---- Extended capabilities ----
# Disable DSN (real NE-256 doesn't have it in config space)
set_property CONFIG.DSN_Enabled                 {false}  $ip

# ---- PCIe link (hardware constraint: x1 Gen2) ----
# These stay as-is since they match the CaptainDMA 75T hardware
set_property CONFIG.Maximum_Link_Width          {X1}     $ip
set_property CONFIG.Link_Speed                  {5.0_GT/s} $ip
set_property CONFIG.Interface_Width             {64_bit} $ip
set_property CONFIG.User_Clk_Freq               {62.5}   $ip

# ---- Performance settings ----
set_property CONFIG.Max_Payload_Size            {512_bytes} $ip
set_property CONFIG.Extended_Tag_Field          {true}   $ip
set_property CONFIG.Extended_Tag_Default        {true}   $ip
set_property CONFIG.Buf_Opt_BMA                 {true}   $ip

# ---- Shadow config space access (keep enabled) ----
set_property CONFIG.PCI_CFG_Space               {true}   $ip
set_property CONFIG.PCI_CFG_Space_Addr          {2A}     $ip
set_property CONFIG.EXT_PCI_CFG_Space           {true}   $ip
set_property CONFIG.EXT_PCI_CFG_Space_Addr      {043}    $ip

# ---- Generate output products ----
puts ""
puts "New configuration:"
puts "  Vendor ID:   [get_property CONFIG.Vendor_ID $ip]"
puts "  Device ID:   [get_property CONFIG.Device_ID $ip]"
puts "  Revision:    [get_property CONFIG.Revision_ID $ip]"
puts "  Class Code:  [get_property CONFIG.Class_Code_Base $ip].[get_property CONFIG.Class_Code_Sub $ip].[get_property CONFIG.Class_Code_Interface $ip]"
puts "  BAR0 Size:   [get_property CONFIG.Bar0_Size $ip] [get_property CONFIG.Bar0_Scale $ip]"
puts "  BAR0 64-bit: [get_property CONFIG.Bar0_64bit $ip]"
puts "  MSI-X:       [get_property CONFIG.MSIx_Enabled $ip] ([get_property CONFIG.MSIx_Table_Size $ip] entries)"
puts ""

puts "Generating output products..."
generate_target all $ip

puts "============================================================"
puts " IP Core reconfiguration COMPLETE"
puts " Run synthesis to verify: launch_runs synth_1"
puts "============================================================"
