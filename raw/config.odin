package raw

ENABLED :: #config(SUPERLUMINAL_ENABLED, ODIN_OS == .Windows)
LINK_MODE :: #config(SUPERLUMINAL_LINK_MODE, "dynamic")

#assert(
	LINK_MODE == "dynamic" || LINK_MODE == "static",
	"SUPERLUMINAL_LINK_MODE must be dynamic or static",
)
when ENABLED {
	#assert(
		ODIN_OS == .Windows && (ODIN_ARCH == .amd64 || ODIN_ARCH == .i386),
		"Superluminal requires a supported Windows target",
	)
}
