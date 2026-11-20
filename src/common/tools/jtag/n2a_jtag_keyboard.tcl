# Product keyboard only. Exact names; never enumerate/program a device.
package require ::quartus::jtag
set opened 0
set locked 0
proc reply {message} { puts stderr $message; flush stderr }
proc cleanup {} {
    global opened locked
    set errors {}
    if {$locked} {
        if {[catch {device_unlock} reason]} {lappend errors $reason}
        set locked 0
    }
    if {$opened} {
        if {[catch {close_device} reason]} {lappend errors $reason}
        set opened 0
    }
    if {[llength $errors]} {error [join $errors {; }]}
}
fconfigure stdin -buffering line
fconfigure stderr -buffering none
if {[catch {
    if {[llength $quartus(args)] != 6} {error "expected cable device tck_hz instance clock_mode cable_index"}
    lassign $quartus(args) cable device tck instance clock_mode cable_index
    if {$cable eq "" || $device eq "" || ![string is integer -strict $tck] || $tck < 1 ||
        $tck > 10000000 || $instance ne "0"} {error "invalid explicit JTAG settings"}
    open_device -hardware_name $cable -device_name $device
    set opened 1
    if {$clock_mode eq "fixed-original-6mhz"} {
        # Original USB-Blaster has fixed6 MHz TCK. This is an explicit family
        # declaration, never a fallback from an unsupported JtagClock query.
        if {$tck != 6000000 || ![regexp {^USB-Blaster \[[^\]]+\]$} $cable]} {
            error "fixed clock requires exact original USB-Blaster name and6 MHz"
        }
    } elseif {$clock_mode eq "adjustable"} {
        if {![regexp {^[1-9][0-9]*$} $cable_index]} {error "explicit adjustable cable index required"}
        set suffix [file extension [info nameofexecutable]]
        set config [file join [file dirname [info nameofexecutable]] jtagconfig$suffix]
        # Bind the index used by jtagconfig to the exact name used by open_device.
        set matched 0
        foreach row [split [exec $config] "\n"] {
            if {[regexp {^([0-9]+)\) (.+)$} [string trim $row] _ index name] &&
                $index == $cable_index && $name eq $cable} {set matched 1}
        }
        if {!$matched} {error "JTAG index/name mismatch"}
        exec $config --setparam $cable_index JtagClock [expr {$tck / 1000000.0}]M
        set actual [string trim [exec $config --getparam $cable_index JtagClock]]
        if {![regexp {^([0-9.]+)([kKmM]?)$} $actual _ number unit]} {error "unknown TCK: $actual"}
        set factor [dict get {{} 1 k 1000 K 1000 m 1000000 M 1000000} $unit]
        if {$number * $factor != $tck} {error "TCK setting not confirmed: $actual"}
    } else {error "unknown explicit clock mode"}
    device_lock -timeout 1000
    set locked 1
} reason]} {
    catch {cleanup}
    reply "N2A OPEN_ERROR $reason"
    exit 1
}
reply "N2A READY"
set failed 0
while {[gets stdin line] >= 0} {
    if {$line eq "QUIT"} {break}
    if {[catch {
        if {![regexp {^SCAN ([0-9]+) ([12]) ([0-9a-fA-F]{16})$} $line _ id ir value]} {
            error "invalid product scan"
        }
        device_virtual_ir_shift -instance_index $instance -ir_value $ir -no_captured_ir_value
        set result [string trim [device_virtual_dr_shift -instance_index $instance -length 64 -dr_value $value -value_in_hex]]
        if {![regexp {^[0-9a-fA-F]{1,16}$} $result]} {error "invalid captured DR"}
    } reason]} {
        reply "N2A ERROR $reason"
        set failed 1
        break
    }
    reply "N2A OK $id $result"
}
if {[catch {cleanup} reason]} {
    reply "N2A CLOSE_ERROR $reason"
    exit 1
}
reply "N2A CLOSED"
exit $failed
