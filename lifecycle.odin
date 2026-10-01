package superluminal_perf

import "raw"


ENABLED :: raw.ENABLED
LINK_MODE :: raw.LINK_MODE
DEFAULT_COLOR :: raw.DEFAULT_COLOR
Error :: raw.Error

@(private)
module: raw.Module

when ODIN_OS != .Windows {
	init :: proc(dll_path: string = "", allocator := context.allocator) -> Error {
		return .Disabled
	}
}


is_initialized :: proc() -> bool {
	when !ENABLED {
		return false
	} else when LINK_MODE == "static" {
		return true
	} else {
		return module.handle != nil
	}
}

shutdown :: proc() -> bool {
	when ENABLED && LINK_MODE == "dynamic" {
		return raw.free(&module)
	} else {
		return true
	}
}
