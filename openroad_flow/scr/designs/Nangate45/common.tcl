global vars

proc nangate45_add_fakerams {sizes} {
    global vars

    if {![info exists vars(additional_lefs)]} {
        set vars(additional_lefs) [list]
    }
    if {![info exists vars(additional_libs)]} {
        set vars(additional_libs) [list]
    }

    foreach size $sizes {
        lappend vars(additional_lefs) "$vars(flow_home)/platforms/nangate45/lef/fakeram45_${size}.lef"
        lappend vars(additional_libs) "$vars(flow_home)/platforms/nangate45/lib/fakeram45_${size}.lib"
    }
}

proc nangate45_sorted_glob {pattern} {
    return [lsort [glob -nocomplain $pattern]]
}
