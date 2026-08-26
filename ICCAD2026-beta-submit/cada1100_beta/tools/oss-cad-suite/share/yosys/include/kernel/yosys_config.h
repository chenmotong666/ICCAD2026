#ifndef YOSYS_CONFIG_H
#define YOSYS_CONFIG_H

// Installation parameters
#define YOSYS_PROGRAM_PREFIX ""
#define YOSYS_DATDIR "share/yosys"

// Feature toggles
#define YOSYS_ENABLE_GLOB
#define YOSYS_ENABLE_SPAWN
#define YOSYS_ENABLE_THREADS
/* #undef YOSYS_ENABLE_DLOPEN */
#define YOSYS_ENABLE_ZLIB
#define YOSYS_ENABLE_PLUGINS
#define YOSYS_ENABLE_READLINE
/* #undef YOSYS_ENABLE_EDITLINE */
#define YOSYS_ENABLE_TCL
/* #undef YOSYS_ENABLE_PYTHON */
/* #undef YOSYS_ENABLE_VERIFIC */

#endif
