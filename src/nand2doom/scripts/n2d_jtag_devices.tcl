# List the JTAG device names quartus_stp reports on one cable, one per line,
# each prefixed `N2D_DEVICE ` so n2d_jtag_device.py can ignore Quartus banners.
#   quartus_stp -t n2d_jtag_devices.tcl "USB-Blaster [1-1]"
set cable [lindex $quartus(args) 0]
if {[catch {get_device_names -hardware_name $cable} names]} {
    puts "N2D_ERROR $names"
    exit 1
}
foreach name $names {
    puts "N2D_DEVICE $name"
}
