package superluminal_perf

import "core:path/filepath"
import "core:sys/windows"
import "raw"
// Externally serialize lifecycle calls. Workers must finish before shutdown.
init :: proc(dll_path: string = "", allocator := context.allocator) -> Error {
	when !ENABLED {
		return .Disabled
	} else when LINK_MODE == "static" {
		return .None
	} else when ODIN_OS == .Windows {
		if is_initialized() {
			return .Already_Initialized
		}
		loaded: raw.Module
		err: Error
		if len(dll_path) == 0 {
			loaded, err = raw.load()
		} else {
			if !filepath.is_abs(dll_path) {
				return .Invalid_Path
			}
			for byte in dll_path {
				if byte == 0 {
					return .Invalid_Path
				}
			}
			wide := windows.utf8_to_wstring_alloc(dll_path, allocator)
			if wide == nil {
				return .Path_Conversion_Failed
			}
			defer free(rawptr(wide), allocator)
			loaded, err = raw.load_from(([^]u16)(wide))
		}
		if err != .None {
			return err
		}
		module = loaded
		return .None
	} else {
		return .Disabled
	}
}
