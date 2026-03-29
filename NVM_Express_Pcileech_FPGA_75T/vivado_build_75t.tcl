#
# NVM_Express_Pcileech_FPGA_75T
# discord: Moer2831
# community: https://discord.gg/sXcQhxa8qy
# RUN FROM WITHIN "Vivado Tcl Shell" WITH COMMAND:
# source vivado_build_75t.tcl -notrace
#
puts "-------------------------------------------------------"
puts " STARTING SYNTHESIS STEP.                              "
puts "-------------------------------------------------------"
set proj_dir [get_property directory [current_project]]
set board_dir [file normalize [file join $proj_dir ..]]
set proj_name [get_property name [current_project]]
set synth_dir [get_property DIRECTORY [get_runs synth_1]]
file mkdir $synth_dir
foreach mem_file {nvme_identify_ctrl.hex nvme_identify_ns.hex} {
    set src [file join $board_dir ip $mem_file]
    set dst [file join $synth_dir $mem_file]
    if {![file exists $src]} {
        error "Missing synthesis memory init file: $src"
    }
    file copy -force $src $dst
}
launch_runs -jobs 4 synth_1
puts "-------------------------------------------------------"
puts " WAITING FOR SYNTHESIS STEP TO FINISH ...              "
puts " THIS IS LIKELY TO TAKE A VERY LONG TIME.              "
puts "-------------------------------------------------------"
wait_on_run synth_1
puts "-------------------------------------------------------"
puts " STARTING IMPLEMENTATION STEP.                         "
puts "-------------------------------------------------------"
launch_runs -jobs 4 impl_1 -to_step write_bitstream
puts "-------------------------------------------------------"
puts " WAITING FOR IMPLEMENTATION STEP TO FINISH ...         "
puts " THIS IS LIKELY TO TAKE A VERY LONG TIME.              "
puts "-------------------------------------------------------"
wait_on_run impl_1
set impl_bin [file join $proj_dir "${proj_name}.runs" impl_1 nvm_express_pcileech_fpga_75t_top.bin]
set output_bin [file join $board_dir "${proj_name}.bin"]
file copy -force $impl_bin $output_bin
puts "-------------------------------------------------------"
puts " BUILD HOPEFULLY COMPLETED.                            "
puts "-------------------------------------------------------"
