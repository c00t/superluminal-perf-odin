package raw

import "core:sys/windows"

// A nonempty environment directory overrides the executable directory.
// Neither the working directory nor PATH participates in discovery.
load :: proc() -> (module: Module, err: Error) {
	when !ENABLED {
		return {}, .Disabled
	} else {
		buffer: [32768]u16 = ---
		path := raw_data(buffer[:])
		windows.SetLastError(0)
		n := int(
			windows.GetEnvironmentVariableW(
				windows.L("SUPERLUMINAL_DLL_DIR"),
				path,
				u32(len(buffer)),
			),
		)
		if n >= len(buffer) {
			return {}, .Invalid_Path
		}
		if n == 0 {
			error := windows.GetLastError()
			if error != 0 && error != windows.ERROR_ENVVAR_NOT_FOUND {
				return {}, .Load_Failed
			}
			n = int(windows.GetModuleFileNameW(nil, path, u32(len(buffer))))
			if n == 0 || n >= len(buffer) {
				return {}, .Load_Failed
			}
			for n > 0 && !path_separator(buffer[n - 1]) {
				n -= 1
			}
			buffer[n] = 0
		}
		if !absolute_path(path) {
			return {}, .Invalid_Path
		}
		name := ([^]u16)(windows.L("PerformanceAPI.dll"))[:len("PerformanceAPI.dll") + 1]
		separator := !path_separator(buffer[n - 1])
		if n + int(separator) + len(name) > len(buffer) {
			return {}, .Invalid_Path
		}
		if separator {
			buffer[n] = '\\'
			n += 1
		}
		copy(buffer[n:], name)
		return load_from(path)
	}
}

// path is a NUL-terminated absolute Windows path. Never call from DllMain.
load_from :: proc(path: [^]u16) -> (module: Module, err: Error) {
	when !ENABLED {
		return {}, .Disabled
	} else when ODIN_OS == .Windows {
		if !absolute_path(path) {
			return {}, .Invalid_Path
		}
		// Win32 requires DWORD flags. The core bit set can be narrower and rebase
		// nonzero enum minima, so preserve the symbol but use its exact C ABI.
		Load_Proc :: #type proc "system" (
			path: windows.LPCWSTR,
			file: windows.HANDLE,
			flags: windows.DWORD,
		) -> windows.HMODULE
		load := transmute(Load_Proc)windows.LoadLibraryExW
		// LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS.
		handle := load(windows.LPCWSTR(path), nil, 0x00000100 | 0x00001000)
		if handle == nil {
			return {}, .Load_Failed
		}
		// Only the successful return transfers ownership to the caller.
		published := false
		defer if !published {
			windows.FreeLibrary(handle)
		}
		address := windows.GetProcAddress(handle, "PerformanceAPI_GetAPI")
		if address == nil {
			return {}, .Missing_Get_API
		}
		get_api := Get_API_Proc(address)
		functions: Functions
		if get_api(VERSION, &functions) != 1 {
			return {}, .Version_Mismatch
		}
		if functions.SetCurrentThreadName == nil ||
		   functions.SetCurrentThreadNameN == nil ||
		   functions.BeginEvent == nil ||
		   functions.BeginEventN == nil ||
		   functions.BeginEventWide == nil ||
		   functions.BeginEventWideN == nil ||
		   functions.EndEvent == nil ||
		   functions.RegisterFiber == nil ||
		   functions.UnregisterFiber == nil ||
		   functions.BeginFiberSwitch == nil ||
		   functions.EndFiberSwitch == nil {
			return {}, .Invalid_Function_Table
		}
		published = true
		return Module{rawptr(handle), functions}, .None
	} else {
		return {}, .Disabled
	}
}

// On unload failure ownership is preserved. Never call from DllMain.
free :: proc(module: ^Module) -> bool {
	if module == nil {
		return false
	}
	if module.handle == nil {
		module^ = {}
		return true
	}
	when ENABLED && ODIN_OS == .Windows {
		if !bool(windows.FreeLibrary(windows.HMODULE(module.handle))) {
			return false
		}
		module^ = {}
		return true
	} else {
		return false
	}
}

@(private)
absolute_path :: proc(path: [^]u16) -> bool {
	if path == nil || path[0] == 0 {
		return false
	}
	// Drive-rooted paths and UNC/device paths; a single leading slash is relative.
	if path_separator(path[0]) {
		return path_separator(path[1]) && path[2] != 0
	}
	letter := ('A' <= path[0] && path[0] <= 'Z') || ('a' <= path[0] && path[0] <= 'z')
	return letter && path[1] == ':' && path_separator(path[2])
}

@(private)
path_separator :: proc(c: u16) -> bool {
	return c == '\\' || c == '/'
}
