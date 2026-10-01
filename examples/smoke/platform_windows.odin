package main

import perf "../.."
import raw "../../raw"
import "base:runtime"
import "core:mem"
import win "core:sys/windows"

worker_fibers :: proc() {
	when perf.ENABLED {
		state: Fiber_State
		state.parent = win.ConvertThreadToFiber(nil)
		if state.parent == nil {panic("ConvertThreadToFiber failed")}
		state.child = win.CreateFiber(0, fiber_entry, &state)
		if state.child == nil {panic("CreateFiber failed")}
		parent_id := u64(uintptr(state.parent))
		child_id := u64(uintptr(state.child))
		functions.RegisterFiber(parent_id)
		functions.BeginFiberSwitch(parent_id, child_id)
		win.SwitchToFiber(state.child)
		functions.EndFiberSwitch(parent_id)
		win.DeleteFiber(state.child)
		functions.UnregisterFiber(parent_id)
	}
}

load_raw :: proc(path: string) -> raw.Module {
	if len(path) == 0 {
		module, err := raw.load()
		if err != .None {fail("Raw DLL discovery failed")}
		return module
	}
	wide := win.utf8_to_wstring_alloc(path, context.allocator)
	if wide == nil {fail("DLL path conversion failed")}
	module, err := raw.load_from(cast([^]u16)wide)
	delete(wide, context.allocator)
	if err != .None {fail("Raw DLL initialization failed")}
	return module
}
when perf.ENABLED {
	Fiber_State :: struct {
		parent, child: rawptr,
	}

	fiber_entry :: proc "system" (parameter: rawptr) {
		context = runtime.default_context()
		context.allocator = mem.panic_allocator()
		context.temp_allocator = mem.panic_allocator()
		state := cast(^Fiber_State)parameter
		id := u64(uintptr(state.child))
		perf.register_fiber(id)
		perf.end_fiber_switch(id)
		{
			perf.scope("Odin Fiber Event")
			work()
		}
		perf.unregister_fiber(id)
		perf.begin_fiber_switch(id, u64(uintptr(state.parent)))
		win.SwitchToFiber(state.parent)
		// The parent deletes this suspended fiber; its callback must never return.
		panic("Completed fiber unexpectedly resumed")
	}
}
