# List the JTAG device names quartus_stp reports on one cable, one per line,
# each prefixed `N2A_DEVICE ` so jtag_chain.py can ignore Quartus banners.
#   quartus_stp -t jtag_devices.tcl "USB-Blaster [1-1]"
set cable [lindex $quartus(args) 0]
if {[catch {get_device_names -hardware_name $cable} names]} {
    puts "N2A_ERROR $names"
    exit 1
}
foreach name $names {
    puts "N2A_DEVICE $name"
}
