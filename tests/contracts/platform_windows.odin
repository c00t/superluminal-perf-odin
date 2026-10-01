package contracts

import perf "../.."
import raw "../../raw"
import "core:fmt"
import "core:mem"
import "core:os"
import win "core:sys/windows"
import "core:testing"

contracts_enabled :: proc(t: ^testing.T) {
	when perf.ENABLED {
		module, err := raw.load_from(nil)
		testing.expect_value(t, err, raw.Error.Invalid_Path)
		testing.expect(t, module.handle == nil && all_absent(module.functions))
		expect_load_error(t, "", .Invalid_Path)
		expect_load_error(t, "relative.dll", .Invalid_Path)
		when perf.LINK_MODE == "static" {
			testing.expect(t, perf.is_initialized())
			testing.expect_value(t, perf.init(), perf.Error.None)
			testing.expect(t, perf.shutdown())
		} else {
			testing.expect(t, !perf.is_initialized())
			cwd, cwd_err := os.get_working_directory(context.allocator)
			defer delete(cwd)
			if !testing.expect(t, cwd_err == nil) {return}
			nul_path := fmt.aprintf("%s\\invalid\x00suffix.dll", cwd)
			malformed_path := fmt.aprintf("%s\\invalid\xff.dll", cwd)
			allocation_path := fmt.aprintf("%s\\allocation.dll", cwd)
			defer delete(nul_path)
			defer delete(malformed_path)
			defer delete(allocation_path)
			for path in ([]string{"relative.dll", nul_path}) {
				testing.expect_value(t, perf.init(path), perf.Error.Invalid_Path)
				testing.expect(t, !perf.is_initialized())
			}
			testing.expect_value(t, perf.init(malformed_path), perf.Error.Path_Conversion_Failed)
			testing.expect_value(
				t,
				perf.init(allocation_path, mem.nil_allocator()),
				perf.Error.Path_Conversion_Failed,
			)
			testing.expect(t, !perf.is_initialized())
		}
		dll := os.get_env("SUPERLUMINAL_TEST_DLL", context.allocator)
		defer delete(dll)
		if dll == "" {
			fmt.println("SKIPPED: real-DLL contracts (SUPERLUMINAL_TEST_DLL is unset)")
			return
		}
		missing := fmt.aprintf("%s.missing-contract-dll", dll)
		defer delete(missing)
		expect_load_error(t, missing, .Load_Failed)
		when perf.LINK_MODE == "dynamic" {
			testing.expect_value(t, perf.init(missing), perf.Error.Load_Failed)
			testing.expect(t, !perf.is_initialized())
		}
		// Resolve the system DLL through the OS, not a fixed installation directory.
		system: [32768]u16
		n := win.GetSystemDirectoryW(raw_data(system[:]), u32(len(system)))
		if testing.expect(t, n > 0 && int(n) + 13 < len(system)) {
			suffix := [?]u16{'\\', 'k', 'e', 'r', 'n', 'e', 'l', '3', '2', '.', 'd', 'l', 'l', 0}
			copy(system[int(n):], suffix[:])
			system_module, system_err := raw.load_from(raw_data(system[:]))
			testing.expect_value(t, system_err, raw.Error.Missing_Get_API)
			testing.expect(t, system_module.handle == nil && all_absent(system_module.functions))
			testing.expect(t, raw.free(&system_module))
		}
		keys := [3]string {
			"SUPERLUMINAL_TEST_OPPOSITE_DLL",
			"SUPERLUMINAL_TEST_VERSION_DLL",
			"SUPERLUMINAL_TEST_TABLE_DLL",
		}
		errors := [3]raw.Error{.Load_Failed, .Version_Mismatch, .Invalid_Function_Table}
		for key, i in keys {
			path := os.get_env(key, context.allocator)
			if testing.expect(
				t,
				path != "",
				"Real-DLL contract run requires all fixture environment variables",
			) {
				expect_load_error(t, path, errors[i], i != 0)
			}
			delete(path)
		}
		wide := win.utf8_to_wstring_alloc(dll, context.allocator)
		defer delete(wide, context.allocator)
		if !testing.expect(t, wide != nil) {return}
		real_module, load_err := raw.load_from(cast([^]u16)wide)
		if !testing.expect_value(t, load_err, raw.Error.None) {return}
		testing.expect(t, real_module.handle != nil && all_present(real_module.functions))
		get_api := cast(raw.Get_API_Proc)win.GetProcAddress(
			cast(win.HMODULE)real_module.handle,
			"PerformanceAPI_GetAPI",
		)
		if testing.expect(t, get_api != nil) {
			table: raw.Functions
			testing.expect_value(t, get_api(raw.VERSION + 1, &table), i32(0))
			testing.expect_value(t, get_api(raw.VERSION, &table), i32(1))
			testing.expect(t, all_present(table))
		}
		testing.expect(t, raw.free(&real_module))
		testing.expect(t, real_module.handle == nil && all_absent(real_module.functions))
		testing.expect(t, raw.free(&real_module))
		when perf.LINK_MODE == "dynamic" {
			if !testing.expect_value(t, perf.init(dll), perf.Error.None) {return}
			defer perf.shutdown()
			testing.expect(t, perf.is_initialized())
			testing.expect_value(t, perf.init(missing), perf.Error.Already_Initialized)
			// A rejected second initialization preserves a usable first module.
			perf.begin_event("Contract retained module")
			perf.end_event()
			testing.expect(t, perf.shutdown())
			testing.expect(t, !perf.is_initialized())
			testing.expect(t, perf.shutdown())
			testing.expect_value(t, perf.init(dll), perf.Error.None)
			perf.begin_event("Contract reinitialized module")
			perf.end_event()
			testing.expect(t, perf.shutdown())
			testing.expect(t, !perf.is_initialized())
		}
	}
}
when perf.ENABLED {
	expect_load_error :: proc(
		t: ^testing.T,
		path: string,
		expected: raw.Error,
		check_released := false,
	) {
		wide := win.utf8_to_wstring_alloc(path, context.allocator)
		defer delete(wide, context.allocator)
		if !testing.expect(t, wide != nil) {return}
		previous := win.GetModuleHandleW(wide)
		module, err := raw.load_from(cast([^]u16)wide)
		testing.expect_value(t, err, expected)
		testing.expect(
			t,
			module.handle == nil && all_absent(module.functions),
			"Failed loads must return a zero module",
		)
		if check_released && previous == nil {
			testing.expect(
				t,
				win.GetModuleHandleW(wide) == nil,
				"Failed negotiation must release its library handle",
			)
		}
		testing.expect(t, raw.free(&module))
	}
}
