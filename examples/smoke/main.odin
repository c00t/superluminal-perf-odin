package main

import perf "../.."
import raw "../../raw"
import "core:fmt"
import "core:mem"
import "core:os"
import "core:thread"
import "core:time"

functions: raw.Functions

fail :: proc(message: string) -> ! {
	fmt.eprintln(message)
	os.exit(1)
}

work :: proc() {
	// Keep intervals visible without allocating on the event path.
	time.sleep(10 * time.Millisecond)
}

child :: proc(early: bool) {
	perf.scope("Odin Child")
	work()
	if early {return}
	work()
}

frame :: proc(index: int) {
	data := [3]string{"frame=0", "frame=1", "frame=2"}
	perf.begin_event("Odin Frame", data[index], perf.make_color(0x12, 0x34, 0x56))
	defer perf.end_event()
	child(index == 1)
}


worker :: proc() {
	context.allocator = mem.panic_allocator()
	context.temp_allocator = mem.panic_allocator()
	perf.set_current_thread_name("Odin Worker")
	{
		perf.scope("Odin Worker Event")
		work()
	}
	when perf.ENABLED {worker_fibers()}
}

raw_events :: proc() {
	when perf.ENABLED {
		functions.SetCurrentThreadName("Raw Thread")
		name := "Odin Main suffix"
		functions.SetCurrentThreadNameN(raw_data(name), 9)
		functions.BeginEvent("Raw UTF8 Z", "raw", raw.DEFAULT_COLOR)
		work()
		_ = functions.EndEvent()
		id := "Raw UTF8 N suffix"
		data := "raw suffix"
		functions.BeginEventN(raw_data(id), 10, raw_data(data), 3, raw.DEFAULT_COLOR)
		work()
		_ = functions.EndEvent()
		wide_z := [?]u16{'R', 'a', 'w', ' ', 'U', 'T', 'F', '1', '6', ' ', 'Z', 0}
		wide_n := [?]u16{'R', 'a', 'w', ' ', 'U', 'T', 'F', '1', '6', ' ', 'N', ' ', 'x'}
		data_z := [?]u16{'r', 'a', 'w', 0}
		data_n := [?]u16{'r', 'a', 'w', ' ', 'x'}
		functions.BeginEventWide(raw_data(wide_z[:]), raw_data(data_z[:]), raw.DEFAULT_COLOR)
		work()
		_ = functions.EndEvent()
		functions.BeginEventWideN(
			raw_data(wide_n[:]),
			11,
			raw_data(data_n[:]),
			3,
			raw.DEFAULT_COLOR,
		)
		work()
		_ = functions.EndEvent()
	}
}

native_events :: proc() {
	perf.set_current_thread_name("Odin Main")
	for i in 0 ..< 3 {frame(i)}
	id_backing := "Odin UTF8 café suffix"
	data_backing := "café suffix"
	perf.begin_event(id_backing[:len("Odin UTF8 café")], data_backing[:len("café")])
	work()
	perf.end_event()
	wide_id := [?]u16{'O', 'd', 'i', 'n', ' ', 'W', 'i', 'd', 'e'}
	wide_data := [?]u16{'n', 'o', 't', 'e', '=', 0xd83c, 0xdfb5}
	perf.begin_event_wide(wide_id[:], wide_data[:])
	work()
	perf.end_event()
}

main :: proc() {
	dll_path := ""
	capture := false
	mode := ""
	for i := 1; i < len(os.args); i += 1 {
		switch os.args[i] {
		case "--dll":
			i += 1
			if i == len(os.args) {fail("--dll requires an absolute path")}
			if len(os.args[i]) >= 2 && os.args[i][:2] == "--" {
				fail("--dll requires an absolute path")
			}
			dll_path = os.args[i]
		case "--capture":
			capture = true
		case "--overflow-utf8", "--overflow-utf16", "--max-utf8", "--max-utf16", "--uninitialized":
			if mode != "" {fail("Only one negative or boundary mode may be selected")}
			mode = os.args[i]
		case:
			fail("Unknown smoke option")
		}
	}
	if mode == "--uninitialized" {
		perf.begin_event("Uninitialized")
		perf.end_event()
		when perf.ENABLED && perf.LINK_MODE == "dynamic" {
			fail("Uninitialized instrumentation unexpectedly returned")
		}
		fmt.println("Superluminal smoke passed")
		return
	}
	when perf.ENABLED {
		if err := perf.init(dll_path); err != .None {
			fmt.eprintln("Superluminal initialization failed:", err)
			os.exit(1)
		}
	}
	raw_module: raw.Module
	when perf.ENABLED && perf.LINK_MODE == "dynamic" {
		raw_module = load_raw(dll_path)
		functions = raw_module.functions
	} else when perf.ENABLED {
		functions = raw.Functions {
			SetCurrentThreadName  = raw.SetCurrentThreadName,
			SetCurrentThreadNameN = raw.SetCurrentThreadName_N,
			BeginEvent            = raw.BeginEvent,
			BeginEventN           = raw.BeginEvent_N,
			BeginEventWide        = raw.BeginEvent_Wide,
			BeginEventWideN       = raw.BeginEvent_Wide_N,
			EndEvent              = raw.EndEvent,
			RegisterFiber         = raw.RegisterFiber,
			UnregisterFiber       = raw.UnregisterFiber,
			BeginFiberSwitch      = raw.BeginFiberSwitch,
			EndFiberSwitch        = raw.EndFiberSwitch,
		}
	}
	if mode != "" {
		count := 65535
		if mode == "--overflow-utf8" || mode == "--overflow-utf16" {count = 65536}
		if mode == "--overflow-utf8" || mode == "--max-utf8" {
			buffer := make([]u8, count)
			defer delete(buffer)
			for &b in buffer {b = 'x'}
			perf.begin_event(string(buffer))
			perf.end_event()
		} else {
			buffer := make([]u16, count)
			defer delete(buffer)
			for &c in buffer {c = 'x'}
			perf.begin_event_wide(buffer)
			perf.end_event()
		}
		when perf.ENABLED {
			if count == 65536 {fail("Oversized instrumentation unexpectedly returned")}
		}
	} else {
		if capture {time.sleep(time.Second)}
		// Thread setup allocates, but every instrumentation call below forbids Odin allocation.
		t := thread.create_and_start(worker)
		if t == nil {fail("Worker creation failed")}
		{
			context.allocator = mem.panic_allocator()
			context.temp_allocator = mem.panic_allocator()
			raw_events()
			native_events()
		}
		thread.join(t)
		thread.destroy(t)
	}
	if !raw.free(&raw_module) {fail("Raw DLL shutdown failed")}
	if !perf.shutdown() {fail("Superluminal shutdown failed")}
	fmt.println("Superluminal smoke passed")
}
