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
file copy -force ./NVM_Express_Pcileech_FPGA_75T/NVM_Express_Pcileech_FPGA_75T.runs/impl_1/nvm_express_pcileech_fpga_75t_top.bin NVM_Express_Pcileech_FPGA_75T.bin
puts "-------------------------------------------------------"
puts " BUILD HOPEFULLY COMPLETED.                            "
puts "-------------------------------------------------------"
