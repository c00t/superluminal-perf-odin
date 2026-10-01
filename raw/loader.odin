package raw


Error :: enum {
	None,
	Disabled,
	Already_Initialized,
	Invalid_Path,
	Path_Conversion_Failed,
	Load_Failed,
	Missing_Get_API,
	Version_Mismatch,
	Invalid_Function_Table,
}

// One owner per handle. Borrowed functions must not outlive this module.
Module :: struct {
	handle:    rawptr,
	functions: Functions,
}

when ODIN_OS != .Windows {
	load :: proc() -> (module: Module, err: Error) {
		return {}, .Disabled
	}

	load_from :: proc(path: [^]u16) -> (module: Module, err: Error) {
		return {}, .Disabled
	}

	free :: proc(module: ^Module) -> bool {
		if module == nil || module.handle != nil {
			return false
		}
		module^ = {}
		return true
	}
}
