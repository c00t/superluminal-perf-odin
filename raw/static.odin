package raw

when ENABLED && LINK_MODE == "static" {
	when ODIN_ARCH == .amd64 {
		@(private)
		ARCH_DIRECTORY :: "x64"
	} else when ODIN_ARCH == .i386 {
		@(private)
		ARCH_DIRECTORY :: "x86"
	}
	@(private)
	LIBRARY_PATH :: "superluminal_sdk:lib/" + ARCH_DIRECTORY + "/PerformanceAPI_MT.lib"
	foreign import performance_api {LIBRARY_PATH}

	@(default_calling_convention = "c", link_prefix = "PerformanceAPI_")
	foreign performance_api {
		SetCurrentThreadName :: proc(name: cstring) ---
		SetCurrentThreadName_N :: proc(name: [^]u8, name_length: u16) ---
		BeginEvent :: proc(id, data: cstring, color: u32) ---
		BeginEvent_N :: proc(id: [^]u8, id_length: u16, data: [^]u8, data_length: u16, color: u32) ---
		BeginEvent_Wide :: proc(id, data: [^]u16, color: u32) ---
		BeginEvent_Wide_N :: proc(id: [^]u16, id_length: u16, data: [^]u16, data_length: u16, color: u32) ---
		EndEvent :: proc() -> Suppress_Tail_Call_Optimization ---
		RegisterFiber :: proc(fiber_id: u64) ---
		UnregisterFiber :: proc(fiber_id: u64) ---
		BeginFiberSwitch :: proc(current_fiber_id, new_fiber_id: u64) ---
		EndFiberSwitch :: proc(fiber_id: u64) ---
	}
}
