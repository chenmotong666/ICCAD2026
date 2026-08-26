# Design Compiler offline / online driver for ICCAD2026.
# Invoked as:  dc_shell-t -f dc_opt.tcl
# Required env:
#   ENG_INPUT       input gate-level Verilog (pre-cost state)
#   ENG_OUTPUT      output gate-level Verilog
#   ENG_BLACKBOX    path to lib/dff_blackbox.v
#   ENG_LIB_DIR     directory containing the compiled .db
#   ENG_LIB_NAME    Liberty or db file name (stem used to find ${stem}.db)
#   ENG_OBJ         "min_depth" | "min_gates"
# Optional env:
#   ENG_ALLOWED     comma list of allowed lib cells (style restriction)
#   ENG_CONE_CELLS_FILE  file listing instance full_names allowed to change
#   ENG_MAX_DELAY   absolute delay target for min_depth (overrides factor)
#   ENG_DELAY_FACTOR  relative delay = factor * ENG_CURRENT_DEPTH (default 0.5)
#   ENG_CURRENT_DEPTH current contest depth (used with ENG_DELAY_FACTOR)
#   ENG_COMPILE_CMD compile_ultra (default) | compile

set input_file $::env(ENG_INPUT)
set output_file $::env(ENG_OUTPUT)
set blackbox_file $::env(ENG_BLACKBOX)
set lib_dir $::env(ENG_LIB_DIR)
set lib_name $::env(ENG_LIB_NAME)
set obj $::env(ENG_OBJ)

if {[info exists ::env(ENG_MAX_DELAY)]} {
  set max_delay $::env(ENG_MAX_DELAY)
} elseif {[info exists ::env(ENG_DELAY_FACTOR)] && [info exists ::env(ENG_CURRENT_DEPTH)]} {
  set max_delay [expr {double($::env(ENG_DELAY_FACTOR)) * double($::env(ENG_CURRENT_DEPTH))}]
  if {$max_delay < 0.2} { set max_delay 0.2 }
} else {
  set max_delay 0.2
}

set compile_cmd "compile_ultra"
if {[info exists ::env(ENG_COMPILE_CMD)] && $::env(ENG_COMPILE_CMD) ne ""} {
  set compile_cmd $::env(ENG_COMPILE_CMD)
}

set_app_var search_path [list . $lib_dir]
set lib_ref [file rootname [file tail $lib_name]]
set db_file [file join $lib_dir ${lib_ref}.db]
if {![file exists $db_file] && [file extension $lib_name] eq ".db"} {
  set db_file [file join $lib_dir $lib_name]
}
if {![file exists $db_file]} {
  echo "Error: compiled library $db_file is missing (run dc_mklib.tcl first)."
  # R42 F6: distinct exit codes for the online DC trace (3 = db missing,
  # 4 = library load/link failure, e.g. a DC version mismatch on the .db).
  exit 3
}
set_app_var link_library [list $db_file]
set_app_var target_library [list $db_file]

read_file -format verilog $blackbox_file
read_file -format verilog $input_file
current_design top
if {[catch {link} link_err]} {
  echo "Error: design link failed: $link_err"
  exit 4
}

# DFF instances are combinational boundaries: never touch or retime them.
set dff_cells [get_cells -hier * -filter "ref_name == dff"]
if {[sizeof_collection $dff_cells] > 0} {
  set_dont_touch $dff_cells
  set_dont_retime $dff_cells
}

# Cone scope: freeze every cell not listed in the file.
if {[info exists ::env(ENG_CONE_CELLS_FILE)] && [file exists $::env(ENG_CONE_CELLS_FILE)]} {
  set fp [open $::env(ENG_CONE_CELLS_FILE) r]
  set keep_list {}
  while {[gets $fp line] >= 0} {
    set line [string trim $line]
    if {$line ne ""} { lappend keep_list $line }
  }
  close $fp
  set all_cells [get_cells -hier * -filter "ref_name != dff"]
  foreach_in_collection c $all_cells {
    set fn [get_attribute $c full_name]
    if {[lsearch -exact $keep_list $fn] < 0} {
      set_dont_touch $c
    }
  }
}

# Style restriction: forbid mapping onto cells outside the allowed subset.
# Empty ENG_ALLOWED means the whole library is usable (no set_dont_use).
if {[info exists ::env(ENG_ALLOWED)] && $::env(ENG_ALLOWED) ne ""} {
  set allow [split $::env(ENG_ALLOWED) ","]
  foreach_in_collection lib_cell [get_lib_cells */*] {
    set cell_name [get_attribute $lib_cell name]
    if {[lsearch -exact $allow $cell_name] < 0} {
      set_dont_use $lib_cell
    }
  }
}

# Rename persistence: protect objects a prior rename request named in the
# prompt.  Presto may renumber primitive instance names at read time, so
# NET names are the reliable anchor; set_dont_touch on the renamed gate's
# output net keeps the net (and its driver) from being collapsed, and
# restore_renames.py re-labels the surviving driver afterwards.
if {[info exists ::env(ENG_PROTECT_CELLS)] && $::env(ENG_PROTECT_CELLS) ne ""} {
  foreach nm [split $::env(ENG_PROTECT_CELLS) ","] {
    set nm [string trim $nm]
    if {$nm ne ""} { catch { set_dont_touch [get_cells $nm] } }
  }
}
if {[info exists ::env(ENG_PROTECT_NETS)] && $::env(ENG_PROTECT_NETS) ne ""} {
  foreach nm [split $::env(ENG_PROTECT_NETS) ","] {
    set nm [string trim $nm]
    if {$nm ne ""} { catch { set_dont_touch [get_nets $nm] } }
  }
}

if {$obj eq "min_depth"} {
  # Every gate has unit delay (1.0), so max_delay is a depth target.
  set dff_q [get_pins -of_objects $dff_cells -filter "name == Q"]
  set dff_d [get_pins -of_objects $dff_cells -filter "name == D"]
  set_max_delay $max_delay -from [all_inputs] -to [all_outputs]
  if {[sizeof_collection $dff_q] > 0 && [sizeof_collection $dff_d] > 0} {
    set_max_delay $max_delay -from $dff_q -to $dff_d
    set_max_delay $max_delay -from [all_inputs] -to $dff_d
    set_max_delay $max_delay -from $dff_q -to [all_outputs]
  }
}

if {$compile_cmd eq "compile"} {
  compile -map_effort medium
} else {
  compile_ultra -no_autoungroup -no_boundary_optimization
}

write -format verilog -hierarchy -output $output_file
exit
